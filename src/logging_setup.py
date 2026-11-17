"""Loguru logging configuration — file + console with rotation."""

import inspect
import logging
import sys
from pathlib import Path

from loguru import logger


class InterceptHandler(logging.Handler):
    """Route stdlib ``logging`` records (flumine, betfairlightweight) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk back past the logging module so loguru reports the real caller
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(log_dir: str = "logs", log_level: str = "INFO") -> None:
    """Configure loguru sinks: stderr console and rotating file.

    Args:
        log_dir: Directory where log files are written.
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Remove the default loguru handler
    logger.remove()

    # Console sink — human-readable, coloured
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File sink — JSON-friendly, rotated daily, retained for 30 days
    logger.add(
        Path(log_dir) / "betfair_bot_{time:YYYY-MM-DD}.log",
        level=log_level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} — {message}"
        ),
        rotation="00:00",      # rotate at midnight
        retention="30 days",
        compression="gz",
        enqueue=True,           # async, thread-safe
    )

    # Third-party libraries log via stdlib logging; forward them to the sinks above
    logging.basicConfig(handlers=[InterceptHandler()], level=log_level, force=True)

    logger.info("Logging initialised — level={}, dir={}", log_level, log_dir)
