"""Replay recorded races through a strategy using flumine's simulator."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from flumine import FlumineSimulation, clients
from flumine.streams.betfairhistoricalstream import BetfairHistoricalStream
from loguru import logger

from src.strategies.momentum import MomentumConfig, MomentumStrategy

DEFAULT_COMMISSION = 0.05


@dataclass(frozen=True)
class TradeResult:
    """One opening bet together with the bets that closed it."""

    selection_id: int
    side: str
    entry_price: float
    size_matched: float
    profit: float
    orders: int
    # Opening exposure (size x price) the closing bets failed to cover
    open_exposure: float = 0.0


@dataclass
class MarketResult:
    market_id: str
    venue: Optional[str]
    start_time: Any
    trades: list[TradeResult] = field(default_factory=list)
    orders: int = 0
    gross_profit: float = 0.0
    commission: float = 0.0

    @property
    def net_profit(self) -> float:
        return self.gross_profit - self.commission


@dataclass
class BacktestResult:
    markets: list[MarketResult]
    commission_rate: float

    @property
    def trades(self) -> list[TradeResult]:
        return [trade for market in self.markets for trade in market.trades]

    @property
    def gross_profit(self) -> float:
        return sum(m.gross_profit for m in self.markets)

    @property
    def commission(self) -> float:
        return sum(m.commission for m in self.markets)

    @property
    def net_profit(self) -> float:
        return sum(m.net_profit for m in self.markets)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.profit > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t.profit < 0)

    @property
    def unhedged_trades(self) -> int:
        """Trades left exposed to the race result (more than a minimum bet open)."""
        return sum(1 for t in self.trades if t.open_exposure / t.entry_price >= 1.0)

    def summary(self) -> str:
        """Human-readable report."""
        lines = [
            f"{'Market':<14} {'Venue':<14} {'Trades':>6} {'Orders':>6} "
            f"{'Gross':>9} {'Comm':>7} {'Net':>9}",
            "-" * 70,
        ]
        for m in self.markets:
            lines.append(
                f"{m.market_id:<14} {(m.venue or '-')[:14]:<14} {len(m.trades):>6} "
                f"{m.orders:>6} {m.gross_profit:>9.2f} {m.commission:>7.2f} {m.net_profit:>9.2f}"
            )
        lines.append("-" * 70)
        lines.append(
            f"{len(self.markets)} markets, {len(self.trades)} trades "
            f"({self.winning_trades} won, {self.losing_trades} lost, "
            f"{self.unhedged_trades} not fully closed)"
        )
        lines.append(
            f"Gross {self.gross_profit:.2f}  commission {self.commission:.2f} "
            f"({self.commission_rate:.0%})  net {self.net_profit:.2f}"
        )
        return "\n".join(lines)


def find_recordings(paths: Iterable[str | Path]) -> list[str]:
    """Expand files and directories into a sorted list of recording files."""
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file() and not p.name.startswith("."))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"No recording at {path}")
    return sorted(str(p) for p in files)


def market_result(market: Any, strategy: Any, commission_rate: float) -> MarketResult:
    """Summarise a strategy's settled orders in one simulated market."""
    result = MarketResult(
        market_id=market.market_id,
        venue=market.venue,
        start_time=market.market_start_datetime,
    )
    by_trade: dict[Any, list] = defaultdict(list)
    for order in market.blotter.strategy_orders(strategy):
        result.orders += 1
        if order.size_matched > 0:
            by_trade[order.trade.id].append(order)

    for orders in by_trade.values():
        entry = min(orders, key=lambda o: o.date_time_created)
        profit = round(sum(o.profit for o in orders), 2)
        signed = sum(
            (1 if o.side == entry.side else -1) * o.size_matched * o.average_price_matched
            for o in orders
        )
        result.trades.append(
            TradeResult(
                selection_id=entry.selection_id,
                side=entry.side,
                entry_price=entry.average_price_matched,
                size_matched=entry.size_matched,
                profit=profit,
                orders=len(orders),
                open_exposure=round(abs(signed), 2),
            )
        )
        result.gross_profit += profit

    result.gross_profit = round(result.gross_profit, 2)
    # Betfair charges commission on net winnings per market, nothing on a loss
    result.commission = round(max(result.gross_profit, 0.0) * commission_rate, 2)
    return result


def run_backtest(
    files: Sequence[str],
    config: Optional[MomentumConfig] = None,
    commission_rate: float = DEFAULT_COMMISSION,
) -> BacktestResult:
    """Simulate :class:`MomentumStrategy` over recorded races.

    Args:
        files: Recording files (Betfair historical data or our own recordings).
        config: Strategy parameters; defaults are used when omitted.
        commission_rate: Fraction of each market's net winnings paid to Betfair.

    Returns:
        Per-market and overall results.
    """
    if not files:
        raise ValueError("No recordings to backtest")
    config = config or MomentumConfig()

    # Only the pre-race period the strategy can act in needs to be replayed
    replay_seconds = config.entry_window_start + config.lookback_seconds + 60
    framework = FlumineSimulation(client=clients.SimulatedClient())
    strategy = MomentumStrategy(
        config=config,
        streams=[
            BetfairHistoricalStream(
                file_path=path,
                listener_kwargs={"inplay": False, "seconds_to_start": replay_seconds},
            )
            for path in files
        ],
    )
    framework.add_strategy(strategy)
    logger.info("Backtesting {} recordings...", len(files))
    framework.run()

    markets = [
        market_result(market, strategy, commission_rate) for market in framework.markets
    ]
    markets.sort(key=lambda m: (str(m.start_time), m.market_id))
    return BacktestResult(markets=markets, commission_rate=commission_rate)
