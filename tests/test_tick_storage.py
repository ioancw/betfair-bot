"""Tests for TickStorage using a fake database connection."""

import os
from datetime import datetime, timezone

import psycopg
import pytest

from src.tick_storage import INSERT_TICK_SQL, TickStorage
from src.ticks import MarketInfo, RunnerInfo, ticks_from_market_book
from tests.factories import make_market_book, make_runner


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def executemany(self, sql, rows):
        if self.conn.fail:
            raise psycopg.OperationalError("database is down")
        self.conn.batches.append((sql, list(rows)))

    def execute(self, sql):
        self.conn.executed.append(sql)


class FakeConnection:
    def __init__(self):
        self.batches, self.executed = [], []
        self.commits = 0
        self.closed = False
        self.fail = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class Harness:
    """A TickStorage wired to fake connections and a controllable clock."""

    def __init__(self, **kwargs):
        self.now = 0.0
        self.connections = []
        self.fail_next_connections = False
        self.storage = TickStorage(
            "postgresql://fake", connect=self._connect, clock=lambda: self.now, **kwargs
        )

    def _connect(self, dsn):
        conn = FakeConnection()
        conn.fail = self.fail_next_connections
        self.connections.append(conn)
        return conn

    @property
    def tick_rows(self):
        return [
            row
            for conn in self.connections
            for sql, rows in conn.batches
            if sql == INSERT_TICK_SQL
            for row in rows
        ]


def make_ticks(count):
    """``count`` ticks with distinct selection ids."""
    return ticks_from_market_book(make_market_book([make_runner(i) for i in range(count)]))


class TestBuffering:
    def test_ticks_are_buffered_until_batch_is_full(self):
        h = Harness(batch_size=5, flush_interval=60)
        h.storage.add_ticks(make_ticks(4))
        assert h.storage.buffered == 4
        assert h.connections == []

        h.storage.add_ticks(make_ticks(1))
        assert h.storage.buffered == 0
        assert len(h.tick_rows) == 5
        assert h.connections[0].commits == 1
        assert h.storage.ticks_written == 5

    def test_stale_buffer_is_flushed_after_interval(self):
        h = Harness(batch_size=100, flush_interval=1.0)
        h.storage.add_ticks(make_ticks(1))
        assert h.tick_rows == []

        h.now = 1.5
        h.storage.add_ticks(make_ticks(1))
        assert len(h.tick_rows) == 2

    def test_adding_nothing_does_not_connect(self):
        h = Harness(batch_size=1, flush_interval=0)
        h.storage.add_ticks([])
        assert h.connections == []

    def test_flush_with_empty_buffer_succeeds_without_connecting(self):
        h = Harness()
        assert h.storage.flush() is True
        assert h.connections == []

    def test_connection_is_reused_between_flushes(self):
        h = Harness(batch_size=1)
        h.storage.add_ticks(make_ticks(1))
        h.storage.add_ticks(make_ticks(1))
        assert len(h.connections) == 1
        assert h.connections[0].commits == 2

    def test_batch_size_must_be_positive(self):
        with pytest.raises(ValueError, match="batch_size"):
            TickStorage("postgresql://fake", batch_size=0)


class TestOutages:
    def test_failed_write_keeps_ticks_and_drops_connection(self):
        h = Harness(batch_size=2, retry_interval=5.0)
        h.fail_next_connections = True
        h.storage.add_ticks(make_ticks(2))

        assert h.storage.buffered == 2
        assert h.storage.ticks_written == 0
        assert h.connections[0].closed is True

    def test_database_is_left_alone_during_retry_interval(self):
        h = Harness(batch_size=2, retry_interval=5.0)
        h.fail_next_connections = True
        h.storage.add_ticks(make_ticks(2))
        h.fail_next_connections = False

        h.now = 2.0
        assert h.storage.flush() is False
        assert len(h.connections) == 1

    def test_buffered_ticks_are_written_after_recovery(self):
        h = Harness(batch_size=2, retry_interval=5.0)
        h.fail_next_connections = True
        h.storage.add_ticks(make_ticks(2))
        h.fail_next_connections = False

        h.now = 6.0
        h.storage.add_ticks(make_ticks(1))

        assert len(h.connections) == 2
        assert len(h.tick_rows) == 3
        assert h.storage.buffered == 0
        assert h.storage.ticks_written == 3

    def test_oldest_ticks_are_dropped_when_buffer_overflows(self):
        h = Harness(batch_size=2, max_buffer=3, retry_interval=0)
        h.fail_next_connections = True
        h.storage.add_ticks(make_ticks(5))

        assert h.storage.buffered == 3
        assert h.storage.ticks_dropped == 2
        # The survivors are the newest three (selection ids 2, 3, 4)
        h.fail_next_connections = False
        h.storage.flush()
        assert [row[2] for row in h.tick_rows] == [2, 3, 4]

    def test_non_database_errors_are_not_swallowed(self):
        h = Harness(batch_size=1)

        def broken_connect(dsn):
            raise RuntimeError("programming error")

        h.storage._connect = broken_connect
        with pytest.raises(RuntimeError, match="programming error"):
            h.storage.add_ticks(make_ticks(1))


