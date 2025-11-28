"""Shared logging configuration for Coglet Pi utilities.

This module initializes a single global logger that honors LOGLEVEL and
LOGFILE environment variables. Use :func:`get_logger` to access the shared
logger from any module. Calling :func:`setup_logging` multiple times is safe;
the configuration will only be applied once.
"""
from __future__ import annotations

import logging
import os
from logging import Logger
from typing import Optional

_LOGGER: Optional[Logger] = None


def _determine_level() -> int:
    level_name = os.getenv("LOGLEVEL", "INFO").strip().upper()
    return logging.DEBUG if level_name == "DEBUG" else logging.INFO


def setup_logging() -> Logger:
    """Configure and return the shared logger.

    The logger writes to a file when LOGFILE is set, otherwise to stdout.
    """
    global _LOGGER

    if _LOGGER is not None:
        return _LOGGER

    level = _determine_level()
    logfile = os.getenv("LOGFILE")

    handlers: list[logging.Handler]
    if logfile:
        handlers = [logging.FileHandler(logfile)]
    else:
        handlers = [logging.StreamHandler()]

    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )

    _LOGGER = logging.getLogger("voiceassistant")
    _LOGGER.setLevel(level)
    _LOGGER.debug("Logging initialized with level=%s output=%s", logging.getLevelName(level), logfile or "stdout")
    return _LOGGER


def get_logger() -> Logger:
    """Return the shared logger, initializing it if necessary."""
    global _LOGGER

    if _LOGGER is None:
        return setup_logging()
    return _LOGGER


__all__ = ["get_logger", "setup_logging"]
