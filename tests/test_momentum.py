"""Tests for the momentum strategy: signal logic and simulated trading."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from flumine.order.order import OrderStatus

from src.backtest import run_backtest
from src.signals import PriceWindow
from src.strategies.momentum import MomentumConfig, MomentumStrategy, Position, is_live
from tests.factories import make_runner


def window_with_change(change, lookback=120.0, start_price=5.0):
    window = PriceWindow(lookback)
    window.add(0, start_price)
    window.add(lookback, start_price * (1 + change))
    return window


# Money queued to back outweighs money queued to lay, and vice versa
BACKERS_QUEUEING = dict(back=((4.9, 100.0),), lay=((5.0, 300.0),))
LAYERS_QUEUEING = dict(back=((4.9, 300.0),), lay=((5.0, 100.0),))


class TestConfig:
    def test_defaults_are_valid(self):
        MomentumConfig()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"stake": 0.5},
            {"entry_threshold": 0},
            {"entry_threshold": 1.5},
            {"entry_window_start": 100, "entry_window_end": 200},
            {"entry_window_end": 30, "close_seconds_before_off": 60},
            {"target_ticks": 0},
            {"stop_ticks": 0},
            {"min_price": 10, "max_price": 5},
        ],
    )
    def test_invalid_settings_are_rejected(self, overrides):
        with pytest.raises(ValueError):
            MomentumConfig(**overrides)

    def test_exposure_limits_follow_stake(self):
        strategy = MomentumStrategy(MomentumConfig(stake=2.0, max_price=10.0))
        assert strategy.max_order_exposure == pytest.approx(30.0)
        assert strategy.max_selection_exposure == pytest.approx(60.0)


class TestSignal:
    def setup_method(self):
        self.strategy = MomentumStrategy(MomentumConfig(entry_threshold=0.03))

    def test_steamer_with_backing_pressure_is_backed(self):
        runner = make_runner(**BACKERS_QUEUEING)
        assert self.strategy.signal(runner, window_with_change(-0.05)) == "BACK"

    def test_drifter_with_laying_pressure_is_laid(self):
        runner = make_runner(**LAYERS_QUEUEING)
        assert self.strategy.signal(runner, window_with_change(0.05)) == "LAY"

    def test_move_below_threshold_is_ignored(self):
        runner = make_runner(**BACKERS_QUEUEING)
        assert self.strategy.signal(runner, window_with_change(-0.02)) is None

    def test_steamer_without_backing_pressure_is_ignored(self):
        runner = make_runner(**LAYERS_QUEUEING)
        assert self.strategy.signal(runner, window_with_change(-0.05)) is None

    def test_drifter_without_laying_pressure_is_ignored(self):
        runner = make_runner(**BACKERS_QUEUEING)
        assert self.strategy.signal(runner, window_with_change(0.05)) is None

    def test_no_history_gives_no_signal(self):
        runner = make_runner(**BACKERS_QUEUEING)
        assert self.strategy.signal(runner, None) is None
        assert self.strategy.signal(runner, PriceWindow(120.0)) is None

    def test_wide_spread_is_ignored(self):
        runner = make_runner(back=((4.6, 100.0),), lay=((5.0, 300.0),))
        assert self.strategy.signal(runner, window_with_change(-0.05)) is None

    def test_empty_book_is_ignored(self):
        runner = make_runner(back=(), lay=())
        assert self.strategy.signal(runner, window_with_change(-0.05)) is None

    @pytest.mark.parametrize(
        "back, lay",
        [(((1.3, 100.0),), ((1.31, 300.0),)), (((20.0, 100.0),), ((21.0, 300.0),))],
    )
    def test_prices_outside_range_are_ignored(self, back, lay):
        runner = make_runner(back=back, lay=lay)
        assert self.strategy.signal(runner, window_with_change(-0.05)) is None

    def test_directions_can_be_switched_off(self):
        steamers_off = MomentumStrategy(MomentumConfig(trade_steamers=False))
        drifters_off = MomentumStrategy(MomentumConfig(trade_drifters=False))
        steamer = make_runner(**BACKERS_QUEUEING)
        drifter = make_runner(**LAYERS_QUEUEING)

        assert steamers_off.signal(steamer, window_with_change(-0.05)) is None
        assert drifters_off.signal(drifter, window_with_change(0.05)) is None


def fake_order(price, matched=0.0, remaining=0.0, status=OrderStatus.EXECUTION_COMPLETE):
    return SimpleNamespace(
        status=status,
        size_matched=matched,
        size_remaining=remaining,
        average_price_matched=price if matched else 0.0,
        order_type=SimpleNamespace(price=price),
    )


class TestPosition:
    def test_live_statuses(self):
        assert is_live(fake_order(5.0, status=OrderStatus.PENDING))
        assert is_live(fake_order(5.0, status=OrderStatus.EXECUTABLE))
        assert is_live(fake_order(5.0, status=OrderStatus.CANCELLING))
        assert not is_live(fake_order(5.0, status=OrderStatus.EXECUTION_COMPLETE))
        assert not is_live(fake_order(5.0, status=OrderStatus.VIOLATION))

    def test_exit_side_is_opposite_of_entry(self):
        entry = fake_order(5.0, matched=2.0)
        assert Position(101, "BACK", None, entry, 0.0).exit_side == "LAY"
        assert Position(101, "LAY", None, entry, 0.0).exit_side == "BACK"

    def test_unmatched_entry_has_nothing_to_hedge(self):
        entry = fake_order(5.0, matched=0.0, remaining=2.0, status=OrderStatus.EXECUTABLE)
        assert Position(101, "BACK", None, entry, 0.0).unhedged_payout() == 0.0

    def test_matched_entry_needs_full_hedge(self):
        position = Position(101, "BACK", None, fake_order(5.0, matched=2.0), 0.0)
        assert position.unhedged_payout() == pytest.approx(10.0)

    def test_resting_exit_counts_as_cover(self):
        position = Position(101, "BACK", None, fake_order(5.0, matched=2.0), 0.0)
        position.exits.append(fake_order(4.8, remaining=10.0 / 4.8, status=OrderStatus.EXECUTABLE))
        assert position.unhedged_payout() == pytest.approx(0.0)

    def test_cancelled_exit_no_longer_counts(self):
        position = Position(101, "BACK", None, fake_order(5.0, matched=2.0), 0.0)
        position.exits.append(fake_order(4.8, remaining=0.0))
        assert position.unhedged_payout() == pytest.approx(10.0)

    def test_partly_matched_exit(self):
        position = Position(101, "BACK", None, fake_order(5.0, matched=2.0), 0.0)
        position.exits.append(fake_order(4.0, matched=1.0, remaining=0.0))
        assert position.unhedged_payout() == pytest.approx(6.0)


class TestSimulatedTrading:
    """Runs the strategy through flumine's simulator on synthetic recordings."""

    def test_steamer_and_drifter_are_traded_for_a_profit(self, sample_race):
        result = run_backtest([sample_race])

        by_runner = {t.selection_id: t for t in result.trades}
        assert set(by_runner) == {102, 103}
        assert by_runner[102].side == "BACK"  # the steamer
        assert by_runner[103].side == "LAY"  # the drifter
        assert all(t.profit > 0 for t in result.trades)
        assert result.unhedged_trades == 0

    def test_profit_is_locked_in_regardless_of_winner(self, tmp_path):
        """A closed trade pays the same whichever horse wins."""
        from src.sample_data import generate_race

        profits = []
        for winner in (101, 102, 103):
            path = generate_race(tmp_path / str(winner) / "1.200000001", winner=winner)
            result = run_backtest([str(path)])
            profits.append(sorted((t.selection_id, t.profit) for t in result.trades))

        for other in profits[1:]:
            for (_, a), (_, b) in zip(profits[0], other, strict=True):
                assert a == pytest.approx(b, abs=0.03)

    def test_reversal_triggers_stop_loss(self, reversal_race):
        result = run_backtest([reversal_race])

        assert len(result.trades) == 2
        assert all(t.profit < 0 for t in result.trades)
        assert result.unhedged_trades == 0
        # Losses are bounded: a 4-tick stop on a 2.00 stake costs well under 1.00 each
        assert all(t.profit > -1.0 for t in result.trades)

    def test_positions_are_closed_before_the_off(self, sample_race):
        """With an unreachable target the time stop must still flatten everything."""
        config = MomentumConfig(target_ticks=60, stop_ticks=60)
        result = run_backtest([sample_race], config)

        assert len(result.trades) == 2
        assert result.unhedged_trades == 0

    def test_one_entry_per_runner(self, sample_race):
        result = run_backtest([sample_race])
        selections = [t.selection_id for t in result.trades]
        assert len(selections) == len(set(selections))

    def test_more_entries_can_be_allowed(self, sample_race):
        config = MomentumConfig(max_trades_per_runner=3, target_ticks=1)
        result = run_backtest([sample_race], config)
        assert len(result.trades) > 2

    def test_steamers_only(self, sample_race):
        result = run_backtest([sample_race], MomentumConfig(trade_drifters=False))
        assert [t.side for t in result.trades] == ["BACK"]

    def test_thin_markets_are_skipped(self, sample_race):
        result = run_backtest([sample_race], MomentumConfig(min_market_matched=10_000_000))
        assert result.trades == []

    def test_high_threshold_means_no_trades(self, sample_race):
        result = run_backtest([sample_race], MomentumConfig(entry_threshold=0.5))
        assert result.trades == []

    def test_max_open_positions_limits_concurrency(self, sample_race):
        config = replace(MomentumConfig(), max_open_positions=1, target_ticks=60, stop_ticks=60)
        result = run_backtest([sample_race], config)
        assert len(result.trades) == 1

    def test_state_is_cleared_when_market_closes(self, sample_race):
        strategy = MomentumStrategy()
        strategy._windows[("1.200000001", 101)] = PriceWindow(10)
        strategy._entries[("1.200000001", 101)] = 1
        strategy._windows[("1.999", 5)] = PriceWindow(10)

        strategy.forget_market("1.200000001")

        assert list(strategy._windows) == [("1.999", 5)]
        assert strategy._entries == {}
