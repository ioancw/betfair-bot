"""Configuration loading from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

from src.constants import CERT_FILENAME, KEY_FILENAME
from src.settings import Settings

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Required environment variable '{name}' is not set. "
                               f"Check your .env file.")
    return value


class Config:
    """Central configuration object populated from environment variables."""

    BETFAIR_USERNAME: str = _require("BETFAIR_USERNAME")
    BETFAIR_PASSWORD: str = _require("BETFAIR_PASSWORD")
    BETFAIR_APP_KEY: str = _require("BETFAIR_APP_KEY")
    CERT_PATH: str = _require("CERT_PATH")

    # Credential-free settings, shared with src.settings
    LOG_LEVEL: str = Settings.LOG_LEVEL
    LOG_DIR: str = Settings.LOG_DIR
    DATABASE_URL: str = Settings.DATABASE_URL
    STREAM_DIR: str = Settings.STREAM_DIR

    @classmethod
    def cert_crt(cls) -> str:
        """Full path to the SSL certificate file."""
        return str(Path(cls.CERT_PATH) / CERT_FILENAME)

    @classmethod
    def cert_key(cls) -> str:
        """Full path to the SSL private key file."""
        return str(Path(cls.CERT_PATH) / KEY_FILENAME)
