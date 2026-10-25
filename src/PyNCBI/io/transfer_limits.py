"""Shared byte budgets and disk checks for every HTTP transfer."""

from __future__ import annotations

import contextlib
import functools
import inspect
import math
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import DataError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

P = ParamSpec("P")
T = TypeVar("T")


class TransferBudget:
    """An operation's byte ceiling, shared by its worker threads (zero disables)."""

    def __init__(self, max_download_mb: float, check_disk_space: bool = True) -> None:
        if not math.isfinite(max_download_mb) or max_download_mb < 0:
            raise ValueError("max_download_mb must be finite and non-negative (0 disables)")
        self.max_download_mb = max_download_mb
        self.limit = int(max_download_mb * 1024 * 1024) if max_download_mb else None
        self.check_disk_space = check_disk_space
        self.received = 0
        self._lock = threading.Lock()

    def check(self, size: int) -> None:
        """Check a declared response size without reserving or charging its bytes."""
        with self._lock:
            if self.limit is not None and self.received + size > self.limit:
                raise DataError(
                    f"Transfer exceeds max_download_mb={self.max_download_mb}",
                    hint="Raise max_download_mb / PYNCBI_MAX_DOWNLOAD_MB, or select fewer files",
                )

    def consume(self, size: int) -> None:
        """Charge actual delivered bytes; never trust HEAD or Content-Length alone."""
        with self._lock:
            if self.limit is not None and self.received + size > self.limit:
                raise DataError(
                    f"Transfer exceeds max_download_mb={self.max_download_mb}",
                    hint="Raise max_download_mb / PYNCBI_MAX_DOWNLOAD_MB, or select fewer files",
                )
            self.received += size

    def disk(self, destination: Path, size: int) -> None:
        if self.check_disk_space and shutil.disk_usage(destination.parent).free < size:
            raise DataError(
                f"Insufficient disk space at {destination.parent}: need {size} bytes",
                hint="Free space or choose another destination",
            )


_active_budget: ContextVar[TransferBudget | None] = ContextVar(
    "pyncbi_transfer_budget", default=None
)


def response_budget(config: Any) -> TransferBudget:
    """Use an enclosing operation budget, or the owning client's per-response default."""
    return _active_budget.get() or TransferBudget(float(config.max_download_mb))


@contextlib.contextmanager
def transfer_scope(
    max_download_mb: float, check_disk_space: bool = True
) -> Iterator[TransferBudget]:
    existing = _active_budget.get()
    if existing is not None:
        yield existing
        return
    budget = TransferBudget(max_download_mb, check_disk_space)
    token = _active_budget.set(budget)
    try:
        yield budget
    finally:
        _active_budget.reset(token)


def bounded_transfer(func: Callable[P, T]) -> Callable[P, T]:
    """Apply one budget across nested loaders and parallel downloads in an operation."""
    signature = inspect.signature(func)

    @functools.wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        arguments = signature.bind(*args, **kwargs).arguments
        client = arguments.get("http_client") or arguments.get("client")
        config = getattr(client, "app_config", None) or get_config()
        limit = arguments.get("max_download_mb")
        if limit is None:
            limit = config.max_download_mb
        with transfer_scope(float(limit), bool(arguments.get("check_disk_space", True))):
            return func(*args, **kwargs)

    return wrapped


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """Carry session configuration, byte budgets and invocation counters into workers."""

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Any:
        return super().submit(copy_context().run, fn, *args, **kwargs)


def read_response(response: Any, budget: TransferBudget) -> bytes:
    """Read an HTTP body incrementally with a hard byte ceiling, then close its socket."""
    response.__dict__["_pyncbi_bytes_received"] = 0
    try:
        length = response.headers.get("Content-Length", "")
        if str(length).isdigit():
            budget.check(int(length))
        chunks = []
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                response.__dict__["_pyncbi_bytes_received"] += len(chunk)
                budget.consume(len(chunk))
                chunks.append(chunk)
        return b"".join(chunks)
    finally:
        response.close()
