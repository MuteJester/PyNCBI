"""Realm-aware, thread-safe token-bucket rate limiters for NCBI backends.

NCBI operates distinct backend infrastructures with different rate policies:
- Entrez E-Utilities (`eutils.ncbi.nlm.nih.gov`): Strict 3 req/s without API key,
  10 req/s with `NCBI_API_KEY`.
- GEO CGI Query/Download (`www.ncbi.nlm.nih.gov/geo/*`): Dynamic CGI endpoints (~8 req/s).
- Static FTP/HTTPS Archive (`ftp.ncbi.nlm.nih.gov`): High-bandwidth static server (~20 req/s).
- BLAST URL API (`blast.ncbi.nlm.nih.gov`): Shared compute; NCBI asks for at most one request
  every 10 seconds and polling no faster than once a minute.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse


def classify_ncbi_realm(url: str) -> str:
    """Classify a URL into its NCBI rate-limiting realm.

    Returns:
        One of: 'eutils', 'blast', 'geo', 'ftp', 'ncbi', or 'external'.
    """
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if "eutils.ncbi.nlm.nih.gov" in host:
        return "eutils"
    if "blast.ncbi.nlm.nih.gov" in host:
        return "blast"
    if "ftp.ncbi.nlm.nih.gov" in host:
        return "ftp"
    if "ncbi.nlm.nih.gov" in host and "/geo/" in path:
        return "geo"
    if "ncbi.nlm.nih.gov" in host:
        return "ncbi"
    return "external"


class TokenBucketLimiter:
    """Thread-safe token-bucket rate limiter with telemetry and Retry-After support.

    Ensures outgoing requests across all threads never exceed the configured
    requests-per-second limit, and allows global pause when a Retry-After
    header or rate-limit payload (HTTP 429/503) is encountered.
    """

    def __init__(self, rate_per_sec: float = 2.8, capacity: float | None = None) -> None:
        self._rate = max(0.1, float(rate_per_sec))
        self._capacity = float(capacity) if capacity is not None else max(1.0, self._rate)
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._paused_until = 0.0
        self._total_acquired = 0
        self._total_wait_seconds = 0.0
        self._throttle_events = 0
        self._lock = threading.Lock()

    @property
    def rate_per_sec(self) -> float:
        """Configured token refill rate per second."""
        return self._rate

    @rate_per_sec.setter
    def rate_per_sec(self, value: float) -> None:
        with self._lock:
            self._rate = max(0.1, float(value))
            self._capacity = max(1.0, self._rate)

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until the requested number of tokens is available.

        Returns:
            Total time spent waiting in seconds.
        """
        waited = 0.0
        while True:
            wait_time = 0.0
            with self._lock:
                now = time.monotonic()

                # Respect global Retry-After pause
                if now < self._paused_until:
                    wait_time = self._paused_until - now
                else:
                    elapsed = now - self._last_refill
                    if elapsed > 0:
                        self._tokens = min(
                            self._capacity,
                            self._tokens + elapsed * self._rate,
                        )
                        self._last_refill = now

                    if self._tokens >= tokens:
                        self._tokens -= tokens
                        self._total_acquired += 1
                        self._total_wait_seconds += waited
                        return waited

                    deficit = tokens - self._tokens
                    wait_time = deficit / self._rate

            if wait_time > 0:
                sleep_dur = min(wait_time, 1.0)
                time.sleep(sleep_dur)
                waited += sleep_dur

    def pause(self, seconds: float) -> None:
        """Pause all threads on this bucket for `seconds` (e.g., on HTTP 429 Retry-After)."""
        if seconds <= 0:
            return
        with self._lock:
            self._throttle_events += 1
            target = time.monotonic() + seconds
            if target > self._paused_until:
                self._paused_until = target

    def stats(self) -> dict[str, Any]:
        """Return diagnostic snapshot of this rate limiter."""
        with self._lock:
            now = time.monotonic()
            paused_remaining = max(0.0, self._paused_until - now)
            return {
                "rate_per_sec": round(self._rate, 2),
                "capacity": round(self._capacity, 2),
                "total_acquired": self._total_acquired,
                "total_wait_ms": round(self._total_wait_seconds * 1000.0, 1),
                "throttle_events": self._throttle_events,
                "paused_remaining_sec": round(paused_remaining, 2),
            }


class NCBIRealmLimiter:
    """Manages independent TokenBucketLimiter instances for each NCBI backend realm."""

    def __init__(
        self,
        eutils_rate: float = 2.8,
        geo_rate: float = 8.0,
        ftp_rate: float = 20.0,
        ncbi_rate: float = 5.0,
        blast_rate: float = 0.1,
    ) -> None:
        self._buckets: dict[str, TokenBucketLimiter] = {
            "eutils": TokenBucketLimiter(rate_per_sec=eutils_rate),
            "geo": TokenBucketLimiter(rate_per_sec=geo_rate),
            "ftp": TokenBucketLimiter(rate_per_sec=ftp_rate),
            "ncbi": TokenBucketLimiter(rate_per_sec=ncbi_rate),
            # NCBI's BLAST URL API etiquette: no more than one request every 10 seconds
            "blast": TokenBucketLimiter(rate_per_sec=blast_rate, capacity=1.0),
        }

    def update_eutils_rate(self, rate_per_sec: float) -> None:
        """Update the Entrez E-Utilities rate limit (e.g., when API key is configured)."""
        self._buckets["eutils"].rate_per_sec = rate_per_sec

    def acquire(self, url: str) -> tuple[str, float]:
        """Acquire a rate-limit token for the given URL's realm.

        Returns:
            Tuple of `(realm_name, wait_seconds)`.
        """
        realm = classify_ncbi_realm(url)
        bucket = self._buckets.get(realm)
        if bucket is None:
            return realm, 0.0
        wait_sec = bucket.acquire()
        return realm, wait_sec

    def pause(self, url: str, seconds: float) -> None:
        """Pause the rate limiter for the realm matching `url`."""
        realm = classify_ncbi_realm(url)
        bucket = self._buckets.get(realm)
        if bucket is not None:
            bucket.pause(seconds)

    def stats(self) -> dict[str, dict[str, Any]]:
        """Return rate-limiter statistics across all NCBI realms."""
        return {realm: bucket.stats() for realm, bucket in self._buckets.items()}
