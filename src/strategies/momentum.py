"""Pre-race momentum trading: join steamers and drifters, then close out.

A *steamer* is a horse whose price is shortening; the strategy backs it and
aims to lay it back at a lower price. A *drifter* is lengthening; the strategy
lays it and aims to back it at a higher price. Every position is closed before
the race starts, so the result of the race should not matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from flumine import BaseStrategy
from flumine.order.order import OrderStatus
from flumine.order.ordertype import LimitOrder
from flumine.order.trade import Trade
from loguru import logger

from src.signals import (
    PriceWindow,
    backing_pressure,
    best_back,
    best_lay,
    move_ticks,
    nearest_tick,
    spread_ticks,
)

LIVE_STATUSES = frozenset(
    {
        OrderStatus.PENDING,
        OrderStatus.EXECUTABLE,
        OrderStatus.CANCELLING,
        OrderStatus.UPDATING,
        OrderStatus.REPLACING,
    }
)


@dataclass(frozen=True)
class MomentumConfig:
    """Tunable parameters of :class:`MomentumStrategy`.

    Attributes:
        stake: Backer's stake of the opening bet, in account currency.
        lookback_seconds: Window over which the price move is measured.
        entry_threshold: Minimum fractional move over the window (0.03 = 3%).
        min_backing_pressure: For steamers, the share of queued money waiting
            to back must be at least this; for drifters, at most ``1 - this``.
        max_spread_ticks: Skip runners whose back/lay gap is wider than this.
        min_price / max_price: Only trade runners priced inside this range.
        min_market_matched: Skip markets with less money matched than this.
        entry_window_start / entry_window_end: Only open positions between
            these many seconds before the scheduled start.
        target_ticks: Take profit this many ladder steps from the entry price.
        stop_ticks: Cut the loss once the price is this many steps against us.
        entry_timeout_seconds: Cancel an opening bet not matched in this time.
        close_seconds_before_off: Close everything this long before the start.
        max_trades_per_runner: Opening bets allowed per runner per race.
        max_open_positions: Positions allowed at once in one market.
        trade_steamers / trade_drifters: Which directions to trade.
        min_order_size: Smallest order the exchange accepts.
        market_types / country_codes: Only trade these markets. The live
            stream is already filtered, but bought historical data can
            include place markets and other countries. An empty tuple
            disables the check.
    """

    stake: float = 2.0
    lookback_seconds: float = 120.0
    entry_threshold: float = 0.03
    min_backing_pressure: float = 0.5
    max_spread_ticks: int = 2
    min_price: float = 1.5
    max_price: float = 12.0
    min_market_matched: float = 10_000.0
    entry_window_start: float = 600.0
    entry_window_end: float = 120.0
    target_ticks: int = 3
    stop_ticks: int = 4
    entry_timeout_seconds: float = 20.0
    close_seconds_before_off: float = 60.0
    max_trades_per_runner: int = 1
    max_open_positions: int = 2
    trade_steamers: bool = True
    trade_drifters: bool = True
    min_order_size: float = 1.0
    market_types: tuple[str, ...] = ("WIN",)
    country_codes: tuple[str, ...] = ("GB",)

    def __post_init__(self) -> None:
        if self.stake < self.min_order_size:
            raise ValueError("stake must be at least min_order_size")
        if not 0 < self.entry_threshold < 1:
            raise ValueError("entry_threshold must be between 0 and 1")
        if self.entry_window_start <= self.entry_window_end:
            raise ValueError("entry_window_start must be earlier than entry_window_end")
        if self.entry_window_end < self.close_seconds_before_off:
            raise ValueError("entries must stop before positions are closed")
        if self.target_ticks < 1 or self.stop_ticks < 1:
            raise ValueError("target_ticks and stop_ticks must be at least 1")
        if self.min_price >= self.max_price:
            raise ValueError("min_price must be below max_price")

    @property
    def worst_case_order_exposure(self) -> float:
        """Largest liability a single order can carry under this config."""
        return self.stake * self.max_price * 1.5


@dataclass
class Position:
    """An opening bet and the bets that close it."""

    selection_id: int
    side: str  # side of the opening bet: "BACK" (steamer) or "LAY" (drifter)
    trade: Trade
    entry: Any
    opened_at: float
    exits: list = field(default_factory=list)
    closing: bool = False

    @property
    def exit_side(self) -> str:
        return "LAY" if self.side == "BACK" else "BACK"

    @property
    def entry_price(self) -> float:
        return self.entry.average_price_matched or self.entry.order_type.price

    def unhedged_payout(self) -> float:
        """Matched opening exposure not yet covered by closing bets.

        Measured as size x price so that it is comparable across prices:
        closing ``payout / price`` at any price levels the outcome.
        """
        opened = self.entry.size_matched * (self.entry.average_price_matched or 0.0)
        covered = 0.0
        for order in self.exits:
            covered += order.size_matched * (order.average_price_matched or 0.0)
            if is_live(order):
                covered += order.size_remaining * order.order_type.price
        return opened - covered


def is_live(order: Any) -> bool:
    """True while an order may still be matched."""
    return order.status in LIVE_STATUSES


class MomentumStrategy(BaseStrategy):
    """Trades pre-race price momentum on a market's active runners."""

    def __init__(self, config: Optional[MomentumConfig] = None, **kwargs: Any) -> None:
        self.cfg = config or MomentumConfig()
        kwargs.setdefault("max_order_exposure", self.cfg.worst_case_order_exposure)
        kwargs.setdefault("max_selection_exposure", self.cfg.worst_case_order_exposure * 2)
        kwargs.setdefault("max_live_trade_count", 2)
        kwargs.setdefault("multi_order_trades", True)
        super().__init__(**kwargs)
        self._windows: dict[tuple[str, int], PriceWindow] = {}
        self._positions: dict[tuple[str, int], Position] = {}
        self._entries: dict[tuple[str, int], int] = {}

    # ------------------------------------------------------------------
    # flumine hooks
    # ------------------------------------------------------------------

    def check_market_book(self, market: Any, market_book: Any) -> bool:
        if market_book.status != "OPEN" or market_book.inplay:
            return False
        definition = market_book.market_definition
        if self.cfg.market_types and definition.market_type not in self.cfg.market_types:
            return False
        if self.cfg.country_codes and definition.country_code not in self.cfg.country_codes:
            return False
        return True

    def process_market_book(self, market: Any, market_book: Any) -> None:
        now = market_book.publish_time_epoch / 1000.0
        seconds_to_start = market.seconds_to_start

        for runner in market_book.runners:
            key = (market_book.market_id, runner.selection_id)
            position = self._positions.get(key)

            if runner.status != "ACTIVE":
                if position is not None:
                    # A removed runner's bets are voided by the exchange
                    del self._positions[key]
                continue

            if runner.last_price_traded:
                window = self._windows.get(key)
                if window is None:
                    window = self._windows[key] = PriceWindow(self.cfg.lookback_seconds)
                window.add(now, runner.last_price_traded)

            if position is not None:
                self._manage(market, runner, position, now, seconds_to_start)
            elif self._can_open(market_book, key, seconds_to_start):
                side = self.signal(runner, self._windows.get(key))
                if side is not None:
                    self._open(market, runner, side, now)

    def process_closed_market(self, market: Any, market_book: Any) -> None:
        self.forget_market(market.market_id)

    def remove_market(self, market_id: str) -> None:
        super().remove_market(market_id)
        self.forget_market(market_id)

    def forget_market(self, market_id: str) -> None:
        for store in (self._windows, self._positions, self._entries):
            for key in [k for k in store if k[0] == market_id]:
                del store[key]

    # ------------------------------------------------------------------
    # Signal
    # ------------------------------------------------------------------

    def signal(self, runner: Any, window: Optional[PriceWindow]) -> Optional[str]:
        """Return "BACK" for a steamer, "LAY" for a drifter, or None."""
        cfg = self.cfg
        if window is None:
            return None
        change = window.change()
        if change is None or abs(change) < cfg.entry_threshold:
            return None

        back, lay = best_back(runner), best_lay(runner)
        if back is None or lay is None:
            return None
        if not (cfg.min_price <= back and lay <= cfg.max_price):
            return None
        spread = spread_ticks(runner)
        if spread is None or spread > cfg.max_spread_ticks:
            return None
        pressure = backing_pressure(runner)
        if pressure is None:
            return None

        if change < 0 and cfg.trade_steamers and pressure >= cfg.min_backing_pressure:
            return "BACK"
        if change > 0 and cfg.trade_drifters and pressure <= 1 - cfg.min_backing_pressure:
            return "LAY"
        return None

    def _can_open(
        self, market_book: Any, key: tuple[str, int], seconds_to_start: float
    ) -> bool:
        cfg = self.cfg
        if not (cfg.entry_window_end <= seconds_to_start <= cfg.entry_window_start):
            return False
        if (market_book.total_matched or 0.0) < cfg.min_market_matched:
            return False
        if self._entries.get(key, 0) >= cfg.max_trades_per_runner:
            return False
        open_here = sum(1 for k in self._positions if k[0] == key[0])
        return open_here < cfg.max_open_positions

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def _open(self, market: Any, runner: Any, side: str, now: float) -> None:
        """Place the opening bet at the price that matches immediately."""
        price = best_back(runner) if side == "BACK" else best_lay(runner)
        trade = Trade(
            market_id=market.market_id,
            selection_id=runner.selection_id,
            handicap=runner.handicap,
            strategy=self,
        )
        order = trade.create_order(
            side=side,
            order_type=LimitOrder(price=price, size=self.cfg.stake, persistence_type="LAPSE"),
        )
        if not market.place_order(order):
            logger.warning(
                "Opening {} on {}/{} rejected: {}",
                side, market.market_id, runner.selection_id, order.violation_msg,
            )
            return
        key = (market.market_id, runner.selection_id)
        self._positions[key] = Position(runner.selection_id, side, trade, order, now)
        self._entries[key] = self._entries.get(key, 0) + 1
        logger.info(
            "OPEN {} {}/{} {:.2f} @ {} ({})",
            side, market.market_id, runner.selection_id, self.cfg.stake, price,
            "steamer" if side == "BACK" else "drifter",
        )

    def _manage(
        self,
        market: Any,
        runner: Any,
        position: Position,
        now: float,
        seconds_to_start: float,
    ) -> None:
        """Advance one open position: cancel, take profit, stop out, or finish."""
        cfg = self.cfg
        entry = position.entry
        # Price at which the closing bet would match straight away
        take = best_lay(runner) if position.exit_side == "LAY" else best_back(runner)

        out_of_time = seconds_to_start <= cfg.close_seconds_before_off
        timed_out = now - position.opened_at >= cfg.entry_timeout_seconds
        if entry.status == OrderStatus.EXECUTABLE and (out_of_time or timed_out):
            market.cancel_order(entry)

        if not position.closing:
            if out_of_time:
                position.closing = True
                logger.info("CLOSE {}/{}: race about to start", market.market_id, runner.selection_id)
            elif entry.size_matched > 0 and take is not None and self._stopped(position, take):
                position.closing = True
                logger.info(
                    "STOP {}/{}: entered at {}, now {}",
                    market.market_id, runner.selection_id, position.entry_price, take,
                )

        if position.closing and take is not None:
            # Chase the market: resting closing bets at stale prices are pulled
            for order in position.exits:
                if order.status == OrderStatus.EXECUTABLE and order.order_type.price != take:
                    market.cancel_order(order)

        if entry.size_matched > 0:
            price = take if position.closing else self._target_price(position)
            if price is not None:
                size = round(position.unhedged_payout() / price, 2)
                if size >= cfg.min_order_size:
                    self._close(market, position, price, size)

        if not is_live(entry) and not any(is_live(o) for o in position.exits):
            reference = take or position.entry_price
            if position.unhedged_payout() / reference < cfg.min_order_size:
                del self._positions[(market.market_id, runner.selection_id)]
                logger.info("DONE {}/{}", market.market_id, runner.selection_id)

    def _target_price(self, position: Position) -> float:
        direction = -1 if position.side == "BACK" else 1
        return move_ticks(position.entry_price, direction * self.cfg.target_ticks)

    def _stopped(self, position: Position, take: float) -> bool:
        if position.side == "BACK":
            return take >= move_ticks(position.entry_price, self.cfg.stop_ticks)
        return take <= move_ticks(position.entry_price, -self.cfg.stop_ticks)

    def _close(self, market: Any, position: Position, price: float, size: float) -> None:
        order = position.trade.create_order(
            side=position.exit_side,
            order_type=LimitOrder(
                price=nearest_tick(price), size=size, persistence_type="LAPSE"
            ),
        )
        if market.place_order(order):
            position.exits.append(order)
            logger.info(
                "EXIT {} {}/{} {:.2f} @ {}{}",
                position.exit_side, market.market_id, position.selection_id, size, price,
                " (closing at market)" if position.closing else "",
            )
        else:
            logger.warning(
                "Closing {} on {}/{} rejected: {}",
                position.exit_side, market.market_id, position.selection_id,
                order.violation_msg,
            )
