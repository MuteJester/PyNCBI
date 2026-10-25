"""Centralized Activity, Progress, State & Verbose Reporting Engine for PyNCBI.

Provides a unified, thread-safe reporting system across the Python API, CLI (`pyncbi`),
and external integrations so users are never in the dark during network queries,
metadata parsing, cohort filtering, or large file downloads.

Key Features:
- **Multi-Level Verbosity (`Verbosity`)**:
  - `SILENT (0)`: Zero stderr/stdout output (used in MCP stdio server and `--quiet` mode)
  - `NORMAL (1)`: Interactive progress bars (`tqdm` on stderr) for downloads and multi-item tasks
  - `VERBOSE (2)`: Step-by-step live state reporting (`▸ starting`, `· step`, `✓ completed (142ms)`)
  - `DEBUG (3)`: Full HTTP request/response trace, rate-limiter waits, and cache operations
- **Stderr Isolation**: All visual status lines and progress bars render strictly to `sys.stderr`,
  keeping `sys.stdout` 100% clean for `--json`, `--markdown`, FASTA, and shell pipelines.
- **Byte-Stream & Task Progress Bars**: Built-in `tqdm` byte transfer bars (`MB/s`, `%`, `ETA`,
  resumable offset) and iterable trackers.
- **Structured Event Hooks**: External callers (UI apps, notebooks, agent harnesses) can register
  callbacks via `reporter.subscribe(fn)` or inspect `reporter.recent_events()`.
"""

from __future__ import annotations

import collections
import contextlib
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from tqdm.auto import tqdm

from PyNCBI.core.config import get_config
from PyNCBI.core.logging import LogLevel, get_logger, set_level

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

logger = get_logger(__name__)


class Verbosity(IntEnum):
    """Verbosity levels for PyNCBI's centralized reporter."""

    SILENT = 0
    NORMAL = 1
    VERBOSE = 2
    DEBUG = 3


@dataclass
class ActivityEvent:
    """Structured state/progress event emitted by PyNCBI operations."""

    phase: str  # 'start', 'step', 'progress', 'warning', 'done', 'error'
    domain: str  # e.g., 'GEO', 'ENTREZ', 'HTTP', 'CACHE', 'DOWNLOAD'
    operation: str  # e.g., 'inspect_accession', 'extract_gse_samples', 'download_file'
    message: str
    target: str = ""
    current: int | None = None
    total: int | None = None
    elapsed_ms: float | None = None
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "domain": self.domain,
            "operation": self.operation,
            "target": self.target,
            "message": self.message,
            "current": self.current,
            "total": self.total,
            "elapsed_ms": round(self.elapsed_ms, 1) if self.elapsed_ms is not None else None,
            "metadata": self.metadata,
        }


