"""Price signals computed from live market data. Pure functions, no I/O."""

from __future__ import annotations

from collections import deque
from typing import Any, Optional

from flumine.utils import PRICES_FLOAT, get_nearest_price

from src.ticks import price_size

_TICK_INDEX = {price: index for index, price in enumerate(PRICES_FLOAT)}


def nearest_tick(price: float) -> float:
    """Snap a price onto Betfair's price ladder."""
    return get_nearest_price(price)


def ticks_between(low: float, high: float) -> int:
    """Number of ladder steps from ``low`` up to ``high`` (negative if reversed)."""
    return _TICK_INDEX[nearest_tick(high)] - _TICK_INDEX[nearest_tick(low)]


def move_ticks(price: float, ticks: int) -> float:
    """Price ``ticks`` ladder steps away, clamped to the ends of the ladder."""
    index = _TICK_INDEX[nearest_tick(price)] + ticks
    index = max(0, min(index, len(PRICES_FLOAT) - 1))
    return PRICES_FLOAT[index]


def best_price(entries: Optional[list]) -> Optional[float]:
    """Price at the top of one side of the book, or None if it is empty."""
    if not entries:
        return None
    return price_size(entries[0])[0]


def best_back(runner: Any) -> Optional[float]:
    """Highest price currently available to back."""
    return best_price(getattr(runner.ex, "available_to_back", None))


def best_lay(runner: Any) -> Optional[float]:
    """Lowest price currently available to lay."""
    return best_price(getattr(runner.ex, "available_to_lay", None))


def spread_ticks(runner: Any) -> Optional[int]:
    """Gap between best back and best lay in ladder steps."""
    back, lay = best_back(runner), best_lay(runner)
    if back is None or lay is None:
        return None
    return ticks_between(back, lay)


def backing_pressure(runner: Any, levels: int = 3) -> Optional[float]:
    """Share of visible money that is waiting to back, between 0 and 1.

    Unmatched back bets sit on the lay side of the book (they are what a layer
    can take) and unmatched lay bets sit on the back side. A value above 0.5
    means more money is queued to back than to lay, which tends to push the
    price shorter; below 0.5 suggests it will drift.
    """
    waiting_to_back = sum(
        price_size(e)[1] or 0.0 for e in (runner.ex.available_to_lay or [])[:levels]
    )
    waiting_to_lay = sum(
        price_size(e)[1] or 0.0 for e in (runner.ex.available_to_back or [])[:levels]
    )
    total = waiting_to_back + waiting_to_lay
    if total <= 0:
        return None
    return waiting_to_back / total


class PriceWindow:
    """Rolling history of one runner's price over the last ``lookback`` seconds.

    Args:
        lookback: Length of the window in seconds.
        min_coverage: Fraction of the window that must be covered by data
            before :meth:`change` reports anything.
    """

    def __init__(self, lookback: float, min_coverage: float = 0.8) -> None:
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        self.lookback = lookback
        self.min_coverage = min_coverage
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, timestamp: float, price: float) -> None:
        """Record ``price`` observed at ``timestamp`` (seconds)."""
        self._samples.append((timestamp, price))
        # Keep exactly one sample at or before the window start as the anchor
        cutoff = timestamp - self.lookback
        while len(self._samples) >= 2 and self._samples[1][0] <= cutoff:
            self._samples.popleft()

    def change(self) -> Optional[float]:
        """Fractional price change across the window.

        Negative means the price shortened (a "steamer"), positive that it
        lengthened (a "drifter"). Returns None until enough history exists.
        """
        if len(self._samples) < 2:
            return None
        start_time, start_price = self._samples[0]
        end_time, end_price = self._samples[-1]
        if end_time - start_time < self.lookback * self.min_coverage:
            return None
        if start_price <= 0:
            return None
        return (end_price - start_price) / start_price

    def __len__(self) -> int:
        return len(self._samples)
