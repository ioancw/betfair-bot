"""Tests for the backtest runner and its report."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.backtest import (
    BacktestResult,
    MarketResult,
    TradeResult,
    find_recordings,
    market_result,
    run_backtest,
)


def order(trade_id, side, price, size, profit, created):
    return SimpleNamespace(
        trade=SimpleNamespace(id=trade_id),
        selection_id=101,
        side=side,
        size_matched=size,
        average_price_matched=price,
        profit=profit,
        date_time_created=datetime(2026, 1, 1, 14, 0, created),
    )


def market_with(orders):
    return SimpleNamespace(
        market_id="1.100",
        venue="Ascot",
        market_start_datetime=datetime(2026, 1, 1, 14, 30),
        blotter=SimpleNamespace(strategy_orders=lambda strategy: orders),
    )


class TestMarketResult:
    def test_orders_are_grouped_into_trades(self):
        orders = [
            order("t1", "BACK", 5.0, 2.0, 8.0, created=1),
            order("t1", "LAY", 4.0, 2.5, -7.5, created=2),
            order("t2", "LAY", 3.0, 2.0, 2.0, created=3),
            order("t2", "BACK", 3.5, 1.71, -1.71, created=4),
        ]
        result = market_result(market_with(orders), None, commission_rate=0.05)

        assert result.orders == 4
        assert [(t.side, t.entry_price, t.profit, t.orders) for t in result.trades] == [
            ("BACK", 5.0, 0.5, 2),
            ("LAY", 3.0, 0.29, 2),
        ]
        assert result.gross_profit == pytest.approx(0.79)

    def test_commission_is_charged_on_net_market_winnings(self):
        orders = [order("t1", "BACK", 5.0, 2.0, 10.0, created=1)]
        result = market_result(market_with(orders), None, commission_rate=0.05)

        assert result.commission == pytest.approx(0.5)
        assert result.net_profit == pytest.approx(9.5)

    def test_no_commission_on_a_losing_market(self):
        orders = [order("t1", "BACK", 5.0, 2.0, -2.0, created=1)]
        result = market_result(market_with(orders), None, commission_rate=0.05)

        assert result.commission == 0.0
        assert result.net_profit == pytest.approx(-2.0)

    def test_unmatched_orders_are_counted_but_form_no_trade(self):
        orders = [order("t1", "BACK", 0.0, 0.0, 0.0, created=1)]
        result = market_result(market_with(orders), None, commission_rate=0.05)

        assert result.orders == 1
        assert result.trades == []

    def test_open_exposure_measures_unhedged_part(self):
        hedged = [
            order("t1", "BACK", 5.0, 2.0, 0.0, created=1),
            order("t1", "LAY", 4.0, 2.5, 0.0, created=2),
        ]
        unhedged = [order("t2", "BACK", 5.0, 2.0, 0.0, created=1)]

        assert market_result(market_with(hedged), None, 0.05).trades[0].open_exposure == 0.0
        assert market_result(market_with(unhedged), None, 0.05).trades[0].open_exposure == 10.0


class TestBacktestResult:
    def make(self):
        won = TradeResult(101, "BACK", 5.0, 2.0, 0.5, 2)
        lost = TradeResult(102, "LAY", 3.0, 2.0, -0.3, 2)
        exposed = TradeResult(103, "BACK", 4.0, 2.0, -2.0, 1, open_exposure=8.0)
        first = MarketResult("1.1", "Ascot", None, [won, lost], 4, 0.2, 0.01)
        second = MarketResult("1.2", "York", None, [exposed], 1, -2.0, 0.0)
        return BacktestResult([first, second], commission_rate=0.05)

    def test_totals(self):
        result = self.make()
        assert result.gross_profit == pytest.approx(-1.8)
        assert result.commission == pytest.approx(0.01)
        assert result.net_profit == pytest.approx(-1.81)
        assert (result.winning_trades, result.losing_trades) == (1, 2)
        assert result.unhedged_trades == 1

    def test_summary_lists_markets_and_totals(self):
        summary = self.make().summary()
        assert "1.1" in summary and "Ascot" in summary
        assert "1.2" in summary and "York" in summary
        assert "2 markets, 3 trades (1 won, 2 lost, 1 not fully closed)" in summary
        assert "net -1.81" in summary


class TestFindRecordings:
    def test_directories_are_searched_recursively(self, tmp_path):
        (tmp_path / "day1").mkdir()
        (tmp_path / "day1" / "1.200").write_text("")
        (tmp_path / "1.100.gz").write_text("")
        (tmp_path / ".hidden").write_text("")

        assert find_recordings([tmp_path]) == sorted(
            [str(tmp_path / "1.100.gz"), str(tmp_path / "day1" / "1.200")]
        )

    def test_single_files_are_accepted(self, tmp_path):
        path = tmp_path / "1.100"
        path.write_text("")
        assert find_recordings([path]) == [str(path)]

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            find_recordings([tmp_path / "nope"])


class TestRunBacktest:
    def test_no_files_is_an_error(self):
        with pytest.raises(ValueError, match="No recordings"):
            run_backtest([])

    def test_multiple_markets_are_reported_separately(self, sample_race, reversal_race):
        result = run_backtest([sample_race, reversal_race])

        assert [m.market_id for m in result.markets] == ["1.200000001", "1.200000009"]
        winner, loser = result.markets
        assert winner.venue == "Sampleton"
        assert winner.gross_profit > 0 and winner.commission > 0
        assert loser.gross_profit < 0 and loser.commission == 0
        assert result.net_profit == pytest.approx(winner.net_profit + loser.net_profit)
