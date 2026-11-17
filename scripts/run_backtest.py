#!/usr/bin/env python3
"""Replay recorded races through the momentum strategy and report profit.

Accepts Betfair historical data files, recordings made by
scripts/run_stream_logger.py, or synthetic files from scripts/make_sample_data.py.
No Betfair login is needed.

Usage:
    python scripts/run_backtest.py data/sample
    python scripts/run_backtest.py data/streams --stake 5 --threshold 0.04 --target-ticks 2
"""

import argparse
import logging
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest import DEFAULT_COMMISSION, find_recordings, run_backtest
from src.strategies.momentum import MomentumConfig


def parse_args() -> argparse.Namespace:
    defaults = MomentumConfig()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="Recording files or directories")
    parser.add_argument("--stake", type=float, default=defaults.stake)
    parser.add_argument("--lookback", type=float, default=defaults.lookback_seconds,
                        help="Seconds over which the price move is measured")
    parser.add_argument("--threshold", type=float, default=defaults.entry_threshold,
                        help="Minimum fractional move to trade (0.03 = 3%%)")
    parser.add_argument("--target-ticks", type=int, default=defaults.target_ticks)
    parser.add_argument("--stop-ticks", type=int, default=defaults.stop_ticks)
    parser.add_argument("--commission", type=float, default=DEFAULT_COMMISSION)
    parser.add_argument("--countries", nargs="+", default=list(defaults.country_codes),
                        help="Country codes to trade (default GB)")
    parser.add_argument("--steamers-only", action="store_true")
    parser.add_argument("--drifters-only", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="Show every order")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.steamers_only and args.drifters_only:
        raise SystemExit("Choose at most one of --steamers-only and --drifters-only.")

    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="INFO" if args.verbose else "WARNING")
    logging.getLogger("flumine").setLevel(logging.ERROR)

    config = MomentumConfig(
        stake=args.stake,
        lookback_seconds=args.lookback,
        entry_threshold=args.threshold,
        target_ticks=args.target_ticks,
        stop_ticks=args.stop_ticks,
        trade_steamers=not args.drifters_only,
        trade_drifters=not args.steamers_only,
        country_codes=tuple(args.countries),
    )
    result = run_backtest(find_recordings(args.paths), config, args.commission)
    print(result.summary())


if __name__ == "__main__":
    main()
