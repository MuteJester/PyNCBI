"""Per-client request telemetry and the optional redacted audit log."""

from __future__ import annotations

import collections
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from PyNCBI.core.exceptions import redact_url
from PyNCBI.core.invocation import record_request
from PyNCBI.core.logging import get_logger
from PyNCBI.io.urls import format_size_human

if TYPE_CHECKING:
    from pathlib import Path

    from PyNCBI.io.response_cache import ResponseCache

logger = get_logger(__name__)
# One append must finish (including close) before another client opens the log.
# Windows append handles can otherwise overwrite each other's end-of-file offset.
_AUDIT_LOCK = threading.Lock()


@dataclass
class RequestEvent:
    """Telemetry record for a single outgoing NCBI HTTP request."""

    method: str
    realm: str
    url: str
    status_code: int
    elapsed_ms: float
    rate_wait_ms: float
    bytes_received: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "realm": self.realm,
            "url": self.url,
            "status_code": self.status_code,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "rate_wait_ms": round(self.rate_wait_ms, 1),
            "bytes_received": self.bytes_received,
            "error": self.error,
        }


class ClientMetrics:
    """Thread-safe telemetry aggregator for `NCBIClient`.

    When `audit_path` is set (or `PYNCBI_AUDIT_LOG` is exported) every recorded request is
    also appended to that file as one JSON object per line, with API keys redacted, so an
    operator can audit exactly what an autonomous agent asked NCBI for.
    """

    def __init__(self, max_recent: int = 100, audit_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._audit_path = audit_path
        self._recent: collections.deque[RequestEvent] = collections.deque(maxlen=max_recent)
        self._total_requests = 0
        self._total_errors = 0
        self._total_retries = 0
        self._total_bytes = 0
        self._total_elapsed_ms = 0.0
        self._total_wait_ms = 0.0
        self._cache_hits = 0
        self._response_cache: ResponseCache | None = None
        self._by_realm: dict[str, dict[str, float | int]] = {}

    def _append_audit(self, event: RequestEvent) -> None:
        if self._audit_path is None:
            return
        entry = {
            "timestamp": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            **event.to_dict(),
        }
        entry["url"] = redact_url(str(entry.get("url", "")))
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with _AUDIT_LOCK, self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:  # pragma: no cover - auditing must never break a request
            logger.debug("Audit log write failed: %s", exc)

    def record(self, event: RequestEvent) -> None:
        """Record a completed or failed HTTP request event."""
        record_request()
        self._append_audit(event)
        with self._lock:
            self._recent.append(event)
            self._total_requests += 1
            if event.error or event.status_code >= 400:
                self._total_errors += 1
            self._total_bytes += event.bytes_received
            self._total_elapsed_ms += event.elapsed_ms
            self._total_wait_ms += event.rate_wait_ms

            r_stats = self._by_realm.setdefault(
                event.realm,
                {
                    "requests": 0,
                    "errors": 0,
                    "bytes_received": 0,
                    "total_elapsed_ms": 0.0,
                    "total_wait_ms": 0.0,
                },
            )
            r_stats["requests"] = int(r_stats["requests"]) + 1
            if event.error or event.status_code >= 400:
                r_stats["errors"] = int(r_stats["errors"]) + 1
            r_stats["bytes_received"] = int(r_stats["bytes_received"]) + event.bytes_received
            r_stats["total_elapsed_ms"] = float(r_stats["total_elapsed_ms"]) + event.elapsed_ms
            r_stats["total_wait_ms"] = float(r_stats["total_wait_ms"]) + event.rate_wait_ms

    def attach_cache(self, cache: ResponseCache) -> None:
        """Expose a client's response cache in the telemetry summary."""
        self._response_cache = cache

    def record_cache_hit(self) -> None:
        """Count a request that was served from the response cache (no NCBI traffic)."""
        with self._lock:
            self._cache_hits += 1

    def record_retry(self) -> None:
        with self._lock:
            self._total_retries += 1

    def reset(self) -> None:
        with self._lock:
            self._recent.clear()
            self._total_requests = 0
            self._total_errors = 0
            self._total_retries = 0
            self._total_bytes = 0
            self._total_elapsed_ms = 0.0
            self._total_wait_ms = 0.0
            self._cache_hits = 0
            self._by_realm.clear()

    def summary(self, include_recent: int = 10) -> dict[str, Any]:
        """Return a structured summary of client telemetry."""
        with self._lock:
            avg_ms = (
                self._total_elapsed_ms / self._total_requests if self._total_requests > 0 else 0.0
            )
            realms_out: dict[str, Any] = {}
            for realm, st in self._by_realm.items():
                reqs = int(st["requests"])
                realms_out[realm] = {
                    "requests": reqs,
                    "errors": int(st["errors"]),
                    "bytes_received": int(st["bytes_received"]),
                    "size_human": format_size_human(int(st["bytes_received"])),
                    "avg_latency_ms": round(float(st["total_elapsed_ms"]) / reqs, 1)
                    if reqs > 0
                    else 0.0,
                    "total_wait_ms": round(float(st["total_wait_ms"]), 1),
                }

            recent_list = [ev.to_dict() for ev in list(self._recent)[-include_recent:]]
            return {
                "total_requests": self._total_requests,
                "cache_hits": self._cache_hits,
                "cache": self._response_cache.stats() if self._response_cache is not None else {},
                "total_errors": self._total_errors,
                "total_retries": self._total_retries,
                "total_bytes_received": self._total_bytes,
                "total_size_human": format_size_human(self._total_bytes),
                "avg_latency_ms": round(avg_ms, 1),
                "total_rate_wait_ms": round(self._total_wait_ms, 1),
                "by_realm": realms_out,
                "recent_requests": recent_list,
            }
