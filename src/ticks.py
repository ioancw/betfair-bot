"""Convert flumine market books into flat rows ("ticks") for storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

LADDER_LEVELS = 3

TICK_COLUMNS = (
    "time",
    "market_id",
    "selection_id",
    "ltp",
    "runner_total_matched",
    "back_price_1", "back_size_1",
    "back_price_2", "back_size_2",
    "back_price_3", "back_size_3",
    "lay_price_1", "lay_size_1",
    "lay_price_2", "lay_size_2",
    "lay_price_3", "lay_size_3",
    "market_total_matched",
    "in_play",
    "market_status",
    "runner_status",
)

# One (price, size) pair per ladder level; (None, None) where the level is empty
Ladder = tuple[tuple[Optional[float], Optional[float]], ...]


@dataclass(frozen=True)
class Tick:
    """Snapshot of one runner's prices at one moment."""

    time: datetime
    market_id: str
    selection_id: int
    ltp: Optional[float]
    runner_total_matched: Optional[float]
    back: Ladder
    lay: Ladder
    market_total_matched: Optional[float]
    in_play: bool
    market_status: Optional[str]
    runner_status: Optional[str]

    def as_row(self) -> tuple:
        """Values in the order of ``TICK_COLUMNS``."""
        ladder = [value for level in self.back for value in level]
        ladder += [value for level in self.lay for value in level]
        return (
            self.time,
            self.market_id,
            self.selection_id,
            self.ltp,
            self.runner_total_matched,
            *ladder,
            self.market_total_matched,
            self.in_play,
            self.market_status,
            self.runner_status,
        )


@dataclass(frozen=True)
class MarketInfo:
    market_id: str
    event_name: Optional[str]
    venue: Optional[str]
    country_code: Optional[str]
    market_name: Optional[str]
    market_type: Optional[str]
    start_time: Optional[datetime]


@dataclass(frozen=True)
class RunnerInfo:
    market_id: str
    selection_id: int
    runner_name: Optional[str]
    sort_priority: Optional[int]


def price_size(entry: Any) -> tuple[Optional[float], Optional[float]]:
    """Read (price, size) from a ladder entry, whether a dict or an object."""
    if isinstance(entry, dict):
        return entry.get("price"), entry.get("size")
    return getattr(entry, "price", None), getattr(entry, "size", None)


def ladder(entries: Optional[list], levels: int = LADDER_LEVELS) -> Ladder:
    """Best ``levels`` entries of one side of the book, padded with (None, None)."""
    entries = entries or []
    out = [price_size(entry) for entry in entries[:levels]]
    out += [(None, None)] * (levels - len(out))
    return tuple(out)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Betfair timestamps are UTC; attach the timezone if it is missing."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def changed_selection_ids(market_book: Any) -> Optional[set[int]]:
    """Selection ids touched by the latest stream update.

    Returns ``None`` when every runner should be treated as changed: there is
    no stream update to inspect, it is a full image, or the market definition
    (status, in-play flag, non-runners) changed.
    """
    update = getattr(market_book, "streaming_update", None)
    if not isinstance(update, dict):
        return None
    if update.get("img") or "marketDefinition" in update:
        return None
    return {rc["id"] for rc in update.get("rc", []) if "id" in rc}


def ticks_from_market_book(market_book: Any, only_changed: bool = True) -> list[Tick]:
    """Build one :class:`Tick` per runner from a market book.

    With ``only_changed`` (the default) runners whose prices did not move in
    the latest stream update are skipped, which keeps storage proportional to
    market activity instead of to the number of updates.
    """
    changed = changed_selection_ids(market_book) if only_changed else None
    time = as_utc(market_book.publish_time)
    ticks = []
    for runner in market_book.runners:
        if changed is not None and runner.selection_id not in changed:
            continue
        ex = getattr(runner, "ex", None)
        ticks.append(
            Tick(
                time=time,
                market_id=market_book.market_id,
                selection_id=runner.selection_id,
                ltp=runner.last_price_traded,
                runner_total_matched=runner.total_matched,
                back=ladder(getattr(ex, "available_to_back", None)),
                lay=ladder(getattr(ex, "available_to_lay", None)),
                market_total_matched=market_book.total_matched,
                in_play=bool(market_book.inplay),
                market_status=market_book.status,
                runner_status=runner.status,
            )
        )
    return ticks


def market_info(market: Any) -> MarketInfo:
    """Describe a flumine ``Market`` (uses the catalogue when it has arrived)."""
    return MarketInfo(
        market_id=market.market_id,
        event_name=market.event_name,
        venue=market.venue,
        country_code=market.country_code,
        market_name=market.market_name,
        market_type=market.market_type,
        start_time=as_utc(market.market_start_datetime),
    )


def runner_infos(market: Any, market_book: Any = None) -> list[RunnerInfo]:
    """Runner names from the catalogue, falling back to the market definition.

    The live stream's market definition carries no runner names, so names are
    only filled in once flumine has polled the market catalogue.
    """
    catalogue = getattr(market, "market_catalogue", None)
    if catalogue is not None and getattr(catalogue, "runners", None):
        return [
            RunnerInfo(market.market_id, r.selection_id, r.runner_name, r.sort_priority)
            for r in catalogue.runners
        ]
    market_book = market_book or getattr(market, "market_book", None)
    definition = getattr(market_book, "market_definition", None)
    if definition is None:
        return []
    return [
        RunnerInfo(
            market.market_id,
            r.selection_id,
            getattr(r, "name", None),
            getattr(r, "sort_priority", None),
        )
        for r in definition.runners
    ]
