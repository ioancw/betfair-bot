"""Tests for the recording strategies."""

import gzip
import json
from unittest.mock import MagicMock

import pytest

from src.stream_logger import RawStreamRecorder, TickLoggerStrategy
from tests.factories import make_catalogue, make_market, make_market_book


@pytest.fixture
def storage():
    storage = MagicMock()
    storage.upsert_market.return_value = True
    storage.upsert_runners.return_value = True
    storage.ticks_written = 0
    storage.ticks_dropped = 0
    return storage


class TestTickLoggerStrategy:
    def test_records_open_pre_race_markets(self, storage):
        strategy = TickLoggerStrategy(storage=storage)
        assert strategy.check_market_book(make_market(), make_market_book()) is True

    def test_skips_suspended_markets_only_when_closed(self, storage):
        strategy = TickLoggerStrategy(storage=storage)
        assert strategy.check_market_book(make_market(), make_market_book(status="SUSPENDED")) is True
        assert strategy.check_market_book(make_market(), make_market_book(status="CLOSED")) is False

    def test_in_play_is_skipped_by_default(self, storage):
        strategy = TickLoggerStrategy(storage=storage)
        assert strategy.check_market_book(make_market(), make_market_book(inplay=True)) is False

    def test_in_play_can_be_included(self, storage):
        strategy = TickLoggerStrategy(storage=storage, include_in_play=True)
        assert strategy.check_market_book(make_market(), make_market_book(inplay=True)) is True

    def test_market_book_is_stored_as_ticks(self, storage):
        strategy = TickLoggerStrategy(storage=storage)
        strategy.process_market_book(make_market(), make_market_book())

        ticks = storage.add_ticks.call_args.args[0]
        assert [t.selection_id for t in ticks] == [101, 102]

    def test_metadata_is_saved_once_per_market(self, storage):
        strategy = TickLoggerStrategy(storage=storage)
        market, book = make_market(), make_market_book()

        strategy.process_market_book(market, book)
        strategy.process_market_book(market, book)

        assert storage.upsert_market.call_count == 1
        assert storage.upsert_market.call_args.args[0].venue == "Ascot"
        assert storage.add_ticks.call_count == 2

    def test_metadata_is_retried_after_a_failed_save(self, storage):
        storage.upsert_market.return_value = False
        strategy = TickLoggerStrategy(storage=storage)
        market, book = make_market(), make_market_book()

        strategy.process_market_book(market, book)
        storage.upsert_market.return_value = True
        strategy.process_market_book(market, book)

        assert storage.upsert_market.call_count == 2

    def test_catalogue_fills_in_runner_names(self, storage):
        strategy = TickLoggerStrategy(storage=storage)
        book = make_market_book()
        market = make_market(market_book=book)
        strategy.process_market_book(market, book)
        assert storage.upsert_runners.call_args.args[0][0].runner_name is None

        market.market_catalogue = make_catalogue({101: "Alpha", 102: "Bravo"})
        strategy.process_market_catalogue(market, market.market_catalogue)

        names = [r.runner_name for r in storage.upsert_runners.call_args.args[0]]
        assert names == ["Alpha", "Bravo"]

    def test_names_are_not_overwritten_by_a_nameless_update(self, storage):
        strategy = TickLoggerStrategy(storage=storage)
        book = make_market_book()
        market = make_market(market_book=book, catalogue=make_catalogue())
        strategy.process_market_catalogue(market, market.market_catalogue)
        calls = storage.upsert_runners.call_count

        market.market_catalogue = None
        strategy.process_market_catalogue(market, None)

        assert storage.upsert_runners.call_count == calls

    def test_closed_market_flushes_and_allows_fresh_metadata(self, storage):
        strategy = TickLoggerStrategy(storage=storage)
        market, book = make_market(), make_market_book()
        strategy.process_market_book(market, book)

        strategy.process_closed_market(market, book)
        storage.flush.assert_called_once()

        strategy.process_market_book(market, book)
        assert storage.upsert_market.call_count == 2

    def test_finish_closes_storage(self, storage):
        TickLoggerStrategy(storage=storage).finish(flumine=None)
        storage.close.assert_called_once()


def definition(status):
    return {"status": status, "runners": []}