class ByteProgressTracker:
    """Context-managed byte download progress bar writing strictly to `sys.stderr`."""

    def __init__(
        self,
        reporter: ActivityReporter,
        description: str,
        total_bytes: int = 0,
        initial_bytes: int = 0,
    ) -> None:
        self._reporter = reporter
        self._description = description
        self._total = max(0, total_bytes)
        self._initial = max(0, initial_bytes)
        self._current = self._initial
        self._t0 = time.monotonic()
        self._bar: Any | None = None

        show_bar = self._reporter.should_show_progress_bar()
        if show_bar:
            self._bar = tqdm(
                total=self._total if self._total > 0 else None,
                initial=self._initial,
                desc=description,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                dynamic_ncols=True,
                file=sys.stderr,
                leave=False,
            )
        elif self._reporter.verbosity >= Verbosity.VERBOSE:
            size_note = f" ({self._format_bytes(self._total)})" if self._total > 0 else ""
            resume_note = (
                f" [resuming from {self._format_bytes(self._initial)}]" if self._initial > 0 else ""
            )
            self._reporter.step(
                "DOWNLOAD",
                f"Starting download: {description}{size_note}{resume_note}",
            )

    @staticmethod
    def _format_bytes(num_bytes: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if num_bytes < 1024.0 or unit == "TB":
                return f"{int(num_bytes)} B" if unit == "B" else f"{num_bytes:.1f} {unit}"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} TB"

    @property
    def current_bytes(self) -> int:
        """Bytes received so far (including any resumed offset)."""
        return int(self._current)

    def update(self, chunk_bytes: int) -> None:
        """Advance progress by `chunk_bytes`."""
        if chunk_bytes <= 0:
            return
        self._current += chunk_bytes
        if self._bar is not None:
            self._bar.update(chunk_bytes)

    def set_current(self, current_bytes: int, total_bytes: int = 0) -> None:
        """Update progress to an absolute `current_bytes` position."""
        if total_bytes > 0 and self._total != total_bytes:
            self._total = total_bytes
            if self._bar is not None:
                self._bar.total = total_bytes
        delta = current_bytes - self._current
        if delta > 0:
            self.update(delta)

    def close(self) -> None:
        elapsed = max(0.001, time.monotonic() - self._t0)
        if self._bar is not None:
            self._bar.close()
            self._bar = None
        if self._reporter.verbosity >= Verbosity.VERBOSE:
            transferred = max(0, self._current - self._initial)
            speed = self._format_bytes(transferred / elapsed) + "/s"
            self._reporter.done(
                "DOWNLOAD",
                f"Downloaded {self._description} ({self._format_bytes(self._current)} at {speed})",
                elapsed_ms=elapsed * 1000.0,
            )

    def __enter__(self) -> ByteProgressTracker:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class TaskScope:
    """Context manager representing a high-level PyNCBI operation."""

    def __init__(
        self,
        reporter: ActivityReporter,
        domain: str,
        operation: str,
        message: str,
        target: str = "",
    ) -> None:
        self._reporter = reporter
        self.domain = domain
        self.operation = operation
        self.message = message
        self.target = target
        self._t0 = time.monotonic()
        self._completed_msg: str | None = None

    def step(self, message: str, *, current: int | None = None, total: int | None = None) -> None:
        """Report an intermediate sub-step within this task."""
        self._reporter.step(
            self.domain,
            message,
            operation=self.operation,
            target=self.target,
            current=current,
            total=total,
        )

    def complete(self, summary_message: str) -> None:
        """Set a custom completion message when the task finishes."""
        self._completed_msg = summary_message

    def __enter__(self) -> TaskScope:
        self._t0 = time.monotonic()
        self._reporter._emit(
            ActivityEvent(
                phase="start",
                domain=self.domain,
                operation=self.operation,
                target=self.target,
                message=self.message,
            )
        )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        elapsed_ms = (time.monotonic() - self._t0) * 1000.0
        if exc_val is not None:
            self._reporter._emit(
                ActivityEvent(
                    phase="error",
                    domain=self.domain,
                    operation=self.operation,
                    target=self.target,
                    message=f"{self.message} failed: {exc_val}",
                    elapsed_ms=elapsed_ms,
                )
            )
        else:
            done_msg = self._completed_msg or f"{self.message} complete"
            self._reporter._emit(
                ActivityEvent(
                    phase="done",
                    domain=self.domain,
                    operation=self.operation,
                    target=self.target,
                    message=done_msg,
                    elapsed_ms=elapsed_ms,
                )
            )


