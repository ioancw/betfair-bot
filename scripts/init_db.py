#!/usr/bin/env python3
"""Create the tick database tables.

Start the database first with `docker compose up -d`, then run:

    python scripts/init_db.py
"""

import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.logging_setup import setup_logging
from src.settings import Settings
from src.tick_storage import TickStorage


def main() -> None:
    setup_logging(log_dir=Settings.LOG_DIR, log_level=Settings.LOG_LEVEL)
    with TickStorage(Settings.DATABASE_URL) as storage:
        storage.init_schema()


if __name__ == "__main__":
    main()
