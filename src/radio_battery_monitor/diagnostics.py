"""Bounded local diagnostic logging without raw radio identifiers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Final

from radio_battery_monitor.settings import default_settings_path

if TYPE_CHECKING:
    from pathlib import Path

LOG_NAME: Final = "radio_battery_monitor"
MAX_LOG_BYTES: Final = 1_048_576
LOG_BACKUP_COUNT: Final = 3


def configure_logging(log_directory: Path | None = None) -> logging.Logger:
    """Configure one bounded application log.

    Returns:
        The configured application logger.
    """
    logger = logging.getLogger(LOG_NAME)
    if logger.handlers:
        return logger
    directory = log_directory or default_settings_path().parent / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "radio-battery-monitor.log",
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
