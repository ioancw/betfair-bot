"""Tests for converting market books into storable ticks."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.ticks import (
    TICK_COLUMNS,
    as_utc,
    changed_selection_ids,
    ladder,
    market_info,
    price_size,
    runner_infos,
    ticks_from_market_book,
)
from tests.factories import (
    PUBLISH_TIME,
    START_TIME,
    make_catalogue,
    make_market,
    make_market_book,
    make_runner,
)


class TestLadder:
    def test_price_size_reads_dicts_and_objects(self):
        assert price_size({"price": 3.5, "size": 20.0}) == (3.5, 20.0)
        assert price_size(SimpleNamespace(price=3.5, size=20.0)) == (3.5, 20.0)

    def test_ladder_pads_missing_levels(self):
        assert ladder([{"price": 2.0, "size": 10.0}]) == ((2.0, 10.0), (None, None), (None, None))

    def test_ladder_truncates_to_three_levels(self):
        entries = [{"price": 2.0 + i, "size": 1.0} for i in range(5)]
        assert [p for p, _ in ladder(entries)] == [2.0, 3.0, 4.0]

    def test_ladder_handles_empty_side(self):
        assert ladder(None) == ((None, None),) * 3
        assert ladder([]) == ((None, None),) * 3


class TestAsUtc:
    def test_naive_datetime_is_treated_as_utc(self):
        assert as_utc(datetime(2026, 1, 1, 12)) == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    def test_aware_datetime_is_converted(self):
        london_summer = timezone(timedelta(hours=1))
        result = as_utc(datetime(2026, 6, 1, 13, tzinfo=london_summer))
        assert result == datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
        assert result.utcoffset() == timedelta(0)

    def test_none_passes_through(self):
        assert as_utc(None) is None


class TestChangedSelectionIds:
    def test_no_streaming_update_means_all_runners(self):
        assert changed_selection_ids(make_market_book(streaming_update=None)) is None

    def test_full_image_means_all_runners(self):
        book = make_market_book(streaming_update={"img": True, "rc": [{"id": 101}]})
        assert changed_selection_ids(book) is None

    def test_market_definition_change_means_all_runners(self):
        book = make_market_book(streaming_update={"marketDefinition": {}, "rc": []})
        assert changed_selection_ids(book) is None

    def test_delta_lists_only_touched_runners(self):
        book = make_market_book(streaming_update={"rc": [{"id": 102, "ltp": 4.0}]})
        assert changed_selection_ids(book) == {102}

    def test_delta_without_runner_changes_is_empty(self):
        assert changed_selection_ids(make_market_book(streaming_update={"tv": 5.0})) == set()


class TestTicksFromMarketBook:
    def test_one_tick_per_runner_on_full_image(self):
        ticks = ticks_from_market_book(make_market_book())
        assert [t.selection_id for t in ticks] == [101, 102]

    def test_only_changed_runners_are_emitted(self):
        book = make_market_book(streaming_update={"rc": [{"id": 102}]})
        assert [t.selection_id for t in ticks_from_market_book(book)] == [102]

    def test_only_changed_can_be_disabled(self):
        book = make_market_book(streaming_update={"rc": [{"id": 102}]})
        assert len(ticks_from_market_book(book, only_changed=False)) == 2

    def test_tick_fields(self):
        runner = make_runner(101, ltp=5.0, back=((4.9, 100.0),), lay=((5.0, 50.0), (5.1, 25.0)))
        book = make_market_book([runner], total_matched=1234.5)
        tick = ticks_from_market_book(book)[0]

        assert tick.time == PUBLISH_TIME.replace(tzinfo=timezone.utc)
        assert tick.market_id == "1.100"
        assert tick.ltp == 5.0
        assert tick.runner_total_matched == 1000.0
        assert tick.back == ((4.9, 100.0), (None, None), (None, None))
        assert tick.lay == ((5.0, 50.0), (5.1, 25.0), (None, None))
        assert tick.market_total_matched == 1234.5
        assert tick.in_play is False
        assert tick.market_status == "OPEN"
        assert tick.runner_status == "ACTIVE"

    def test_object_style_ladders_are_supported(self):
        book = make_market_book([make_runner(as_dicts=False)])
        assert ticks_from_market_book(book)[0].back[0] == (4.9, 100.0)

    def test_as_row_matches_column_order(self):
        tick = ticks_from_market_book(make_market_book([make_runner()]))[0]
        row = dict(zip(TICK_COLUMNS, tick.as_row(), strict=True))

        assert row["selection_id"] == 101
        assert row["back_price_1"] == 4.9 and row["back_size_1"] == 100.0
        assert row["back_price_3"] == 4.7 and row["back_size_3"] == 60.0
        assert row["lay_price_1"] == 5.0 and row["lay_size_2"] == 80.0
        assert row["market_status"] == "OPEN"
        assert row["runner_status"] == "ACTIVE"
        assert row["in_play"] is False


class TestMetadata:
    def test_market_info(self):
        info = market_info(make_market())
        assert info.market_id == "1.100"
        assert info.venue == "Ascot"
        assert info.market_type == "WIN"
        assert info.start_time == START_TIME.replace(tzinfo=timezone.utc)

    def test_runner_names_come_from_catalogue(self):
        market = make_market(catalogue=make_catalogue({101: "Alpha", 102: "Bravo"}))
        infos = runner_infos(market, make_market_book())
        assert [(r.selection_id, r.runner_name, r.sort_priority) for r in infos] == [
            (101, "Alpha", 1),
            (102, "Bravo", 2),
        ]

    def test_falls_back_to_market_definition_without_names(self):
        infos = runner_infos(make_market(), make_market_book())
        assert [(r.selection_id, r.runner_name) for r in infos] == [(101, None), (102, None)]

    def test_no_definition_and_no_catalogue_gives_nothing(self):
        assert runner_infos(make_market(), SimpleNamespace(market_definition=None)) == []
