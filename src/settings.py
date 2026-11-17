"""Settings that need no Betfair credentials (database, logging, file paths).

Scripts that never log in to Betfair, such as the backtester and the database
setup, import from here so they run without credentials configured.
"""

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")

    # Tick database (matches docker-compose.yml by default)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://betfair:betfair@localhost:5432/betfair"
    )
    # Where raw stream recordings are written for later backtesting
    STREAM_DIR: str = os.getenv("STREAM_DIR", "data/streams")
