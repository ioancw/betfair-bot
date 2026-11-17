"""Tests for the price signal helpers."""

import pytest

from src.signals import (
    PriceWindow,
    backing_pressure,
    best_back,
    best_lay,
    move_ticks,
    nearest_tick,
    spread_ticks,
    ticks_between,
)
from tests.factories import make_runner


class TestLadderMaths:
    def test_nearest_tick_snaps_to_ladder(self):
        assert nearest_tick(5.04) == 5.0
        assert nearest_tick(2.013) == 2.02
        assert nearest_tick(1.004) == 1.01

    def test_ticks_between_crosses_step_boundaries(self):
        # 1.99 -> 2.00 -> 2.02: the step changes from 0.01 to 0.02 at 2.0
        assert ticks_between(1.99, 2.02) == 2
        # 3.95 -> 4.0 -> 4.1 -> 4.2
        assert ticks_between(3.95, 4.2) == 3

    def test_ticks_between_is_signed(self):
        assert ticks_between(4.2, 3.95) == -3
        assert ticks_between(5.0, 5.0) == 0

    def test_move_ticks(self):
        assert move_ticks(4.2, -4) == 3.9
        assert move_ticks(5.7, -3) == 5.4
        assert move_ticks(1.99, 2) == 2.02

    def test_move_ticks_clamps_at_ladder_ends(self):
        assert move_ticks(1.02, -10) == 1.01
        assert move_ticks(990, 10) == 1000


class TestBookReads:
    def test_best_prices(self):
        runner = make_runner(back=((4.9, 10.0), (4.8, 5.0)), lay=((5.1, 10.0),))
        assert best_back(runner) == 4.9
        assert best_lay(runner) == 5.1

    def test_empty_side_gives_none(self):
        runner = make_runner(back=(), lay=())
        assert best_back(runner) is None
        assert best_lay(runner) is None
        assert spread_ticks(runner) is None
        assert backing_pressure(runner) is None

    def test_spread_in_ticks(self):
        assert spread_ticks(make_runner(back=((4.9, 1.0),), lay=((5.0, 1.0),))) == 1
        assert spread_ticks(make_runner(back=((4.8, 1.0),), lay=((5.1, 1.0),))) == 3

    def test_backing_pressure_is_share_of_money_on_lay_side(self):
        # 300 waiting to back (shown on the lay side), 100 waiting to lay
        runner = make_runner(back=((4.9, 100.0),), lay=((5.0, 300.0),))
        assert backing_pressure(runner) == pytest.approx(0.75)

    def test_backing_pressure_only_counts_requested_levels(self):
        runner = make_runner(
            back=((4.9, 100.0), (4.8, 900.0)), lay=((5.0, 100.0), (5.1, 100.0))
        )
        assert backing_pressure(runner, levels=1) == pytest.approx(0.5)
        assert backing_pressure(runner, levels=2) == pytest.approx(200 / 1200)

    def test_object_style_entries_are_supported(self):
        runner = make_runner(back=((4.9, 100.0),), lay=((5.0, 100.0),), as_dicts=False)
        assert best_back(runner) == 4.9
        assert backing_pressure(runner) == pytest.approx(0.5)


class TestPriceWindow:
    def test_no_change_until_window_is_covered(self):
        window = PriceWindow(lookback=100)
        window.add(0, 5.0)
        assert window.change() is None
        window.add(50, 4.5)
        assert window.change() is None  # only half the window has data

    def test_change_is_fractional_move_over_window(self):
        window = PriceWindow(lookback=100)
        window.add(0, 5.0)
        window.add(100, 4.5)
        assert window.change() == pytest.approx(-0.10)

    def test_coverage_threshold_allows_slightly_short_history(self):
        window = PriceWindow(lookback=100, min_coverage=0.8)
        window.add(0, 5.0)
        window.add(80, 5.5)
        assert window.change() == pytest.approx(0.10)

    def test_old_samples_are_discarded_but_anchor_is_kept(self):
        window = PriceWindow(lookback=100)
        for second, price in [(0, 9.0), (50, 5.0), (150, 4.0), (200, 3.0)]:
            window.add(second, price)
        # At t=200 the window starts at t=100; the sample at t=50 is the anchor
        assert len(window) == 3
        assert window.change() == pytest.approx((3.0 - 5.0) / 5.0)

    def test_steady_price_gives_zero(self):
        window = PriceWindow(lookback=10)
        for second in range(30):
            window.add(second, 4.0)
        assert window.change() == 0.0
        assert len(window) <= 12

    def test_lookback_must_be_positive(self):
        with pytest.raises(ValueError):
            PriceWindow(lookback=0)
