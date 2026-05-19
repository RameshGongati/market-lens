"""Logging setup — logs to both file and console."""

import logging
import sys
from pathlib import Path

_APP_LOG_DIR = Path.home() / ".market-lens" / "logs"
_LOG_FILE = _APP_LOG_DIR / "app.log"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _configure_root_logger() -> None:
    """Set up file and console handlers on the root logger (once)."""
    global _configured
    if _configured:
        return

    _APP_LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(console_handler)
    else:
        root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, ensuring the root logger is configured.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A configured Logger instance.
    """
    _configure_root_logger()
    return logging.getLogger(name)
