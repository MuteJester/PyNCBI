"""Standard-library NCBI Backend Client (`NCBIClient`) for PyNCBI.

Provides a unified, observable, realm-aware HTTP client for all NCBI backends:
- Realm-specific token-bucket rate limiting (`eutils`: 3/10 req/s, `geo`: 8 req/s, `ftp`: 20 req/s)
- Automatic `ftp://` -> `https://` normalization
- Automatic `GET` -> `POST` promotion on Entrez E-Utilities when UIDs/query exceed URL limits
- Application-level error detection (catches NCBI 200-OK rate-limit, invalid API key,
  private/embargoed GEO accession, and non-existent accession payloads)
- Atomic `.part` file downloads with HTTP Range resume support
- Built-in request telemetry, latency metrics, and API-key redaction in logs/errors
- Health & connectivity diagnostics (`client.check_health()`)
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from PyNCBI.core.config import Config, get_config
from PyNCBI.core.exceptions import (
    AccessionNotFoundError,
    AccessionPrivateError,
    BotChallengeError,
    ConnectionFailedError,
    DownloadError,
    HTTPError,
    InvalidAPIKeyError,
    PyNCBIError,
    RateLimitExceededError,
    RequestTimeoutError,
    redact_url,
)
from PyNCBI.core.logging import get_logger
from PyNCBI.core.reporter import get_reporter
from PyNCBI.io.rate_limiter import NCBIRealmLimiter, classify_ncbi_realm
from PyNCBI.io.response_cache import ResponseCache
from PyNCBI.io.responses import (
    _GEO_NOT_FOUND_RE,
    _GEO_PRIVATE_RE,
    looks_like_bot_challenge,
)
from PyNCBI.io.telemetry import ClientMetrics, RequestEvent
from PyNCBI.io.transfer_limits import read_response, response_budget
from PyNCBI.io.transport import (
    HTTPResponse,
    HTTPStatusError,
    HTTPTransport,
    TransportConnectionError,
    TransportError,
    TransportTimeout,
)
from PyNCBI.io.urls import (
    _API_KEY_PARAM_RE,
    _CACHEABLE_PATH_RE,
    _UNCACHEABLE_PATH_RE,
    download_filename,
    format_size_human,
    infer_file_type,
    normalize_ncbi_url,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PyNCBI.core.types import ProgressCallback

logger = get_logger(__name__)


def _pkg_version() -> str:
    """Package version for the User-Agent without importing the package root at module load."""
    try:
        from importlib.metadata import version

        return version("PyNCBI")
    except Exception:  # noqa: BLE001 - editable/uninstalled checkouts
        return "1.0.0"


@dataclass
class NCBIClientConfig:
    """Configuration for `NCBIClient`.

    Attributes:
        timeout: Default request timeout in seconds
        max_retries: Maximum number of retry attempts on transient/rate-limit errors
        retry_backoff: Exponential backoff factor between retries
        retry_statuses: HTTP status codes to retry on
        rate_limit_per_sec: Override E-Utilities rate limit (None = auto from Config)
        geo_rate_limit_per_sec: Max requests/sec to GEO CGI endpoints
        ftp_rate_limit_per_sec: Max requests/sec to static NCBI FTP/HTTPS servers
        eutils_post_threshold_chars: Promote E-Utilities GET to POST when URL exceeds this length
    """

    timeout: float = 30.0
    max_retries: int = 3
    retry_backoff: float = 0.5
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    rate_limit_per_sec: float | None = None
    geo_rate_limit_per_sec: float = 8.0
    ftp_rate_limit_per_sec: float = 20.0
    eutils_post_threshold_chars: int = 1800
    extra_headers: dict[str, str] = field(default_factory=dict)


# Backward-compatible alias
HttpClientConfig = NCBIClientConfig


class NCBIClient:
    """Realm-aware standard-library HTTP backend client for NCBI.

    Manages standard-library HTTP transport, realm-specific rate limiters (`eutils`, `geo`, `ftp`),
    automatic E-Utilities parameter injection (`tool`, `email`, `api_key`),
    automatic `GET` -> `POST` promotion for long UID queries, application-level
    error detection, atomic `.part` resumable downloads, and request telemetry.
    """

    def __init__(
        self,
        config: NCBIClientConfig | None = None,
        *,
        app_config: Config | None = None,
    ) -> None:
        self.config = config or NCBIClientConfig()
        # A pinned app config isolates this client's credentials from the process-wide one and
        # stays correct inside worker threads (where context-scoped config does not propagate).
        self._app_config = app_config
        app_cfg = self.app_config
        eutils_rate = self.config.rate_limit_per_sec or app_cfg.effective_rate_limit
        self._realm_limiter = NCBIRealmLimiter(
            eutils_rate=eutils_rate,
            geo_rate=self.config.geo_rate_limit_per_sec,
            ftp_rate=self.config.ftp_rate_limit_per_sec,
        )
        self.metrics = ClientMetrics(audit_path=app_cfg.audit_log_path)
        self.response_cache = ResponseCache()
        self.metrics.attach_cache(self.response_cache)
        self._session = self._create_session()
        self._geo_warmed_up = False
        self._warmup_lock = threading.Lock()

    def _cache_key(self, url: str) -> str | None:
        """A cache key for a cacheable metadata URL, or None when the URL must not be cached."""
        if _UNCACHEABLE_PATH_RE.search(url) or not _CACHEABLE_PATH_RE.search(url):
            return None
        # The API key never changes the response, so rotating it must not invalidate the cache
        return _API_KEY_PARAM_RE.sub("", url)

    def clear_response_cache(self) -> None:
        """Drop every cached response held by this client."""
        self.response_cache.clear()

    @property
    def app_config(self) -> Config:
        """The configuration this client reads credentials and rate limits from."""
        return self._app_config if self._app_config is not None else get_config()

    def _create_session(self) -> HTTPTransport:
        """Create a client-owned standard-library transport with isolated cookies."""
        return HTTPTransport(
            {
                "User-Agent": f"PyNCBI/{_pkg_version()} (https://github.com/MuteJester/PyNCBI)",
                "Accept-Encoding": "gzip, deflate",
                **self.config.extra_headers,
            }
        )

    def _open(self, method: str, url: str, **options: Any) -> HTTPResponse:
        """One retry policy for metadata, HEAD, previews, streams and downloads."""
        attempts = max(1, self.config.max_retries + 1)
        for attempt in range(attempts):
            started = time.monotonic()
            response = None
            failure = None
            try:
                response = self._session.request(method, url, **options)
                if (
                    response.status_code not in self.config.retry_statuses
                    or attempt + 1 == attempts
                ):
                    return response
            except (TransportConnectionError, TransportTimeout) as exc:
                if attempt + 1 == attempts:
                    raise
                failure = exc
            delay = min(self.config.retry_backoff * 2**attempt, 30.0)
            if response is not None:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    import datetime
                    from email.utils import parsedate_to_datetime

                    with contextlib.suppress(ValueError, TypeError, OverflowError):
                        delay = max(
                            delay,
                            (
                                parsedate_to_datetime(retry_after)
                                - datetime.datetime.now(datetime.timezone.utc)
                            ).total_seconds(),
                        )
                response.close()
            self.metrics.record(
                RequestEvent(
                    method=method,
                    realm=classify_ncbi_realm(url),
                    url=redact_url(url),
                    status_code=response.status_code if response is not None else 0,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                    rate_wait_ms=0,
                    bytes_received=0,
                    error=type(failure).__name__ if failure else "retryable_http_status",
                )
            )
            self.metrics.record_retry()
            self._realm_limiter.pause(url, max(0.0, delay))
            self._realm_limiter.acquire(url)
        raise AssertionError("Unreachable HTTP retry state")

    def _sync_rate_limit_with_config(self) -> None:
        """Keep E-Utilities rate limit synchronized if Config.api_key changes at runtime."""
        if self.config.rate_limit_per_sec is None:
            self._realm_limiter.update_eutils_rate(self.app_config.effective_rate_limit)

    def _prepare_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        acquire: bool = True,
    ) -> tuple[str, str, dict[str, str], float]:
        """Normalize URL, inject E-Utilities parameters, and acquire realm rate-limit token.

        Returns:
            Tuple of `(prepared_url, base_url_without_query, query_dict, wait_seconds)`.
        """
        self._sync_rate_limit_with_config()
        normalized = normalize_ncbi_url(url)
        parsed = urlparse(normalized)
        query_dict = dict(parse_qsl(parsed.query, keep_blank_values=True))

        if params:
            for k, v in params.items():
                if v is not None:
                    query_dict[k] = str(v)

        realm = classify_ncbi_realm(normalized)
        if realm == "eutils":
            app_cfg = self.app_config
            query_dict.setdefault("tool", "PyNCBI")
            if app_cfg.email and "email" not in query_dict:
                query_dict["email"] = app_cfg.email
            if app_cfg.api_key and "api_key" not in query_dict:
                query_dict["api_key"] = app_cfg.api_key

        wait_sec = 0.0
        if acquire:
            _, wait_sec = self._realm_limiter.acquire(normalized)
        new_query = urlencode(query_dict)
        full_url = urlunparse(parsed._replace(query=new_query))
        base_url = urlunparse(parsed._replace(query=""))
        return full_url, base_url, query_dict, wait_sec

    def _warm_up_geo_session(self) -> bool:
        """Obtain NCBI's session cookie with one cheap GEO request.

        GEO's shared download CGI (e.g. the RNA-seq annotation tables) answers cookie-less
        clients with a reCAPTCHA page, but serves the file once the session carries the
        `ncbi_sid` cookie set by any `acc.cgi` request. Returns True if a warm-up was
        performed (callers retry once), False if the session was already warmed up.
        """
        with self._warmup_lock:
            if self._geo_warmed_up:
                return False
            self._geo_warmed_up = True
        warm_url = get_config().get_ncbi_query_url("GPL96")
        try:
            prepared, _, _, _ = self._prepare_request(warm_url)
            # The body is irrelevant - only the `ncbi_sid` cookie matters - but the
            # response still has to be closed or its socket is never returned to the pool.
            with self._open("GET", prepared, timeout=min(15.0, self.config.timeout)) as response:
                response.close()
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.debug("GEO session warm-up failed: %s", exc)
        return True

    def _handle_rate_limit_headers(self, url: str, response: HTTPResponse) -> float | None:
        """Inspect Retry-After headers and pause the realm's token bucket if throttled."""
        if response.status_code in (429, 503):
            retry_after_raw = response.headers.get("Retry-After")
            pause_sec = 2.0
            if retry_after_raw:
                try:
                    pause_sec = float(retry_after_raw)
                except ValueError:
                    pause_sec = 2.0
            self._realm_limiter.pause(url, pause_sec)
            return pause_sec
        return None

    def _inspect_application_errors(
        self,
        url: str,
        realm: str,
        status_code: int,
        text_sample: str,
    ) -> None:
        """Detect NCBI application-level errors returned inside HTTP 200/400/429 bodies."""
        if not text_sample:
            return

        if looks_like_bot_challenge(text_sample):
            raise BotChallengeError(url)

        if realm == "eutils":
            lower = text_sample.lower()
            if "api key invalid" in lower or "invalid api key" in lower:
                raise InvalidAPIKeyError(url, "NCBI E-Utilities rejected the configured api_key")
            if "api rate limit exceeded" in lower or "rate limit exceeded" in lower:
                self._realm_limiter.pause(url, 1.5)
                raise RateLimitExceededError(url, retry_after=1.5, detail="API rate limit exceeded")

        if status_code == 200 and "form=text" in url:
            head = text_sample[:8192]
            looks_html = head.lstrip()[:15].lower().startswith(("<!doctype html", "<html"))
            if looks_html:
                priv_match = _GEO_PRIVATE_RE.search(head)
                parsed_q = dict(parse_qsl(urlparse(url).query))
                acc = parsed_q.get("acc", "UNKNOWN")
                if priv_match:
                    raise AccessionPrivateError(
                        priv_match.group(1) or acc,
                        f"scheduled release: {(priv_match.group(2) or 'embargoed').strip()}",
                    )
                raise AccessionNotFoundError(
                    acc,
                    "GEO returned an HTML page instead of a SOFT record (accession does not exist)",
                )

        if realm == "geo" and status_code == 200:
            head = text_sample[:8192]
            priv_match = _GEO_PRIVATE_RE.search(head)
            if priv_match:
                acc = priv_match.group(1) or "UNKNOWN"
                release_date = priv_match.group(2) or "embargoed"
                raise AccessionPrivateError(acc, f"scheduled release: {release_date.strip()}")

            nf_match = _GEO_NOT_FOUND_RE.search(head)
            if nf_match:
                parsed_q = dict(parse_qsl(urlparse(url).query))
                acc = nf_match.group(1) or parsed_q.get("acc", "UNKNOWN")
                raise AccessionNotFoundError(acc)

    def _execute(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> HTTPResponse:
        """Central request execution pipeline with E-Utilities POST promotion,
        application-level error retries, telemetry, and structured debug logging.
        """
        timeout = timeout or self.config.timeout
        attempt = 0
        max_attempts = max(1, self.config.max_retries + 1)
        cache_ttl = float(self.app_config.http_cache_ttl)
        cache_key: str | None = None
        if cache_ttl > 0:
            probe_url, _, _, _ = self._prepare_request(url, params=params, acquire=False)
            cache_key = self._cache_key(probe_url)
            if cache_key is not None:
                cached = self.response_cache.get(cache_key)
                if isinstance(cached, HTTPResponse):
                    self.metrics.record_cache_hit()
                    logger.debug("[CACHE] %s", redact_url(probe_url))
                    return cached

        budget = response_budget(self.app_config)
        while True:
            attempt += 1
            prepared_url, base_url, query_dict, wait_sec = self._prepare_request(url, params=params)
            realm = classify_ncbi_realm(prepared_url)
            safe_url = redact_url(prepared_url)

            # Promote E-Utilities GET to POST when query string is very long (>1800 chars)
            use_post = (
                realm == "eutils" and len(prepared_url) > self.config.eutils_post_threshold_chars
            )
            method = "POST" if use_post else "GET"

            t0 = time.monotonic()
            try:
                if use_post:
                    response = self._open(
                        "POST",
                        base_url,
                        data=query_dict,
                        headers=headers,
                        timeout=timeout,
                        stream=True,
                    )
                else:
                    response = self._open(
                        "GET",
                        prepared_url,
                        headers=headers,
                        timeout=timeout,
                        stream=True,
                    )
                response._content = read_response(response, budget)
                response.__dict__["_content_consumed"] = True
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                retry_after = self._handle_rate_limit_headers(prepared_url, response)

                if response.status_code == 429:
                    raise RateLimitExceededError(prepared_url, retry_after=retry_after)

                response.raise_for_status()

                # Inspect first 2 KB for application-level errors (e.g., 200-OK rate limit)
                content_bytes = response.content
                sample_text = content_bytes[:8192].decode("utf-8", errors="replace")
                self._inspect_application_errors(
                    prepared_url, realm, response.status_code, sample_text
                )

                self.metrics.record(
                    RequestEvent(
                        method=method,
                        realm=realm,
                        url=safe_url,
                        status_code=response.status_code,
                        elapsed_ms=elapsed_ms,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=len(content_bytes),
                    )
                )
                logger.debug(
                    "[%s %d] %s (%s, %.0fms, wait=%.0fms)",
                    method,
                    response.status_code,
                    safe_url,
                    format_size_human(len(content_bytes)),
                    elapsed_ms,
                    wait_sec * 1000.0,
                )
                if cache_key is not None and response.status_code == 200:
                    self.response_cache.set(
                        cache_key, response, ttl=cache_ttl, size_bytes=len(content_bytes)
                    )
                return response

            except BotChallengeError:
                self.metrics.record(
                    RequestEvent(
                        method=method,
                        realm=realm,
                        url=safe_url,
                        status_code=403,
                        elapsed_ms=(time.monotonic() - t0) * 1000.0,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=0,
                        error="BotChallengeError",
                    )
                )
                if realm in ("geo", "ncbi", "external") and self._warm_up_geo_session():
                    self.metrics.record_retry()
                    logger.debug(
                        "Bot challenge on %s; retrying once after GEO session warm-up", safe_url
                    )
                    continue
                raise

            except RateLimitExceededError as e:
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                self.metrics.record(
                    RequestEvent(
                        method=method,
                        realm=realm,
                        url=safe_url,
                        status_code=429,
                        elapsed_ms=elapsed_ms,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=0,
                        error=type(e).__name__,
                    )
                )
                if attempt < max_attempts and response.status_code != 429:
                    self.metrics.record_retry()
                    backoff = min(self.config.retry_backoff * (2 ** (attempt - 1)), 3.0)
                    get_reporter().warn(
                        "HTTP",
                        f"Rate limit encountered on '{realm}'; retrying ({attempt}/{max_attempts - 1}) in {backoff:.1f}s...",
                        operation="request",
                        target=safe_url,
                    )
                    time.sleep(backoff)
                    continue
                raise

            except TransportTimeout as e:
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                self.metrics.record(
                    RequestEvent(
                        method=method,
                        realm=realm,
                        url=safe_url,
                        status_code=0,
                        elapsed_ms=elapsed_ms,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=0,
                        error="RequestTimeoutError",
                    )
                )
                raise RequestTimeoutError(prepared_url, timeout) from e

            except TransportConnectionError as e:
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                self.metrics.record(
                    RequestEvent(
                        method=method,
                        realm=realm,
                        url=safe_url,
                        status_code=0,
                        elapsed_ms=elapsed_ms,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=0,
                        error="ConnectionFailedError",
                    )
                )
                raise ConnectionFailedError(prepared_url, e) from e

            except HTTPStatusError as e:
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                status = e.response.status_code if e.response is not None else 0
                body_snippet = ""
                if e.response is not None and e.response.content:
                    body_snippet = e.response.content[:1024].decode("utf-8", errors="replace")
                self.metrics.record(
                    RequestEvent(
                        method=method,
                        realm=realm,
                        url=safe_url,
                        status_code=status,
                        elapsed_ms=elapsed_ms,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=0,
                        error=f"HTTPError({status})",
                    )
                )
                self._inspect_application_errors(prepared_url, realm, status, body_snippet)
                raise self._translate_request_exception(prepared_url, e) from e
            except TransportError as e:
                # Other transport failures:
                # a connection dropped mid-body, a bad content encoding, too many
                # redirects. Without this, a low-level transport exception escapes and the
                # documented contract (error_code, retryable, hint) does not apply.
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                translated = self._translate_request_exception(prepared_url, e)
                self.metrics.record(
                    RequestEvent(
                        method=method,
                        realm=realm,
                        url=safe_url,
                        status_code=0,
                        elapsed_ms=elapsed_ms,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=0,
                        error=type(translated).__name__,
                    )
                )
                raise translated from e
            except PyNCBIError as exc:
                self.metrics.record(
                    RequestEvent(
                        method=method,
                        realm=realm,
                        url=safe_url,
                        status_code=getattr(response, "status_code", 0),
                        elapsed_ms=(time.monotonic() - t0) * 1000.0,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=getattr(response, "_pyncbi_bytes_received", 0),
                        error=type(exc).__name__,
                    )
                )
                raise

    def _translate_request_exception(self, url: str, exc: TransportError) -> PyNCBIError:
        """Keep low-level stdlib failures inside the public error taxonomy."""
        if isinstance(exc, HTTPStatusError):
            if exc.response.status_code == 429:
                return RateLimitExceededError(url)
            return HTTPError(url, exc.response.status_code, str(exc))
        return ConnectionFailedError(url, exc)

    def get_text(
        self,
        url: str,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Fetch URL content as UTF-8 text."""
        response = self._execute(url, params=params, timeout=timeout)
        if not response.encoding or response.encoding.lower() in (
            "iso-8859-1",
            "latin-1",
            "ascii",
        ):
            return response.content.decode("utf-8", errors="replace")
        return response.text

    def get_json(
        self,
        url: str,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch URL content and parse as JSON dictionary."""
        response = self._execute(url, params=params, timeout=timeout)
        data: dict[str, Any] = response.json()
        if isinstance(data, dict) and "error" in data:
            err_msg = str(data.get("error", ""))
            self._inspect_application_errors(
                url, classify_ncbi_realm(url), response.status_code, err_msg
            )
        return data

    def get_bytes(
        self,
        url: str,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        """Fetch URL content as raw bytes."""
        response = self._execute(url, params=params, timeout=timeout)
        return response.content

    def get_range_bytes(
        self,
        url: str,
        max_bytes: int = 32768,
        timeout: float | None = None,
    ) -> bytes:
        """Fetch only the first `max_bytes` of a remote file via HTTP Range / streaming.

        Allows instant previewing of large remote `.gz`, `.csv`, `.tsv`, or `.tar` files
        without downloading the full file body.
        """
        timeout = timeout or self.config.timeout
        prepared_url, _, _, wait_sec = self._prepare_request(url)
        realm = classify_ncbi_realm(prepared_url)
        safe_url = redact_url(prepared_url)
        headers = {"Range": f"bytes=0-{max_bytes - 1}"} if "?" not in prepared_url else {}
        budget = response_budget(self.app_config)
        t0 = time.monotonic()

        try:
            with self._open(
                "GET",
                prepared_url,
                headers=headers,
                stream=True,
                timeout=timeout,
            ) as response:
                self._handle_rate_limit_headers(prepared_url, response)
                response.raise_for_status()
                chunks: list[bytes] = []
                collected = 0
                for chunk in response.iter_content(chunk_size=min(8192, max_bytes)):
                    if not chunk:
                        break
                    budget.consume(len(chunk))
                    chunks.append(chunk)
                    collected += len(chunk)
                    if collected >= max_bytes:
                        break
                result = b"".join(chunks)[:max_bytes]
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                self.metrics.record(
                    RequestEvent(
                        method="GET(Range)",
                        realm=realm,
                        url=safe_url,
                        status_code=response.status_code,
                        elapsed_ms=elapsed_ms,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=len(result),
                    )
                )
                return result
        except TransportTimeout as e:
            raise RequestTimeoutError(prepared_url, timeout) from e
        except TransportConnectionError as e:
            raise ConnectionFailedError(prepared_url, e) from e
        except HTTPStatusError as e:
            raise self._translate_request_exception(prepared_url, e) from e
        except TransportError as e:
            raise self._translate_request_exception(prepared_url, e) from e

    def stream_lines(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        chunk_size: int = 65536,
        encoding: str = "utf-8",
    ) -> Iterator[str]:
        """Stream a remote text (or gzip-compressed text) resource line by line.

        Decompresses gzip payloads transparently (by magic bytes, not by extension), so
        multi-GB GEO CGI tables (`acc.cgi?...&view=data`) can be parsed incrementally and
        abandoned early (e.g., `limit=` rows) without downloading the entire body.
        Closing the generator early closes the underlying HTTP connection.
        """
        timeout = timeout or self.config.timeout
        prepared_url, _, _, wait_sec = self._prepare_request(url, params=params)
        realm = classify_ncbi_realm(prepared_url)
        safe_url = redact_url(prepared_url)
        t0 = time.monotonic()
        received = 0
        status_code = 0
        error: str | None = None
        retried = False

        try:
            with self._open("GET", prepared_url, stream=True, timeout=timeout) as response:
                status_code = response.status_code
                self._handle_rate_limit_headers(prepared_url, response)
                if response.status_code == 429:
                    raise RateLimitExceededError(prepared_url)
                response.raise_for_status()

                content_len = int(response.headers.get("content-length", 0) or 0)
                parsed_q = dict(parse_qsl(urlparse(prepared_url).query))
                label = parsed_q.get("acc") or prepared_url.split("?")[0].rstrip("/").split("/")[-1]
                bar = get_reporter().download_bar(
                    description=f"stream {label}", total_bytes=content_len
                )
                bar.__enter__()
                try:
                    yield from self._iter_stream_lines(
                        response, bar, chunk_size, encoding, prepared_url, realm
                    )
                finally:
                    received = bar.current_bytes
                    bar.__exit__(None, None, None)
        except TransportTimeout as e:
            error = "RequestTimeoutError"
            raise RequestTimeoutError(prepared_url, timeout) from e
        except TransportConnectionError as e:
            error = "ConnectionFailedError"
            raise ConnectionFailedError(prepared_url, e) from e
        except HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else 0
            error = f"HTTPError({status})"
            raise self._translate_request_exception(prepared_url, e) from e
        except TransportError as e:
            error = type(e).__name__
            raise self._translate_request_exception(prepared_url, e) from e
        except BotChallengeError:
            error = "BotChallengeError"
            if self._warm_up_geo_session():
                retried = True
            else:
                raise
        except RateLimitExceededError as e:
            error = type(e).__name__
            raise
        finally:
            self.metrics.record(
                RequestEvent(
                    method="GET(Stream)",
                    realm=realm,
                    url=safe_url,
                    status_code=status_code,
                    elapsed_ms=(time.monotonic() - t0) * 1000.0,
                    rate_wait_ms=wait_sec * 1000.0,
                    bytes_received=received,
                    error=error,
                )
            )
        if retried:
            yield from self.stream_lines(
                url, params=params, timeout=timeout, chunk_size=chunk_size, encoding=encoding
            )

    def _iter_stream_lines(
        self,
        response: HTTPResponse,
        bar: Any,
        chunk_size: int,
        encoding: str,
        prepared_url: str,
        realm: str,
    ) -> Iterator[str]:
        decompressor: zlib._Decompress | None = None
        first = True
        buffer = b""
        budget = response_budget(self.app_config)
        length = response.headers.get("Content-Length", "")
        if length.isdigit():
            budget.check(int(length))
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            budget.consume(len(chunk))
            bar.update(len(chunk))
            if first:
                first = False
                if chunk[:2] == b"\x1f\x8b":
                    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                else:
                    self._inspect_application_errors(
                        prepared_url,
                        realm,
                        response.status_code,
                        chunk[:4096].decode(encoding, errors="replace"),
                    )
            data = decompressor.decompress(chunk) if decompressor else chunk
            if not data:
                continue
            buffer += data
            *complete, buffer = buffer.split(b"\n")
            for raw_line in complete:
                yield raw_line.decode(encoding, errors="replace").rstrip("\r")
        if decompressor is not None:
            tail = decompressor.flush()
            if tail:
                buffer += tail
        if buffer:
            yield buffer.decode(encoding, errors="replace").rstrip("\r")

    def inspect_url(
        self,
        url: str,
        source: str = "supplementary",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Inspect a remote file via HTTP HEAD without downloading its body.

        Returns:
            Dictionary with keys: name, url, size_bytes, size_human,
            last_modified, file_type, source, exists.
        """
        prepared_url, _, _, wait_sec = self._prepare_request(url)
        realm = classify_ncbi_realm(prepared_url)
        safe_url = redact_url(prepared_url)
        filename = download_filename(prepared_url)

        size_bytes: int | None = None
        last_modified: str | None = None
        exists = False
        status_code = 0
        t0 = time.monotonic()

        try:
            resp = self._open("HEAD", prepared_url, allow_redirects=True, timeout=timeout)
            status_code = resp.status_code
            if resp.status_code == 200:
                exists = True
                cl = resp.headers.get("Content-Length") or resp.headers.get("content-length")
                if cl and cl.isdigit():
                    size_bytes = int(cl)
                last_modified = resp.headers.get("Last-Modified") or resp.headers.get(
                    "last-modified"
                )
            elif resp.status_code in (403, 405):
                with self._open(
                    "GET",
                    prepared_url,
                    headers={"Range": "bytes=0-0"},
                    stream=True,
                    timeout=timeout,
                ) as get_resp:
                    status_code = get_resp.status_code
                    if get_resp.status_code in (200, 206):
                        exists = True
                        cr = get_resp.headers.get("Content-Range", "")
                        if "/" in cr:
                            total_str = cr.split("/")[-1].strip()
                            if total_str.isdigit():
                                size_bytes = int(total_str)
                        elif get_resp.headers.get("Content-Length", "").isdigit():
                            size_bytes = int(get_resp.headers["Content-Length"])
                        last_modified = get_resp.headers.get("Last-Modified")
        except Exception:
            pass
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self.metrics.record(
                RequestEvent(
                    method="HEAD",
                    realm=realm,
                    url=safe_url,
                    status_code=status_code,
                    elapsed_ms=elapsed_ms,
                    rate_wait_ms=wait_sec * 1000.0,
                    bytes_received=0,
                )
            )

        return {
            "name": filename,
            "url": safe_url,
            "size_bytes": size_bytes,
            "size_human": format_size_human(size_bytes),
            "last_modified": last_modified,
            "file_type": infer_file_type(filename),
            "source": source,
            "exists": exists,
        }

    def download_file(
        self,
        url: str,
        destination: Path,
        progress_callback: ProgressCallback | None = None,
        chunk_size: int = 65536,
        *,
        resume: bool = True,
    ) -> Path:
        """Download a file atomically via a `.part` staging file with HTTP Range resume support.

        If an interrupted `<destination>.part` file exists and `resume=True`, automatically
        requests the remaining byte range (`Range: bytes=<offset>-`) from static NCBI servers
        and atomically renames `<destination>.part` -> `<destination>` on completion.
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        part_path = destination.with_name(destination.name + ".part")

        prepared_url, _, _, wait_sec = self._prepare_request(url)
        realm = classify_ncbi_realm(prepared_url)
        safe_url = redact_url(prepared_url)

        existing_bytes = 0
        req_headers: dict[str, str] = {}
        if resume and part_path.exists() and "?" not in prepared_url:
            existing_bytes = part_path.stat().st_size
            if existing_bytes > 0:
                req_headers["Range"] = f"bytes={existing_bytes}-"

        response: HTTPResponse | None = None
        bytes_this_request = 0
        budget = response_budget(self.app_config)
        t0 = time.monotonic()
        try:
            with self._open(
                "GET",
                prepared_url,
                headers=req_headers,
                stream=True,
                timeout=self.config.timeout,
            ) as response:
                self._handle_rate_limit_headers(prepared_url, response)

                # If Range was not satisfiable (e.g. file already complete or changed), restart cleanly
                if response.status_code == 416 and existing_bytes > 0:
                    part_path.unlink(missing_ok=True)
                    return self.download_file(
                        url,
                        destination,
                        progress_callback=progress_callback,
                        chunk_size=chunk_size,
                        resume=False,
                    )

                response.raise_for_status()

                is_partial = response.status_code == 206 and existing_bytes > 0
                content_len = int(response.headers.get("content-length", 0))
                total = (
                    (existing_bytes + content_len) if is_partial and content_len else content_len
                )
                budget.check(content_len)
                budget.disk(destination, content_len)
                current = existing_bytes if is_partial else 0
                file_mode = "ab" if is_partial else "wb"
                bytes_this_request = 0

                with (
                    get_reporter().download_bar(
                        description=destination.name,
                        total_bytes=total,
                        initial_bytes=current,
                    ) as bar,
                    part_path.open(file_mode) as f,
                ):
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            budget.consume(len(chunk))
                            budget.disk(destination, len(chunk))
                            f.write(chunk)
                            chunk_len = len(chunk)
                            current += chunk_len
                            bytes_this_request += chunk_len
                            bar.update(chunk_len)
                            if progress_callback:
                                progress_callback(current, total, destination.name)

                if content_len > 0 and bytes_this_request < content_len:
                    raise DownloadError(
                        prepared_url,
                        f"Incomplete transfer ({bytes_this_request} of {content_len} bytes received)",
                    )

                part_path.replace(destination)
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                self.metrics.record(
                    RequestEvent(
                        method="GET(Download)",
                        realm=realm,
                        url=safe_url,
                        status_code=response.status_code,
                        elapsed_ms=elapsed_ms,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=bytes_this_request,
                    )
                )

        except TransportTimeout as e:
            raise RequestTimeoutError(prepared_url, self.config.timeout) from e
        except TransportConnectionError as e:
            raise ConnectionFailedError(prepared_url, e) from e
        except HTTPStatusError as e:
            raise self._translate_request_exception(prepared_url, e) from e
        except TransportError as e:
            # A body that stops early (ChunkedEncodingError) is the usual way a long GEO
            # transfer dies. The partial `.part` file is kept so a retry can resume it.
            raise self._translate_request_exception(prepared_url, e) from e
        finally:
            failure = sys.exc_info()[1]
            if failure is not None:
                self.metrics.record(
                    RequestEvent(
                        method="GET(Download)",
                        realm=realm,
                        url=safe_url,
                        status_code=response.status_code if response is not None else 0,
                        elapsed_ms=(time.monotonic() - t0) * 1000.0,
                        rate_wait_ms=wait_sec * 1000.0,
                        bytes_received=bytes_this_request,
                        error=type(failure).__name__,
                    )
                )

        return destination

    def rate_limit_stats(self) -> dict[str, dict[str, Any]]:
        """Return live token-bucket rate limiter statistics across all NCBI realms."""
        self._sync_rate_limit_with_config()
        return self._realm_limiter.stats()

    def check_health(self, probe_network: bool = True) -> dict[str, Any]:
        """Run a comprehensive diagnostic check of PyNCBI configuration, cache, and NCBI backends.

        Args:
            probe_network: If True, probes NCBI E-Utilities and GEO endpoints to measure
                live latency and validate `NCBI_API_KEY` if configured.

        Returns:
            Structured dictionary suitable for terminal display, JSON output, or MCP tools.
        """
        from PyNCBI import __version__
        from PyNCBI.io.cache import LocalCache

        app_cfg = self.app_config
        cache: LocalCache[Any] = LocalCache(app_cfg.cache_folder)
        cache_stats = cache.status_summary()

        endpoints: dict[str, dict[str, Any]] = {}
        api_key_valid: bool | None = None

        if probe_network:
            # 1. Probe Entrez E-Utilities (`einfo.fcgi`)
            t0 = time.monotonic()
            try:
                einfo = self.get_json(
                    f"{app_cfg.NCBI_EUTILS_BASE_URL}/einfo.fcgi",
                    params={"retmode": "json"},
                    timeout=10.0,
                )
                latency = round((time.monotonic() - t0) * 1000.0, 1)
                db_count = len(einfo.get("einforesult", {}).get("dblist", []))
                endpoints["eutils"] = {
                    "ok": True,
                    "url": app_cfg.NCBI_EUTILS_BASE_URL,
                    "latency_ms": latency,
                    "databases_available": db_count,
                }
                if app_cfg.api_key:
                    api_key_valid = True
            except InvalidAPIKeyError as e:
                api_key_valid = False
                endpoints["eutils"] = {
                    "ok": False,
                    "url": app_cfg.NCBI_EUTILS_BASE_URL,
                    "error": e.message,
                }
            except Exception as e:
                endpoints["eutils"] = {
                    "ok": False,
                    "url": app_cfg.NCBI_EUTILS_BASE_URL,
                    "error": str(e),
                }

            # 2. Probe NCBI FTP/HTTPS mirror via HEAD
            t1 = time.monotonic()
            ftp_info = self.inspect_url(
                f"{app_cfg.NCBI_FTP_HTTPS_BASE}/geo/series/GSE85nnn/GSE85506/matrix/GSE85506_series_matrix.txt.gz",
                source="series_matrix",
                timeout=10.0,
            )
            endpoints["ftp_https"] = {
                "ok": bool(ftp_info.get("exists")),
                "url": app_cfg.NCBI_FTP_HTTPS_BASE,
                "latency_ms": round((time.monotonic() - t1) * 1000.0, 1),
            }

        return {
            "version": __version__,
            "config": {
                "api_key_configured": bool(app_cfg.api_key),
                "api_key_valid": api_key_valid,
                "email_configured": bool(app_cfg.email),
                "effective_eutils_rate_limit_per_sec": app_cfg.effective_rate_limit,
                "geo_rate_limit_per_sec": self.config.geo_rate_limit_per_sec,
                "ftp_rate_limit_per_sec": self.config.ftp_rate_limit_per_sec,
                "request_timeout_sec": self.config.timeout,
                "max_retries": self.config.max_retries,
                "agent_mode": app_cfg.agent_mode,
            },
            "cache": cache_stats,
            "rate_limiters": self.rate_limit_stats(),
            "telemetry": self.metrics.summary(include_recent=5),
            "recent_activity": get_reporter().recent_events(limit=10),
            "endpoints": endpoints,
        }

    def close(self) -> None:
        """Close the client-owned transport and cookie store."""
        self._session.close()

    def __enter__(self) -> NCBIClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


# Backward-compatible alias
HttpClient = NCBIClient


def _default_progress_callback(current: int, total: int, description: str = "") -> None:
    """Default wget-style progress callback (suppressed in agent/non-TTY mode)."""
    if get_config().agent_mode or not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return
    if total > 0:
        percent = current / total * 100
        message = f"Downloading {description}: {percent:.0f}% [{current:,} / {total:,}] bytes"
    else:
        message = f"Downloading {description}: {current:,} bytes"
    sys.stdout.write("\r" + message)
    sys.stdout.flush()


_default_client: NCBIClient | None = None


def get_client() -> NCBIClient:
    """Get the default shared `NCBIClient` singleton."""
    global _default_client
    if _default_client is None:
        _default_client = NCBIClient()
    return _default_client


def get_default_client() -> NCBIClient:
    """Alias for `get_client()`."""
    return get_client()
