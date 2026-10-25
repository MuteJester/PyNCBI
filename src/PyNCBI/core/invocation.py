"""Invocation-local timing and request counts, including work delegated to threads."""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class _Invocation:
    start: float = field(default_factory=time.monotonic)
    requests: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


_current: ContextVar[_Invocation | None] = ContextVar("pyncbi_invocation", default=None)


def begin_run() -> None:
    _current.set(_Invocation())


def record_request() -> None:
    state = _current.get()
    if state is not None:
        with state.lock:
            state.requests += 1


def run_snapshot() -> tuple[float | None, int | None]:
    state = _current.get()
    if state is None:
        return None, None
    with state.lock:
        return round((time.monotonic() - state.start) * 1000.0, 1), state.requests
