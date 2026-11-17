"""Builders for fake flumine objects used across the tests."""

from datetime import datetime
from types import SimpleNamespace

PUBLISH_TIME = datetime(2026, 1, 1, 14, 20, 0)  # naive UTC, as betfairlightweight gives
START_TIME = datetime(2026, 1, 1, 14, 30, 0)


def make_runner(
    selection_id=101,
    ltp=5.0,
    back=((4.9, 100.0), (4.8, 80.0), (4.7, 60.0)),
    lay=((5.0, 100.0), (5.1, 80.0), (5.2, 60.0)),
    status="ACTIVE",
    total_matched=1000.0,
    as_dicts=True,
):
    def side(levels):
        if as_dicts:
            return [{"price": p, "size": s} for p, s in levels]
        return [SimpleNamespace(price=p, size=s) for p, s in levels]

    return SimpleNamespace(
        selection_id=selection_id,
        handicap=0,
        status=status,
        last_price_traded=ltp,
        total_matched=total_matched,
        ex=SimpleNamespace(available_to_back=side(back), available_to_lay=side(lay)),
    )


def make_market_book(
    runners=None,
    market_id="1.100",
    status="OPEN",
    inplay=False,
    streaming_update=None,
    publish_time=PUBLISH_TIME,
    total_matched=50_000.0,
    definition_runners=None,
):
    runners = runners if runners is not None else [make_runner(101), make_runner(102)]
    if definition_runners is None:
        definition_runners = [
            SimpleNamespace(selection_id=r.selection_id, name=None, sort_priority=i)
            for i, r in enumerate(runners, start=1)
        ]
    return SimpleNamespace(
        market_id=market_id,
        status=status,
        inplay=inplay,
        publish_time=publish_time,
        publish_time_epoch=int(publish_time.timestamp() * 1000),
        total_matched=total_matched,
        runners=runners,
        streaming_update=streaming_update,
        market_definition=SimpleNamespace(runners=definition_runners),
    )


def make_market(market_id="1.100", market_book=None, catalogue=None, seconds_to_start=300.0):
    return SimpleNamespace(
        market_id=market_id,
        event_name="Ascot 1st Jan",
        venue="Ascot",
        country_code="GB",
        market_name="1m Hcap",
        market_type="WIN",
        market_start_datetime=START_TIME,
        market_catalogue=catalogue,
        market_book=market_book,
        seconds_to_start=seconds_to_start,
    )


def make_catalogue(names=None):
    names = names or {101: "Alpha", 102: "Bravo"}
    return SimpleNamespace(
        runners=[
            SimpleNamespace(selection_id=sid, runner_name=name, sort_priority=i)
            for i, (sid, name) in enumerate(names.items(), start=1)
        ]
    )
