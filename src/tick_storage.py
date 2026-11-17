"""Buffered writer for ticks and market metadata into TimescaleDB/PostgreSQL."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

import psycopg
from loguru import logger

from src.ticks import TICK_COLUMNS, MarketInfo, RunnerInfo, Tick

SCHEMA_PATH = Path(__file__).parent.parent / "sql" / "schema.sql"

INSERT_TICK_SQL = (
    f"INSERT INTO ticks ({', '.join(TICK_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(TICK_COLUMNS))}) "
    "ON CONFLICT (market_id, selection_id, time) DO NOTHING"
)

UPSERT_MARKET_SQL = """
INSERT INTO markets (market_id, event_name, venue, country_code, market_name, market_type, start_time)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (market_id) DO UPDATE SET
    event_name   = COALESCE(EXCLUDED.event_name, markets.event_name),
    venue        = COALESCE(EXCLUDED.venue, markets.venue),
    country_code = COALESCE(EXCLUDED.country_code, markets.country_code),
    market_name  = COALESCE(EXCLUDED.market_name, markets.market_name),
    market_type  = COALESCE(EXCLUDED.market_type, markets.market_type),
    start_time   = COALESCE(EXCLUDED.start_time, markets.start_time),
    updated_at   = now()
"""

UPSERT_RUNNER_SQL = """
INSERT INTO runners (market_id, selection_id, runner_name, sort_priority)
VALUES (%s, %s, %s, %s)
ON CONFLICT (market_id, selection_id) DO UPDATE SET
    runner_name   = COALESCE(EXCLUDED.runner_name, runners.runner_name),
    sort_priority = COALESCE(EXCLUDED.sort_priority, runners.sort_priority)
"""


class TickStorage:
    """Writes ticks in batches and survives database outages.

    Ticks are buffered in memory and written when ``batch_size`` rows have
    accumulated or ``flush_interval`` seconds have passed. If a write fails the
    rows stay buffered (up to ``max_buffer``, oldest dropped first) and the
    connection is reopened on the next attempt, so a database restart does not
    stop the stream logger.

    Args:
        dsn: PostgreSQL connection string.
        batch_size: Flush once this many ticks are buffered.
        flush_interval: Flush at least this often (seconds) while ticks arrive.
        max_buffer: Upper bound on buffered ticks during an outage.
        retry_interval: After a failed write, wait this long (seconds) before
            touching the database again, so an outage cannot stall the stream.
        connect: Connection factory, replaceable in tests.
        clock: Monotonic clock, replaceable in tests.
    """

    def __init__(
        self,
        dsn: str,
        batch_size: int = 500,
        flush_interval: float = 1.0,
        max_buffer: int = 200_000,
        retry_interval: float = 5.0,
        connect: Callable[[str], psycopg.Connection] = psycopg.connect,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._dsn = dsn
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_buffer = max(max_buffer, batch_size)
        self._retry_interval = retry_interval
        self._retry_at = 0.0
        self._connect = connect
        self._clock = clock
        self._conn: Optional[psycopg.Connection] = None
        self._buffer: list[tuple] = []
        self._last_flush = clock()
        self._lock = threading.RLock()
        self.ticks_written = 0
        self.ticks_dropped = 0

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = self._connect(self._dsn)
        return self._conn

    def _discard_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _execute(self, sql: str, rows: list[tuple]) -> bool:
        """Run ``sql`` for every row in one transaction. Returns success."""
        if self._clock() < self._retry_at:
            return False
        try:
            conn = self._connection()
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
            return True
        except psycopg.Error as exc:
            logger.error("Database write failed ({} rows): {}", len(rows), exc)
            self._discard_connection()
            self._retry_at = self._clock() + self._retry_interval
            return False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self, schema_path: Path = SCHEMA_PATH) -> None:
        """Create tables (and the hypertable when TimescaleDB is installed)."""
        sql = Path(schema_path).read_text()
        with self._lock:
            conn = self._connection()
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        logger.info("Database schema applied from {}", schema_path)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def upsert_market(self, info: MarketInfo) -> bool:
        row = (
            info.market_id,
            info.event_name,
            info.venue,
            info.country_code,
            info.market_name,
            info.market_type,
            info.start_time,
        )
        with self._lock:
            return self._execute(UPSERT_MARKET_SQL, [row])

    def upsert_runners(self, runners: Iterable[RunnerInfo]) -> bool:
        rows = [
            (r.market_id, r.selection_id, r.runner_name, r.sort_priority) for r in runners
        ]
        if not rows:
            return True
        with self._lock:
            return self._execute(UPSERT_RUNNER_SQL, rows)

    # ------------------------------------------------------------------
    # Ticks
    # ------------------------------------------------------------------

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def add_ticks(self, ticks: Iterable[Tick]) -> None:
        """Buffer ticks, flushing when the batch is full or stale."""
        with self._lock:
            self._buffer.extend(tick.as_row() for tick in ticks)
            if not self._buffer:
                return
            full = len(self._buffer) >= self._batch_size
            stale = self._clock() - self._last_flush >= self._flush_interval
            if full or stale:
                self.flush()

    def flush(self) -> bool:
        """Write all buffered ticks. Returns False if they are still buffered."""
        with self._lock:
            self._last_flush = self._clock()
            if not self._buffer:
                return True
            if self._execute(INSERT_TICK_SQL, self._buffer):
                self.ticks_written += len(self._buffer)
                self._buffer = []
                return True
            overflow = len(self._buffer) - self._max_buffer
            if overflow > 0:
                del self._buffer[:overflow]
                self.ticks_dropped += overflow
                logger.warning(
                    "Tick buffer full; dropped {} oldest ticks ({} dropped in total)",
                    overflow,
                    self.ticks_dropped,
                )
            return False

    def close(self) -> None:
        """Flush what can be flushed and close the connection."""
        with self._lock:
            if not self.flush():
                logger.error("{} ticks could not be written before close", len(self._buffer))
            self._discard_connection()

    def __enter__(self) -> "TickStorage":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
