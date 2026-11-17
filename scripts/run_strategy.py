#!/usr/bin/env python3
"""Run the momentum strategy against the live Betfair stream.

By default this PAPER TRADES: prices are real, but orders are matched by a
local simulator and never reach the exchange.

Real orders are only sent when both of these are true:
  * the --live flag is given
  * the environment variable BETFAIR_LIVE_TRADING is set to "yes"

Usage:
    python scripts/run_strategy.py                      # paper trading
    python scripts/run_strategy.py --stake 2 --threshold 0.04
    BETFAIR_LIVE_TRADING=yes python scripts/run_strategy.py --live --stake 2
"""

import argparse
import os
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from src.config import Config
from src.flumine_setup import create_framework, create_trading_client, price_stream
from src.logging_setup import setup_logging
from src.strategies.momentum import MomentumConfig, MomentumStrategy

MAX_LIVE_STAKE = 5.0


def parse_args() -> argparse.Namespace:
    defaults = MomentumConfig()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="Send real orders (see above)")
    parser.add_argument("--countries", nargs="+", default=["GB"])
    parser.add_argument("--stake", type=float, default=defaults.stake)
    parser.add_argument("--lookback", type=float, default=defaults.lookback_seconds)
    parser.add_argument("--threshold", type=float, default=defaults.entry_threshold)
    parser.add_argument("--target-ticks", type=int, default=defaults.target_ticks)
    parser.add_argument("--stop-ticks", type=int, default=defaults.stop_ticks)
    return parser.parse_args()


def live_trading_allowed(live_flag: bool, stake: float) -> bool:
    """Decide whether real orders may be sent; exits on an unsafe request."""
    if not live_flag:
        return False
    if os.getenv("BETFAIR_LIVE_TRADING", "").lower() != "yes":
        raise SystemExit("--live also requires BETFAIR_LIVE_TRADING=yes in the environment.")
    if stake > MAX_LIVE_STAKE:
        raise SystemExit(
            f"Live stake {stake} exceeds the built-in limit of {MAX_LIVE_STAKE}. "
            "Raise MAX_LIVE_STAKE in scripts/run_strategy.py deliberately if you mean it."
        )
    return True


def main() -> None:
    args = parse_args()
    live = live_trading_allowed(args.live, args.stake)
    setup_logging(log_dir=Config.LOG_DIR, log_level=Config.LOG_LEVEL)

    config = MomentumConfig(
        stake=args.stake,
        lookback_seconds=args.lookback,
        entry_threshold=args.threshold,
        target_ticks=args.target_ticks,
        stop_ticks=args.stop_ticks,
    )
    trading = create_trading_client(
        Config.BETFAIR_USERNAME, Config.BETFAIR_PASSWORD, Config.BETFAIR_APP_KEY, Config.CERT_PATH
    )
    framework = create_framework(trading, paper_trade=not live)
    framework.add_strategy(MomentumStrategy(config=config, stream=price_stream(args.countries)))

    if live:
        logger.warning("LIVE TRADING: real orders will be placed, stake {}", config.stake)
    else:
        logger.info("Paper trading: no orders will reach the exchange.")
    framework.run()


if __name__ == "__main__":
    main()
