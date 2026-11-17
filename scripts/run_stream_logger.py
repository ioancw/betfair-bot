#!/usr/bin/env python3
"""Record live prices for upcoming horse racing WIN markets. Places no bets.

Writes two things:
  * rows in the `ticks` table (best three back/lay prices per runner) for analysis
  * raw stream files under STREAM_DIR, replayable with scripts/run_backtest.py

Usage:
    python scripts/run_stream_logger.py                 # GB races
    python scripts/run_stream_logger.py --countries GB IE
    python scripts/run_stream_logger.py --no-db         # raw files only

Stop with Ctrl+C.
"""

import argparse
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from src.config import Config
from src.flumine_setup import (
    create_framework,
    create_trading_client,
    price_stream,
    recording_stream,
)
from src.logging_setup import setup_logging
from src.stream_logger import RawStreamRecorder, TickLoggerStrategy
from src.tick_storage import TickStorage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--countries", nargs="+", default=["GB"], help="Country codes to record")
    parser.add_argument("--no-db", action="store_true", help="Do not write ticks to the database")
    parser.add_argument("--no-raw", action="store_true", help="Do not write raw stream files")
    parser.add_argument("--in-play", action="store_true", help="Keep recording ticks after the off")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.no_db and args.no_raw:
        raise SystemExit("Nothing to record: --no-db and --no-raw were both given.")
    setup_logging(log_dir=Config.LOG_DIR, log_level=Config.LOG_LEVEL)

    trading = create_trading_client(
        Config.BETFAIR_USERNAME, Config.BETFAIR_PASSWORD, Config.BETFAIR_APP_KEY, Config.CERT_PATH
    )
    framework = create_framework(trading, paper_trade=True, place_orders=False)

    if not args.no_db:
        storage = TickStorage(Config.DATABASE_URL)
        storage.init_schema()
        framework.add_strategy(
            TickLoggerStrategy(
                storage=storage,
                include_in_play=args.in_play,
                stream=price_stream(args.countries),
            )
        )
    if not args.no_raw:
        framework.add_strategy(
            RawStreamRecorder(Config.STREAM_DIR, stream=recording_stream(args.countries))
        )

    logger.info("Recording {} horse racing WIN markets. Ctrl+C to stop.", args.countries)
    framework.run()


if __name__ == "__main__":
    main()