class ActivityReporter:
    """Centralized, thread-safe status, progress, and verbose reporter for PyNCBI."""

    _ANSI_CYAN = "\033[36m"
    _ANSI_GREEN = "\033[32m"
    _ANSI_YELLOW = "\033[33m"
    _ANSI_RED = "\033[31m"
    _ANSI_DIM = "\033[2m"
    _ANSI_BOLD = "\033[1m"
    _ANSI_RESET = "\033[0m"

    def __init__(self, verbosity: Verbosity | None = None) -> None:
        self._lock = threading.Lock()
        self._verbosity = verbosity if verbosity is not None else self._detect_default_verbosity()
        self._events: collections.deque[ActivityEvent] = collections.deque(maxlen=250)
        self._subscribers: list[Callable[[ActivityEvent], None]] = []

    @staticmethod
    def _detect_default_verbosity() -> Verbosity:
        env_v = os.environ.get("PYNCBI_VERBOSE", "").strip().lower()
        if env_v in ("2", "debug", "trace"):
            return Verbosity.DEBUG
        if env_v in ("1", "true", "yes", "verbose"):
            return Verbosity.VERBOSE
        if env_v in ("0", "silent", "quiet"):
            return Verbosity.SILENT

        env_log = os.environ.get("PYNCBI_LOG_LEVEL", "").strip().upper()
        if env_log == "DEBUG":
            return Verbosity.DEBUG
        if env_log == "VERBOSE":
            return Verbosity.VERBOSE
        if env_log in ("SILENT", "QUIET", "ERROR"):
            return Verbosity.SILENT

        if get_config().agent_mode:
            return Verbosity.SILENT
        return Verbosity.NORMAL

    @property
    def verbosity(self) -> Verbosity:
        return self._verbosity

    def set_verbosity(self, level: Verbosity | int) -> None:
        """Set the active verbosity level and synchronize the root logger."""
        resolved = Verbosity(max(0, min(3, int(level))))
        self._verbosity = resolved
        if resolved == Verbosity.SILENT:
            set_level(LogLevel.SILENT)
        elif resolved == Verbosity.NORMAL:
            set_level(LogLevel.WARNING)
        elif resolved == Verbosity.VERBOSE:
            set_level(LogLevel.INFO)
        else:
            set_level(LogLevel.DEBUG)

    def should_show_progress_bar(self) -> bool:
        """Return True if interactive `tqdm` progress bars should be drawn on `sys.stderr`."""
        if self._verbosity == Verbosity.SILENT:
            return False
        if get_config().agent_mode and self._verbosity < Verbosity.VERBOSE:
            return False
        return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

    @staticmethod
    def _supports_color() -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("FORCE_COLOR"):
            return True
        return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

    def subscribe(self, callback: Callable[[ActivityEvent], None]) -> Callable[[], None]:
        """Register an event listener callback. Returns an unsubscribe function."""
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock, contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return _unsubscribe

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent structured activity events."""
        with self._lock:
            return [ev.to_dict() for ev in list(self._events)[-limit:]]

    def clear_events(self) -> None:
        """Clear recorded activity events."""
        with self._lock:
            self._events.clear()

    def task(
        self,
        domain: str,
        operation: str,
        message: str,
        *,
        target: str = "",
    ) -> TaskScope:
        """Create a context-managed task scope that reports start, steps, and completion timing."""
        return TaskScope(
            self,
            domain=domain.upper(),
            operation=operation,
            message=message,
            target=target,
        )

    def step(
        self,
        domain: str,
        message: str,
        *,
        operation: str = "",
        target: str = "",
        current: int | None = None,
        total: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Report an intermediate progress step."""
        self._emit(
            ActivityEvent(
                phase="step",
                domain=domain.upper(),
                operation=operation,
                target=target,
                message=message,
                current=current,
                total=total,
                metadata=metadata or {},
            )
        )

    def done(
        self,
        domain: str,
        message: str,
        *,
        operation: str = "",
        target: str = "",
        elapsed_ms: float | None = None,
    ) -> None:
        """Report completion of an operation."""
        self._emit(
            ActivityEvent(
                phase="done",
                domain=domain.upper(),
                operation=operation,
                target=target,
                message=message,
                elapsed_ms=elapsed_ms,
            )
        )

    def warn(
        self,
        domain: str,
        message: str,
        *,
        operation: str = "",
        target: str = "",
    ) -> None:
        """Report a non-fatal warning (e.g., rate-limit pause, retry, or fallback)."""
        self._emit(
            ActivityEvent(
                phase="warning",
                domain=domain.upper(),
                operation=operation,
                target=target,
                message=message,
            )
        )

    def download_bar(
        self,
        description: str,
        total_bytes: int = 0,
        initial_bytes: int = 0,
    ) -> ByteProgressTracker:
        """Create a byte-level download progress bar on `sys.stderr`."""
        return ByteProgressTracker(
            self,
            description=description,
            total_bytes=total_bytes,
            initial_bytes=initial_bytes,
        )

    def track(
        self,
        iterable: Iterable[Any],
        description: str,
        *,
        total: int | None = None,
        unit: str = "item",
    ) -> Iterator[Any]:
        """Wrap an iterable with a `tqdm` progress bar on `sys.stderr` when appropriate."""
        if self.should_show_progress_bar():
            yield from tqdm(
                iterable,
                desc=description,
                total=total,
                unit=unit,
                dynamic_ncols=True,
                file=sys.stderr,
                leave=False,
            )
        else:
            if self._verbosity >= Verbosity.VERBOSE:
                total_str = f" ({total} {unit}s)" if total is not None else ""
                self.step("PROGRESS", f"{description}{total_str}")
            yield from iterable

    def _emit(self, event: ActivityEvent) -> None:
        with self._lock:
            self._events.append(event)
            subscribers = list(self._subscribers)

        for sub in subscribers:
            with contextlib.suppress(Exception):
                sub(event)

        if self._verbosity == Verbosity.SILENT:
            return

        # Warning and error events show in NORMAL+, start/step/done show in VERBOSE+
        should_print = (
            event.phase in ("warning", "error") and self._verbosity >= Verbosity.NORMAL
        ) or (self._verbosity >= Verbosity.VERBOSE)

        if should_print:
            self._write_stderr_line(event)

    def _write_stderr_line(self, event: ActivityEvent) -> None:
        use_color = self._supports_color()
        progress_str = ""
        if event.current is not None and event.total is not None and event.total > 0:
            progress_str = f" [{event.current}/{event.total}]"

        timing_str = ""
        if event.elapsed_ms is not None:
            if event.elapsed_ms >= 1000.0:
                timing_str = f" ({event.elapsed_ms / 1000.0:.2f}s)"
            else:
                timing_str = f" ({event.elapsed_ms:.0f}ms)"

        tag = f"[{event.domain}]"
        if use_color:
            if event.phase == "start":
                line = f"{self._ANSI_CYAN}▸ {self._ANSI_BOLD}{tag}{self._ANSI_RESET} {event.message}{progress_str}\n"
            elif event.phase == "step":
                line = (
                    f"{self._ANSI_CYAN}  · {tag}{self._ANSI_RESET} {event.message}{progress_str}\n"
                )
            elif event.phase == "done":
                line = (
                    f"{self._ANSI_GREEN}✓ {self._ANSI_BOLD}{tag}{self._ANSI_RESET} "
                    f"{event.message}{self._ANSI_DIM}{timing_str}{self._ANSI_RESET}\n"
                )
            elif event.phase == "warning":
                line = f"{self._ANSI_YELLOW}⚠ {self._ANSI_BOLD}{tag}{self._ANSI_RESET} {event.message}\n"
            else:
                line = f"{self._ANSI_RED}✗ {self._ANSI_BOLD}{tag}{self._ANSI_RESET} {event.message}{timing_str}\n"
        else:
            symbol = {
                "start": "->",
                "step": " .",
                "done": "OK",
                "warning": "WARN",
                "error": "ERR",
            }.get(event.phase, "--")
            line = f"{symbol} {tag} {event.message}{progress_str}{timing_str}\n"

        with contextlib.suppress(Exception):
            sys.stderr.write(line)
            sys.stderr.flush()


_default_reporter: ActivityReporter | None = None


def get_reporter() -> ActivityReporter:
    """Get the global shared `ActivityReporter` singleton."""
    global _default_reporter
    if _default_reporter is None:
        _default_reporter = ActivityReporter()
    return _default_reporter


@contextlib.contextmanager
def verbosity_scope(level: Verbosity | int) -> Iterator[ActivityReporter]:
    """Context manager to temporarily override PyNCBI reporter verbosity."""
    reporter = get_reporter()
    prev = reporter.verbosity
    reporter.set_verbosity(level)
    try:
        yield reporter
    finally:
        reporter.set_verbosity(prev)


__all__ = [
    "Verbosity",
    "ActivityEvent",
    "ByteProgressTracker",
    "TaskScope",
    "ActivityReporter",
    "get_reporter",
    "verbosity_scope",
]
