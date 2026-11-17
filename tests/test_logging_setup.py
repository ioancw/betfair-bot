"""Tests for the logging setup module."""

import logging

import pytest
from loguru import logger

from src.logging_setup import setup_logging


@pytest.fixture(autouse=True)
def reset_logging():
    """Remove loguru sinks and stdlib handlers so tests don't leak into each other."""
    root = logging.getLogger()
    original_handlers, original_level = root.handlers[:], root.level
    yield
    logger.remove()
    root.handlers[:] = original_handlers
    root.setLevel(original_level)


def _log_text(log_dir) -> str:
    logger.complete()  # flush the enqueued file sink
    return "".join(p.read_text() for p in log_dir.glob("betfair_bot_*.log"))


def test_logging_creates_log_directory(tmp_path):
    """setup_logging() creates the log directory if it does not exist."""
    log_dir = tmp_path / "custom_logs"
    assert not log_dir.exists()

    setup_logging(log_dir=str(log_dir), log_level="WARNING")

    assert log_dir.exists()


def test_messages_are_written_to_log_file(tmp_path):
    """Loguru messages at or above the level reach the file sink."""
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=str(log_dir), log_level="DEBUG")

    logger.debug("hello from loguru")

    assert "hello from loguru" in _log_text(log_dir)


def test_messages_below_level_are_filtered(tmp_path):
    """Messages below the configured level are not written."""
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=str(log_dir), log_level="WARNING")

    logger.info("should be dropped")
    logger.warning("should be kept")

    text = _log_text(log_dir)
    assert "should be dropped" not in text
    assert "should be kept" in text


def test_stdlib_logging_is_routed_to_loguru(tmp_path):
    """Records from stdlib loggers (flumine, betfairlightweight) reach the file sink."""
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=str(log_dir), log_level="INFO")

    logging.getLogger("betfairlightweight.streaming").warning("stream %s", "disconnected")

    text = _log_text(log_dir)
    assert "stream disconnected" in text
    assert "WARNING" in text


def test_stdlib_exception_traceback_is_preserved(tmp_path):
    """Exceptions logged via stdlib logging keep their traceback."""
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=str(log_dir), log_level="INFO")

    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("flumine").exception("worker failed")

    text = _log_text(log_dir)
    assert "worker failed" in text
    assert "ValueError: boom" in text


def test_repeated_setup_does_not_duplicate_output(tmp_path):
    """Calling setup_logging() twice leaves a single set of sinks."""
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=str(log_dir), log_level="INFO")
    setup_logging(log_dir=str(log_dir), log_level="INFO")

    logging.getLogger("flumine").info("only once please")

    assert _log_text(log_dir).count("only once please") == 1
