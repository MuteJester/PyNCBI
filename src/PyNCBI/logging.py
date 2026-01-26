"""Logging configuration for PyNCBI.

This module provides a professional logging system with configurable
verbosity levels, colored output, and structured formatting.

Usage:
    from PyNCBI.logging import get_logger, configure_logging, LogLevel

    # Get a logger for your module
    logger = get_logger(__name__)
    logger.info("Fetching GSM data")
    logger.debug("Cache hit for GSM123456")

    # Configure logging level
    configure_logging(LogLevel.DEBUG)  # Show all messages
    configure_logging(LogLevel.WARNING)  # Only warnings and errors

    # Or use environment variable
    # PYNCBI_LOG_LEVEL=DEBUG python script.py

Example:
    # In user code
    import PyNCBI
    from PyNCBI.logging import configure_logging, LogLevel

    # Enable verbose output
    configure_logging(LogLevel.DEBUG)

    # Now operations will show detailed progress
    gsm = PyNCBI.GSM('GSM1234567')
"""

from __future__ import annotations

import logging
import os
import sys
from enum import IntEnum
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    pass


class LogLevel(IntEnum):
    """Log levels for PyNCBI.

    Levels (from most to least verbose):
        DEBUG: Detailed diagnostic information
        INFO: General operational messages (default)
        WARNING: Something unexpected but not an error
        ERROR: A failure that prevented an operation
        CRITICAL: A serious failure requiring immediate attention
        SILENT: No output at all
    """

    DEBUG = logging.DEBUG  # 10
    INFO = logging.INFO  # 20
    WARNING = logging.WARNING  # 30
    ERROR = logging.ERROR  # 40
    CRITICAL = logging.CRITICAL  # 50
    SILENT = 100  # No output


class ColoredFormatter(logging.Formatter):
    """Formatter that adds colors to log output for terminals."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[1;31m",  # Bold Red
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    def __init__(self, fmt: str | None = None, use_colors: bool = True) -> None:
        super().__init__(fmt)
        self.use_colors = use_colors and _supports_color()

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with optional colors."""
        # Save original values
        orig_msg = record.msg
        orig_levelname = record.levelname

        if self.use_colors:
            color = self.COLORS.get(record.levelname, "")
            record.levelname = f"{color}{record.levelname}{self.RESET}"

            # Color the message based on level
            if record.levelno >= logging.ERROR:
                record.msg = f"{self.COLORS['ERROR']}{record.msg}{self.RESET}"
            elif record.levelno >= logging.WARNING:
                record.msg = f"{self.COLORS['WARNING']}{record.msg}{self.RESET}"

        result = super().format(record)

        # Restore original values
        record.msg = orig_msg
        record.levelname = orig_levelname

        return result


class ProgressFormatter(logging.Formatter):
    """Formatter optimized for progress messages."""

    def __init__(self, use_colors: bool = True) -> None:
        super().__init__()
        self.use_colors = use_colors and _supports_color()

    def format(self, record: logging.LogRecord) -> str:
        """Format progress messages cleanly."""
        if self.use_colors:
            if record.levelno >= logging.ERROR:
                return f"\033[31m✗ {record.msg}\033[0m"
            elif record.levelno >= logging.WARNING:
                return f"\033[33m⚠ {record.msg}\033[0m"
            elif record.levelno == logging.INFO:
                return f"\033[32m✓ {record.msg}\033[0m"
            else:
                return f"\033[36m· {record.msg}\033[0m"
        else:
            if record.levelno >= logging.ERROR:
                return f"[ERROR] {record.msg}"
            elif record.levelno >= logging.WARNING:
                return f"[WARN] {record.msg}"
            elif record.levelno == logging.INFO:
                return f"[INFO] {record.msg}"
            else:
                return f"[DEBUG] {record.msg}"


def _supports_color() -> bool:
    """Check if the terminal supports colors."""
    # Check for NO_COLOR environment variable (standard)
    if os.environ.get("NO_COLOR"):
        return False

    # Check for FORCE_COLOR environment variable
    if os.environ.get("FORCE_COLOR"):
        return True

    # Check if stdout is a TTY
    if not hasattr(sys.stdout, "isatty"):
        return False

    if not sys.stdout.isatty():
        return False

    # Check platform
    if sys.platform == "win32":
        # Windows 10+ supports ANSI colors
        return os.environ.get("TERM") or os.environ.get("WT_SESSION")

    return True


