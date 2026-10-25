"""Short-lived in-memory response cache for `NCBIClient`.

Purpose: an agent that calls `inspect` then `metadata` then `expression` on the same accession
should not re-download the same SOFT record three times. This is a deduplication cache, not a
persistent one: entries are bounded, expire quickly, and live only in the owning client, so
NCBI sees one request per distinct URL per short window.

Never used for job/status endpoints (BLAST polling), file bodies, byte ranges or streams; those
callers must always see current state.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

DEFAULT_TTL_SECONDS = 300.0
DEFAULT_MAX_ENTRIES = 256
DEFAULT_MAX_ENTRY_BYTES = 8 * 1024 * 1024


@dataclass
class _Entry:
    value: Any
    expires_at: float
    size_bytes: int


class ResponseCache:
    """Thread-safe TTL + LRU cache with an entry-size ceiling and hit/miss telemetry."""

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.max_entry_bytes = max(0, int(max_entry_bytes))
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._stored_bytes = 0

    def get(self, key: str) -> Any | None:
        """Return the cached value, or None when absent or expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expires_at <= now:
                del self._entries[key]
                self._stored_bytes -= entry.size_bytes
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, *, ttl: float, size_bytes: int = 0) -> bool:
        """Store a value; returns False when it was refused (ttl<=0 or too large)."""
        if ttl <= 0:
            return False
        if self.max_entry_bytes and size_bytes > self.max_entry_bytes:
            return False
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._stored_bytes -= existing.size_bytes
            self._entries[key] = _Entry(
                value=value, expires_at=time.monotonic() + ttl, size_bytes=size_bytes
            )
            self._stored_bytes += size_bytes
            while len(self._entries) > self.max_entries:
                _, dropped = self._entries.popitem(last=False)
                self._stored_bytes -= dropped.size_bytes
                self._evictions += 1
        return True

    def clear(self) -> None:
        """Drop every entry (counters are kept)."""
        with self._lock:
            self._entries.clear()
            self._stored_bytes = 0

    def stats(self) -> dict[str, Any]:
        """Entry count, bytes held, and hit/miss/eviction counters."""
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": max(0, self._stored_bytes),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "max_entries": self.max_entries,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __repr__(self) -> str:
        stats = self.stats()
        return f"ResponseCache(entries={stats['entries']}/{self.max_entries}, hits={stats['hits']})"
