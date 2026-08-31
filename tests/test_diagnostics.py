"""Tests for bounded, reusable local logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from radio_battery_monitor.diagnostics import LOG_NAME, MAX_LOG_BYTES, configure_logging

if TYPE_CHECKING:
    from pathlib import Path


def test_configure_logging_creates_one_bounded_handler(tmp_path: Path) -> None:
    """Repeated setup reuses one non-propagating rotating file logger."""
    logger = logging.getLogger(LOG_NAME)
    for existing in tuple(logger.handlers):
        existing.close()
        logger.removeHandler(existing)
    try:
        configured = configure_logging(tmp_path)
        assert configure_logging(tmp_path) is configured
        assert configured.level == logging.INFO
        assert not configured.propagate
        assert len(configured.handlers) == 1
        handler = configured.handlers[0]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == MAX_LOG_BYTES
        configured.info("safe diagnostic")
        assert (tmp_path / "radio-battery-monitor.log").read_text(encoding="utf-8").endswith("safe diagnostic\n")
    finally:
        for existing in tuple(logger.handlers):
            existing.close()
            logger.removeHandler(existing)