def _get_default_level() -> int:
    """Get default log level from environment or use INFO."""
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


# Package-level logger
_ROOT_LOGGER_NAME = "PyNCBI"
_logger: logging.Logger | None = None
_configured = False


def _setup_logger() -> logging.Logger:
    """Set up the PyNCBI root logger."""
    global _configured

    logger = logging.getLogger(_ROOT_LOGGER_NAME)

    if not _configured:
        logger.setLevel(_get_default_level())

        # Remove existing handlers
        logger.handlers.clear()

        # Add console handler with colored formatter
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.DEBUG)

        # Use detailed format for DEBUG, simple for others
        fmt = "%(levelname)-8s | %(name)s | %(message)s"
        handler.setFormatter(ColoredFormatter(fmt))

        logger.addHandler(handler)

        # Don't propagate to root logger
        logger.propagate = False

        _configured = True

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Get a logger for PyNCBI.

    Args:
        name: Logger name (typically __name__). If None, returns root PyNCBI logger.

    Returns:
        Configured logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("Processing GSM data")
    """
    _setup_logger()

    if name is None:
        return logging.getLogger(_ROOT_LOGGER_NAME)

    # Create child logger under PyNCBI namespace
    if name.startswith("PyNCBI"):
        return logging.getLogger(name)
    else:
        return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


def configure_logging(
    level: LogLevel | int = LogLevel.INFO,
    *,
    stream: TextIO | None = None,
    format_style: str = "standard",
    use_colors: bool | None = None,
) -> None:
    """Configure PyNCBI logging.

    Args:
        level: Log level (use LogLevel enum or int)
        stream: Output stream (default: stderr)
        format_style: 'standard', 'minimal', or 'detailed'
        use_colors: Force colors on/off (None for auto-detect)

    Example:
        # Enable verbose output
        configure_logging(LogLevel.DEBUG)

        # Quiet mode (errors only)
        configure_logging(LogLevel.ERROR)

        # Disable colors
        configure_logging(LogLevel.INFO, use_colors=False)
    """
    global _configured

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(level)

    # Remove existing handlers
    logger.handlers.clear()

    # Determine output stream
    output = stream or sys.stderr

    # Determine color support
    colors = use_colors if use_colors is not None else _supports_color()

    # Create handler
    handler = logging.StreamHandler(output)
    handler.setLevel(logging.DEBUG)

    # Select formatter based on style
    if format_style == "minimal":
        handler.setFormatter(ProgressFormatter(use_colors=colors))
    elif format_style == "detailed":
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
        handler.setFormatter(ColoredFormatter(fmt, use_colors=colors))
    else:  # standard
        fmt = "%(levelname)-8s | %(name)s | %(message)s"
        handler.setFormatter(ColoredFormatter(fmt, use_colors=colors))

    logger.addHandler(handler)
    logger.propagate = False

    _configured = True


def set_level(level: LogLevel | int) -> None:
    """Set the logging level.

    Convenience function to change level without full reconfiguration.

    Args:
        level: New log level
    """
    logger = get_logger()
    logger.setLevel(level)


def silence() -> None:
    """Silence all PyNCBI logging output.

    Useful for library usage where you don't want any console output.
    """
    set_level(LogLevel.SILENT)


def verbose() -> None:
    """Enable verbose (DEBUG) logging.

    Useful for debugging issues with data fetching.
    """
    set_level(LogLevel.DEBUG)


# Context manager for temporary log level changes
class log_level:
    """Context manager to temporarily change log level.

    Example:
        with log_level(LogLevel.DEBUG):
            # Verbose output in this block
            gsm = GSM('GSM123456')
        # Back to normal level
    """

    def __init__(self, level: LogLevel | int) -> None:
        self.level = level
        self.previous_level: int | None = None

    def __enter__(self) -> "log_level":
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
