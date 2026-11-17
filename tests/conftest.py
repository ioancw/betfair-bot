"""Shared pytest fixtures for the Betfair bot test suite."""

import os
import sys
from pathlib import Path

import pytest

# src.config reads these at import time; give tests harmless defaults
for _name in ("BETFAIR_USERNAME", "BETFAIR_PASSWORD", "BETFAIR_APP_KEY", "CERT_PATH"):
    os.environ.setdefault(_name, "test-value")

# Ensure src/ is importable without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    """Provide a complete set of env vars and fake cert files."""
    cert_dir = tmp_path / "certs"
    cert_dir.mkdir()
    (cert_dir / "client-2048.crt").write_text("FAKE_CERT")
    (cert_dir / "client-2048.key").write_text("FAKE_KEY")

    monkeypatch.setenv("BETFAIR_USERNAME", "test@example.com")
    monkeypatch.setenv("BETFAIR_PASSWORD", "test_password")
    monkeypatch.setenv("BETFAIR_APP_KEY", "test_app_key")
    monkeypatch.setenv("CERT_PATH", str(cert_dir))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))

    return {
        "cert_dir": cert_dir,
        "username": "test@example.com",
        "password": "test_password",
        "app_key": "test_app_key",
    }


@pytest.fixture
def mock_api_client(mock_env):
    """Return a BetfairClient with the underlying APIClient mocked out."""
    from src.betfair_client import BetfairClient

    client = BetfairClient(
        username=mock_env["username"],
        password=mock_env["password"],
        app_key=mock_env["app_key"],
        cert_path=str(mock_env["cert_dir"]),
    )
    return client


@pytest.fixture(autouse=True)
def quiet_loguru():
    """Keep loguru output out of the test run and remove sinks tests add."""
    from loguru import logger

    logger.remove()
    yield
    logger.remove()


@pytest.fixture
def sample_race(tmp_path):
    """A synthetic recording where the steamer and drifter follow through."""
    from src.sample_data import generate_race

    return str(generate_race(tmp_path / "recordings" / "1.200000001"))


@pytest.fixture
def reversal_race(tmp_path):
    """A synthetic recording where both moves reverse, so momentum loses."""
    from src.sample_data import generate_race

    return str(
        generate_race(
            tmp_path / "recordings" / "1.200000009", market_id="1.200000009", reversal=True
        )
    )
