-- Tick storage schema. Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS markets (
    market_id     TEXT PRIMARY KEY,
    event_name    TEXT,
    venue         TEXT,
    country_code  TEXT,
    market_name   TEXT,
    market_type   TEXT,
    start_time    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runners (
    market_id      TEXT   NOT NULL,
    selection_id   BIGINT NOT NULL,
    runner_name    TEXT,
    sort_priority  INTEGER,
    PRIMARY KEY (market_id, selection_id)
);

-- One row per runner each time that runner's prices change.
CREATE TABLE IF NOT EXISTS ticks (
    time                  TIMESTAMPTZ NOT NULL,
    market_id             TEXT        NOT NULL,
    selection_id          BIGINT      NOT NULL,
    ltp                   DOUBLE PRECISION,
    runner_total_matched  DOUBLE PRECISION,
    back_price_1          DOUBLE PRECISION,
    back_size_1           DOUBLE PRECISION,
    back_price_2          DOUBLE PRECISION,
    back_size_2           DOUBLE PRECISION,
    back_price_3          DOUBLE PRECISION,
    back_size_3           DOUBLE PRECISION,
    lay_price_1           DOUBLE PRECISION,
    lay_size_1            DOUBLE PRECISION,
    lay_price_2           DOUBLE PRECISION,
    lay_size_2            DOUBLE PRECISION,
    lay_price_3           DOUBLE PRECISION,
    lay_size_3            DOUBLE PRECISION,
    market_total_matched  DOUBLE PRECISION,
    in_play               BOOLEAN     NOT NULL DEFAULT FALSE,
    market_status         TEXT,
    runner_status         TEXT,
    PRIMARY KEY (market_id, selection_id, time)
);

CREATE INDEX IF NOT EXISTS ticks_time_idx ON ticks (time DESC);

-- TimescaleDB features are applied when the extension is installed; on plain
-- PostgreSQL the tables above still work as ordinary tables.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
        PERFORM create_hypertable(
            'ticks', 'time',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE,
            migrate_data => TRUE
        );
        ALTER TABLE ticks SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'market_id, selection_id',
            timescaledb.compress_orderby = 'time'
        );
        PERFORM add_compression_policy('ticks', INTERVAL '7 days', if_not_exists => TRUE);
    ELSE
        RAISE NOTICE 'timescaledb extension not available; ticks is a plain table';
    END IF;
END
$$;
