"""Logging configuration for AutoLaunch.

Writes rotating logs to XDG_STATE_HOME/autolaunch/autolaunch.log without hardcoded user paths.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "autolaunch"
_CONFIGURED = False


def get_log_dir() -> Path:
    """Returns the dynamic XDG state directory for AutoLaunch logs."""
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "autolaunch"
    return Path.home() / ".local" / "state" / "autolaunch"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configures and returns the shared AutoLaunch logger."""
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME)
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    try:
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "autolaunch.log"
        handler = RotatingFileHandler(
            log_file,
            maxBytes=512 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())

    _CONFIGURED = True
    return logger


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """Returns a child or root AutoLaunch logger."""
    if not logging.getLogger(_LOGGER_NAME).handlers:
        logging.getLogger(_LOGGER_NAME).addHandler(logging.NullHandler())
    return logging.getLogger(name)
