"""Logging configuration for PyNCBI.

Provides a clean logging system with configurable verbosity levels,
colored terminal output, and silent mode for AI agents / MCP.
"""

from __future__ import annotations

import logging
import os
import sys
from enum import IntEnum
from typing import TextIO


class LogLevel(IntEnum):
    """Log levels for PyNCBI."""

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL
    SILENT = 100


class ColoredFormatter(logging.Formatter):
    """Formatter that adds colors to log output for terminals."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[1;31m",
    }
    RESET = "\033[0m"

    def __init__(self, fmt: str | None = None, use_colors: bool = True) -> None:
        super().__init__(fmt)
        self.use_colors = use_colors and _supports_color()

    def format(self, record: logging.LogRecord) -> str:
        orig_msg = record.msg
        orig_levelname = record.levelname

        if self.use_colors:
            color = self.COLORS.get(record.levelname, "")
            record.levelname = f"{color}{record.levelname}{self.RESET}"
            if record.levelno >= logging.ERROR:
                record.msg = f"{self.COLORS['ERROR']}{record.msg}{self.RESET}"
            elif record.levelno >= logging.WARNING:
                record.msg = f"{self.COLORS['WARNING']}{record.msg}{self.RESET}"

        result = super().format(record)
        record.msg = orig_msg
        record.levelname = orig_levelname
        return result


class ProgressFormatter(logging.Formatter):
    """Formatter optimized for progress messages."""

    def __init__(self, use_colors: bool = True) -> None:
        super().__init__()
        self.use_colors = use_colors and _supports_color()

    def format(self, record: logging.LogRecord) -> str:
        if self.use_colors:
            if record.levelno >= logging.ERROR:
                return f"\033[31m✗ {record.msg}\033[0m"
            if record.levelno >= logging.WARNING:
                return f"\033[33m⚠ {record.msg}\033[0m"
            if record.levelno == logging.INFO:
                return f"\033[32m✓ {record.msg}\033[0m"
            return f"\033[36m· {record.msg}\033[0m"
        if record.levelno >= logging.ERROR:
            return f"[ERROR] {record.msg}"
        if record.levelno >= logging.WARNING:
            return f"[WARN] {record.msg}"
        if record.levelno == logging.INFO:
            return f"[INFO] {record.msg}"
        return f"[DEBUG] {record.msg}"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        return bool(os.environ.get("TERM") or os.environ.get("WT_SESSION"))
    return True


def _get_default_level() -> int:
    env_level = os.environ.get("PYNCBI_LOG_LEVEL", "").upper()
    level_map = {
        "DEBUG": LogLevel.DEBUG,
        "INFO": LogLevel.INFO,
        "WARNING": LogLevel.WARNING,
        "WARN": LogLevel.WARNING,
        "ERROR": LogLevel.ERROR,
        "CRITICAL": LogLevel.CRITICAL,
        "SILENT": LogLevel.SILENT,
        "QUIET": LogLevel.WARNING,
        "VERBOSE": LogLevel.DEBUG,
    }
    return level_map.get(env_level, LogLevel.INFO)


_ROOT_LOGGER_NAME = "PyNCBI"
_configured = False


def _setup_logger() -> logging.Logger:
    global _configured
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    if not _configured:
        logger.setLevel(_get_default_level())
        logger.handlers.clear()
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.DEBUG)
        fmt = "%(levelname)-8s | %(name)s | %(message)s"
        handler.setFormatter(ColoredFormatter(fmt))
        logger.addHandler(handler)
        logger.propagate = False
        _configured = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Get a logger under the PyNCBI namespace."""
    _setup_logger()
    if name is None:
        return logging.getLogger(_ROOT_LOGGER_NAME)
    if name.startswith("PyNCBI"):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


def configure_logging(
    level: LogLevel | int = LogLevel.INFO,
    *,
    stream: TextIO | None = None,
    format_style: str = "standard",
    use_colors: bool | None = None,
) -> None:
    """Configure PyNCBI logging."""
    global _configured
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()

    output = stream or sys.stderr
    colors = use_colors if use_colors is not None else _supports_color()
    handler = logging.StreamHandler(output)
    handler.setLevel(logging.DEBUG)

    if format_style == "minimal":
        handler.setFormatter(ProgressFormatter(use_colors=colors))
    elif format_style == "detailed":
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
        handler.setFormatter(ColoredFormatter(fmt, use_colors=colors))
    else:
        fmt = "%(levelname)-8s | %(name)s | %(message)s"
        handler.setFormatter(ColoredFormatter(fmt, use_colors=colors))

    logger.addHandler(handler)
    logger.propagate = False
    _configured = True


def set_level(level: LogLevel | int) -> None:
    """Set the logging level."""
    get_logger().setLevel(level)


def silence() -> None:
    """Silence all PyNCBI logging and activity reporting output."""
    from PyNCBI.core.reporter import Verbosity, get_reporter

    set_level(LogLevel.SILENT)
    get_reporter().set_verbosity(Verbosity.SILENT)


def verbose(debug: bool = False) -> None:
    """Enable verbose step-by-step reporting (or full HTTP debug trace when `debug=True`)."""
    from PyNCBI.core.reporter import Verbosity, get_reporter

    if debug:
        set_level(LogLevel.DEBUG)
        get_reporter().set_verbosity(Verbosity.DEBUG)
    else:
        set_level(LogLevel.INFO)
        get_reporter().set_verbosity(Verbosity.VERBOSE)


class log_level:
    """Context manager to temporarily change log level."""

    def __init__(self, level: LogLevel | int) -> None:
        self.level = level
        self.previous_level: int | None = None

    def __enter__(self) -> log_level:
        logger = get_logger()
        self.previous_level = logger.level
        logger.setLevel(self.level)
        return self

    def __exit__(self, *args: object) -> None:
        if self.previous_level is not None:
            get_logger().setLevel(self.previous_level)


__all__ = [
    "LogLevel",
    "get_logger",
    "configure_logging",
    "set_level",
    "silence",
    "verbose",
    "log_level",
]