class TestMetadata:
    def test_upsert_market_writes_one_row(self):
        h = Harness()
        start = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
        info = MarketInfo("1.100", "Ascot 1st Jan", "Ascot", "GB", "1m Hcap", "WIN", start)

        assert h.storage.upsert_market(info) is True
        sql, rows = h.connections[0].batches[0]
        assert "INSERT INTO markets" in sql
        assert rows == [("1.100", "Ascot 1st Jan", "Ascot", "GB", "1m Hcap", "WIN", start)]

    def test_upsert_runners_writes_all_rows(self):
        h = Harness()
        runners = [RunnerInfo("1.100", 101, "Alpha", 1), RunnerInfo("1.100", 102, "Bravo", 2)]

        assert h.storage.upsert_runners(runners) is True
        sql, rows = h.connections[0].batches[0]
        assert "INSERT INTO runners" in sql
        assert rows == [("1.100", 101, "Alpha", 1), ("1.100", 102, "Bravo", 2)]

    def test_upsert_runners_with_nothing_is_a_no_op(self):
        h = Harness()
        assert h.storage.upsert_runners([]) is True
        assert h.connections == []

    def test_upsert_reports_failure(self):
        h = Harness()
        h.fail_next_connections = True
        assert h.storage.upsert_market(MarketInfo("1.1", None, None, None, None, None, None)) is False


class TestLifecycle:
    def test_close_flushes_and_closes_connection(self):
        h = Harness(batch_size=100, flush_interval=60)
        h.storage.add_ticks(make_ticks(3))
        h.storage.close()

        assert len(h.tick_rows) == 3
        assert h.connections[0].closed is True

    def test_context_manager_closes(self):
        h = Harness(batch_size=100, flush_interval=60)
        with h.storage as storage:
            storage.add_ticks(make_ticks(1))
        assert len(h.tick_rows) == 1
        assert h.connections[0].closed is True

    def test_init_schema_executes_schema_file(self, tmp_path):
        schema = tmp_path / "schema.sql"
        schema.write_text("CREATE TABLE example (id INT);")
        h = Harness()

        h.storage.init_schema(schema)

        assert h.connections[0].executed == ["CREATE TABLE example (id INT);"]
        assert h.connections[0].commits == 1

    def test_bundled_schema_defines_all_tables(self):
        from src.tick_storage import SCHEMA_PATH
        from src.ticks import TICK_COLUMNS

        sql = SCHEMA_PATH.read_text()
        for table in ("markets", "runners", "ticks"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        ticks_ddl = sql.split("CREATE TABLE IF NOT EXISTS ticks")[1].split(");")[0]
        for column in TICK_COLUMNS:
            assert column in ticks_ddl
        assert "create_hypertable" in sql


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run against a real PostgreSQL/TimescaleDB",
)
class TestRealDatabase:
    """Round trip through a real database. Uses and removes market id 1.999999999."""

    MARKET_ID = "1.999999999"

    @pytest.fixture
    def storage(self):
        storage = TickStorage(os.environ["TEST_DATABASE_URL"], batch_size=1000)
        storage.init_schema()
        yield storage
        storage.close()
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
            for table in ("ticks", "runners", "markets"):
                conn.execute(f"DELETE FROM {table} WHERE market_id = %s", (self.MARKET_ID,))

    def test_schema_can_be_applied_twice(self, storage):
        storage.init_schema()

    def test_ticks_and_metadata_round_trip(self, storage):
        start = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
        book = make_market_book(market_id=self.MARKET_ID)
        ticks = ticks_from_market_book(book)

        assert storage.upsert_market(
            MarketInfo(self.MARKET_ID, "Ascot", "Ascot", "GB", "1m Hcap", "WIN", start)
        )
        assert storage.upsert_runners([RunnerInfo(self.MARKET_ID, 101, None, 1)])
        # A later upsert fills in the name without losing the sort priority
        assert storage.upsert_runners([RunnerInfo(self.MARKET_ID, 101, "Alpha", None)])
        storage.add_ticks(ticks)
        storage.add_ticks(ticks)  # duplicates are ignored
        assert storage.flush() is True

        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
            rows = conn.execute(
                "SELECT selection_id, ltp, back_price_1, lay_size_3, in_play, time "
                "FROM ticks WHERE market_id = %s ORDER BY selection_id",
                (self.MARKET_ID,),
            ).fetchall()
            runner = conn.execute(
                "SELECT runner_name, sort_priority FROM runners WHERE market_id = %s",
                (self.MARKET_ID,),
            ).fetchone()
            market = conn.execute(
                "SELECT venue, start_time FROM markets WHERE market_id = %s", (self.MARKET_ID,)
            ).fetchone()

        assert [(r[0], r[1], r[2], r[3], r[4]) for r in rows] == [
            (101, 5.0, 4.9, 60.0, False),
            (102, 5.0, 4.9, 60.0, False),
        ]
        assert rows[0][5] == ticks[0].time
        assert runner == ("Alpha", 1)
        assert market == ("Ascot", start)
