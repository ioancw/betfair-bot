"""Generate a synthetic race recording in Betfair's stream file format.

The prices are invented: one runner is made to steam, one to drift and the
rest wander. This exists so the backtester and strategy can be exercised
without a Betfair account. Results on this data say nothing about whether a
strategy makes money on real markets.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.signals import move_ticks, nearest_tick

RUNNERS = (
    # selection id, name, opening price, price at the off
    (101, "Steady Favourite", 2.8, 2.8),
    (102, "Steamer", 6.0, 4.6),
    (103, "Drifter", 4.0, 5.2),
    (104, "Outsider One", 9.0, 9.0),
    (105, "Outsider Two", 15.0, 15.0),
)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _definition(start: datetime, status: str, in_play: bool, winner: int | None) -> dict:
    runners = []
    for priority, (selection_id, name, _, _) in enumerate(RUNNERS, start=1):
        if winner is None:
            runner_status = "ACTIVE"
        else:
            runner_status = "WINNER" if selection_id == winner else "LOSER"
        runners.append(
            {"status": runner_status, "sortPriority": priority, "id": selection_id, "name": name}
        )
    return {
        "bspMarket": True,
        "turnInPlayEnabled": True,
        "persistenceEnabled": True,
        "marketBaseRate": 5.0,
        "eventId": "30000001",
        "eventTypeId": "7",
        "numberOfWinners": 1,
        "bettingType": "ODDS",
        "marketType": "WIN",
        "marketTime": _iso(start),
        "suspendTime": _iso(start),
        "bspReconciled": winner is not None,
        "complete": True,
        "inPlay": in_play,
        "crossMatching": True,
        "runnersVoidable": False,
        "numberOfActiveRunners": len(RUNNERS),
        "betDelay": 0,
        "status": status,
        "runners": runners,
        "regulators": ["MR_INT"],
        "venue": "Sampleton",
        "countryCode": "GB",
        "discountAllowed": True,
        "timezone": "Europe/London",
        "openDate": _iso(start),
        "version": 1,
        "name": "1m Hcap",
        "eventName": "Sampleton 1st Jan",
    }


def _trend(opening: float, closing: float, progress: float, reversal: bool) -> float:
    """Underlying price at ``progress`` (0 = recording start, 1 = the off)."""
    if progress < 0.25:
        return opening
    if not reversal:
        if progress > 0.8:
            return closing
        return opening + (closing - opening) * (progress - 0.25) / 0.55
    # Start towards the closing price, then swing back past the opening
    turn = opening + (closing - opening) * 0.3
    end = opening - (closing - opening) * 0.5
    if progress < 0.5:
        return opening + (turn - opening) * (progress - 0.25) / 0.25
    if progress > 0.8:
        return end
    return turn + (end - turn) * (progress - 0.5) / 0.3


def generate_race(
    path: str | Path,
    market_id: str = "1.200000001",
    start: datetime | None = None,
    duration_seconds: int = 900,
    seed: int = 7,
    winner: int = 101,
    reversal: bool = False,
) -> Path:
    """Write a synthetic recording covering ``duration_seconds`` before the off.

    Args:
        path: File to write (one JSON message per line).
        market_id: Market id used in the recording.
        start: Scheduled race start; defaults to a fixed date for repeatability.
        duration_seconds: How long before the start the recording begins.
        seed: Seed for the random price noise.
        winner: Selection id settled as the winner.
        reversal: Make the steamer and drifter turn round halfway and end
            beyond where they started, so momentum trades lose.

    Returns:
        The path written.
    """
    rng = random.Random(seed)
    start = start or datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    begin = start - timedelta(seconds=duration_seconds)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    books: dict[int, dict[str, dict[float, float]]] = {
        sid: {"atb": {}, "atl": {}} for sid, *_ in RUNNERS
    }
    traded: dict[int, dict[float, float]] = {sid: {} for sid, *_ in RUNNERS}
    volume: dict[int, float] = {sid: 0.0 for sid, *_ in RUNNERS}
    lines = []

    def message(moment: datetime, change: dict) -> None:
        change["id"] = market_id
        lines.append(
            json.dumps(
                {
                    "op": "mcm",
                    "clk": str(len(lines) + 1),
                    "pt": int(moment.timestamp() * 1000),
                    "mc": [change],
                },
                separators=(",", ":"),
            )
        )

    for second in range(duration_seconds):
        moment = begin + timedelta(seconds=second)
        progress = second / duration_seconds
        runner_changes = []
        for selection_id, _, opening, closing in RUNNERS:
            trend = _trend(opening, closing, progress, reversal)
            ltp = nearest_tick(trend * (1 + rng.uniform(-0.004, 0.004)))

            # More money queues to back a steamer, more to lay a drifter
            bias = 0.5 if closing == opening else (0.68 if closing < opening else 0.32)
            depth = 400.0 if opening < 8 else 120.0
            back_prices = [move_ticks(ltp, -1 - level) for level in range(3)]
            lay_prices = [move_ticks(ltp, level) for level in range(3)]
            new_book = {
                "atb": {p: round(depth * (1 - bias) * rng.uniform(0.8, 1.2), 2) for p in back_prices},
                "atl": {p: round(depth * bias * rng.uniform(0.8, 1.2), 2) for p in lay_prices},
            }

            change: dict = {"id": selection_id, "ltp": ltp}
            for side in ("atb", "atl"):
                updates = [[price, size] for price, size in new_book[side].items()]
                updates += [[price, 0] for price in books[selection_id][side] if price not in new_book[side]]
                change[side] = updates
                books[selection_id][side] = new_book[side]

            matched = round(rng.uniform(20, 120) * (3.0 / opening), 2)
            traded[selection_id][ltp] = round(traded[selection_id].get(ltp, 0.0) + matched, 2)
            volume[selection_id] = round(volume[selection_id] + matched, 2)
            change["trd"] = [[ltp, traded[selection_id][ltp]]]
            change["tv"] = volume[selection_id]
            runner_changes.append(change)

        market_change: dict = {"rc": runner_changes, "tv": round(60_000 + sum(volume.values()), 2)}
        if second == 0:
            market_change["marketDefinition"] = _definition(start, "OPEN", False, None)
            market_change["img"] = True
        message(moment, market_change)

    message(start, {"marketDefinition": _definition(start, "OPEN", True, None)})
    message(start + timedelta(seconds=120), {"marketDefinition": _definition(start, "CLOSED", True, winner)})

    path.write_text("\n".join(lines) + "\n")
    return path
