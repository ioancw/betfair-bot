#!/usr/bin/env python3
"""Write synthetic race recordings so the backtester can be tried offline.

The prices are invented. Use this to see how the pieces work, not to judge
whether a strategy is profitable.

Usage:
    python scripts/make_sample_data.py            # 5 races into data/sample
    python scripts/make_sample_data.py --races 20 --out data/sample
"""

import argparse
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sample_data import generate_race


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--races", type=int, default=5)
    parser.add_argument("--out", default="data/sample")
    args = parser.parse_args()

    first_start = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    for index in range(args.races):
        market_id = f"1.{200000001 + index}"
        path = generate_race(
            Path(args.out) / market_id,
            market_id=market_id,
            start=first_start + timedelta(minutes=30 * index),
            seed=index,
            # Every third race the move reverses, so some trades lose
            reversal=index % 3 == 2,
        )
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