class TestRawStreamRecorder:
    def test_each_message_is_written_as_a_stream_line(self, tmp_path):
        recorder = RawStreamRecorder(tmp_path / "streams")
        recorder.process_raw_data("clk1", 1000, {"id": "1.100", "rc": [{"id": 101, "ltp": 5.0}]})
        recorder.process_raw_data("clk2", 2000, {"id": "1.100", "rc": [{"id": 101, "ltp": 4.9}]})
        recorder.finish(flumine=None)

        lines = (tmp_path / "streams" / "1.100").read_text().splitlines()
        assert [json.loads(line) for line in lines] == [
            {"op": "mcm", "clk": "clk1", "pt": 1000, "mc": [{"id": "1.100", "rc": [{"id": 101, "ltp": 5.0}]}]},
            {"op": "mcm", "clk": "clk2", "pt": 2000, "mc": [{"id": "1.100", "rc": [{"id": 101, "ltp": 4.9}]}]},
        ]

    def test_markets_are_written_to_separate_files(self, tmp_path):
        recorder = RawStreamRecorder(tmp_path)
        recorder.process_raw_data("a", 1, {"id": "1.100"})
        recorder.process_raw_data("b", 2, {"id": "1.200"})
        recorder.finish(flumine=None)

        assert sorted(p.name for p in tmp_path.iterdir()) == ["1.100", "1.200"]

    def test_messages_without_market_id_are_ignored(self, tmp_path):
        recorder = RawStreamRecorder(tmp_path)
        recorder.process_raw_data("a", 1, {"rc": []})
        recorder.finish(flumine=None)
        assert list(tmp_path.iterdir()) == []

    def test_closed_market_is_gzipped(self, tmp_path):
        recorder = RawStreamRecorder(tmp_path)
        recorder.process_raw_data("a", 1, {"id": "1.100", "marketDefinition": definition("OPEN")})
        recorder.process_raw_data("b", 2, {"id": "1.100", "marketDefinition": definition("CLOSED")})

        assert [p.name for p in tmp_path.iterdir()] == ["1.100.gz"]
        with gzip.open(tmp_path / "1.100.gz", "rt") as f:
            messages = [json.loads(line) for line in f]
        assert [m["clk"] for m in messages] == ["a", "b"]
        assert messages[1]["mc"][0]["marketDefinition"]["status"] == "CLOSED"

    def test_add_creates_directory(self, tmp_path):
        recorder = RawStreamRecorder(tmp_path / "nested" / "streams")
        recorder.add(flumine=None)
        assert (tmp_path / "nested" / "streams").is_dir()

    def test_recording_can_be_replayed_by_the_backtester(self, tmp_path, sample_race):
        """A live recording must give the same backtest result as its source."""
        from src.backtest import run_backtest

        recorder = RawStreamRecorder(tmp_path / "streams")
        with open(sample_race) as f:
            for line in f:
                message = json.loads(line)
                for datum in message["mc"]:
                    recorder.process_raw_data(message["clk"], message["pt"], datum)
        recorder.finish(flumine=None)

        original = run_backtest([sample_race])
        replayed = run_backtest([str(tmp_path / "streams" / "1.200000001.gz")])

        assert len(replayed.trades) == len(original.trades) > 0
        assert replayed.gross_profit == original.gross_profit


class RecordingStorage:
    """In-memory stand-in for TickStorage."""

    def __init__(self):
        self.ticks, self.markets, self.runners = [], [], []
        self.ticks_written = self.ticks_dropped = 0
        self.closed = False

    def add_ticks(self, ticks):
        self.ticks.extend(ticks)

    def upsert_market(self, info):
        self.markets.append(info)
        return True

    def upsert_runners(self, runners):
        self.runners.append(list(runners))
        return True

    def flush(self):
        return True

    def close(self):
        self.closed = True


def test_tick_logger_with_real_flumine_market_books(sample_race):
    """Drive the logger with genuine flumine objects by replaying a recording."""
    from flumine import FlumineSimulation, clients
    from flumine.streams.betfairhistoricalstream import BetfairHistoricalStream

    storage = RecordingStorage()
    framework = FlumineSimulation(client=clients.SimulatedClient())
    framework.add_strategy(
        TickLoggerStrategy(storage=storage, stream=BetfairHistoricalStream(file_path=sample_race))
    )
    framework.run()

    # 900 seconds of pre-race updates for 5 runners; nothing once in play
    assert len(storage.ticks) == 900 * 5
    assert not any(t.in_play for t in storage.ticks)
    assert {t.selection_id for t in storage.ticks} == {101, 102, 103, 104, 105}

    first = storage.ticks[0]
    assert first.market_id == "1.200000001"
    assert first.time.tzinfo is not None
    assert first.market_status == "OPEN"
    assert first.back[0][0] < first.lay[0][0]  # best back sits below best lay
    assert all(size and size > 0 for _, size in first.back + first.lay)

    assert storage.markets[0].venue == "Sampleton"
    assert storage.markets[0].market_type == "WIN"
    assert [r.runner_name for r in storage.runners[0]][:2] == ["Steady Favourite", "Steamer"]
    assert storage.closed is True
