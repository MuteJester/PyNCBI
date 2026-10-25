"""Custom exception hierarchy for PyNCBI.

All PyNCBI exceptions inherit from PyNCBIError, allowing users and AI agents to
catch all library-specific exceptions cleanly and inspect `.message` and `.hint`.

Exception Hierarchy:
    PyNCBIError (base)
    ├── NetworkError
    │   ├── ConnectionFailedError
    │   ├── RequestTimeoutError
    │   ├── HTTPError
    │   │   ├── RateLimitExceededError
    │   │   ├── InvalidAPIKeyError
    │   │   └── BotChallengeError
    │   └── DownloadError
    ├── DataError
    │   ├── ParseError
    │   │   └── SOFTParseError
    │   ├── NoDataAvailableError
    │   ├── AccessionNotFoundError
    │   ├── AccessionPrivateError
    │   ├── InvalidAccessionError
    │   └── DataProcessingError
    ├── CacheError
    │   ├── CacheCorruptedError
    │   ├── CacheNotFoundError
    │   └── CacheWriteError
    ├── FileOperationError
    │   └── ArchiveExtractionError
    │       ├── PathTraversalError
    │       └── SymlinkError
    └── ConfigurationError
        └── InvalidModeError
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_API_KEY_REDACT_RE = re.compile(r"([?&]api_key=)[^&#\s]+", re.IGNORECASE)


def redact_url(url: str | None) -> str:
    """Redact `api_key=...` query parameter from URLs before logging or raising."""
    if not url:
        return ""
    return _API_KEY_REDACT_RE.sub(r"\1***REDACTED***", url)


class PyNCBIError(Exception):
    """Base exception for all PyNCBI errors.

    Attributes:
        message: Human-readable error message
        hint: Optional suggestion for resolving the error
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.message = message
        self.hint = hint
        full_message = message
        if hint:
            full_message = f"{message}\n  Hint: {hint}"
        super().__init__(full_message)


# =============================================================================
# Network Errors
# =============================================================================


class NetworkError(PyNCBIError):
    """Base for network-related errors."""

    def __init__(self, message: str, *, url: str | None = None, **kwargs: object) -> None:
        self.url = redact_url(url) if url else None
        super().__init__(redact_url(message), **kwargs)  # type: ignore[arg-type]


class ConnectionFailedError(NetworkError):
    """Failed to establish connection to NCBI."""

    def __init__(self, url: str, cause: Exception | None = None) -> None:
        self.cause = cause
        safe_url = redact_url(url)
        message = f"Failed to connect to {safe_url}"
        if cause:
            message = f"{message}: {redact_url(str(cause))}"
        super().__init__(
            message,
            url=safe_url,
            hint="Check your internet connection or run `pyncbi status` to diagnose connectivity",
        )


class RequestTimeoutError(NetworkError):
    """Request timed out."""

    def __init__(self, url: str, timeout: float) -> None:
        self.timeout = timeout
        safe_url = redact_url(url)
        super().__init__(
            f"Request to {safe_url} timed out after {timeout}s",
            url=safe_url,
            hint="Try increasing timeout via PYNCBI_REQUEST_TIMEOUT environment variable",
        )


class HTTPError(NetworkError):
    """HTTP error response from server."""

    def __init__(
        self,
        url: str,
        status_code: int,
        message: str = "",
        *,
        hint: str | None = None,
    ) -> None:
        self.status_code = status_code
        safe_url = redact_url(url)
        detail = f": {redact_url(message)}" if message else ""

        default_hints = {
            400: "Check query syntax or parameters passed to NCBI",
            404: "Verify the accession ID or file URL is correct",
            414: "Request URI too long - reduce batch size or use POST",
            429: "Rate limit exceeded - set NCBI_API_KEY (10 req/s) or reduce concurrency",
            500: "NCBI server error - try again in a few moments",
            502: "NCBI bad gateway - transient upstream error, retry shortly",
            503: "NCBI service temporarily unavailable or under maintenance",
            504: "NCBI gateway timeout - retry shortly",
        }
        resolved_hint = hint or default_hints.get(status_code, "Check the URL and try again")

        super().__init__(
            f"HTTP {status_code} from {safe_url}{detail}",
            url=safe_url,
            hint=resolved_hint,
        )


class RateLimitExceededError(HTTPError):
    """NCBI API rate limit exceeded (HTTP 429 or E-Utilities rate-limit payload)."""

    def __init__(self, url: str, retry_after: float | None = None, detail: str = "") -> None:
        self.retry_after = retry_after
        msg = detail or "NCBI API rate limit exceeded"
        if retry_after:
            msg = f"{msg} (Retry-After: {retry_after:.1f}s)"
        super().__init__(
            url,
            429,
            msg,
            hint="Set environment variable NCBI_API_KEY for 10 req/s (default without key is 3 req/s)",
        )


class InvalidAPIKeyError(HTTPError):
    """Configured NCBI_API_KEY was rejected by NCBI E-Utilities."""

    def __init__(self, url: str, detail: str = "API key invalid") -> None:
        super().__init__(
            url,
            400,
            detail,
            hint="Check or unset your NCBI_API_KEY / PYNCBI_API_KEY environment variable",
        )


class BotChallengeError(HTTPError):
    """NCBI returned an interactive bot-challenge (reCAPTCHA) page instead of data.

    Some GEO download CGI endpoints (notably the shared NCBI RNA-seq annotation tables)
    are protected by a browser challenge for non-interactive clients on some networks.
    """

    def __init__(self, url: str, detail: str = "") -> None:
        msg = "NCBI returned a bot-challenge (reCAPTCHA) page instead of the requested content"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(
            url,
            403,
            msg,
            hint=(
                "This endpoint is browser-gated on your network. PyNCBI falls back to "
                "E-Utilities / NCBI Datasets where possible; otherwise download the file in a "
                "browser and pass the local path, or set NCBI_API_KEY and retry later."
            ),
        )


class DownloadError(NetworkError):
    """Failed to download a file.

    Attributes:
        completed_paths: Local paths that were downloaded successfully before/alongside
            the failure (populated by parallel multi-file downloads).
    """

    def __init__(
        self,
        url: str,
        reason: str,
        *,
        completed_paths: Sequence[object] | None = None,
    ) -> None:
        self.reason = reason
        self.completed_paths: list[object] = list(completed_paths or [])
        safe_url = redact_url(url)
        super().__init__(
            f"Failed to download {safe_url}: {reason}",
            url=safe_url,
            hint="Check if the file URL is accessible and try again",
        )


# =============================================================================
# Data Errors
# =============================================================================


class DataError(PyNCBIError):
    """Base for data-related errors."""


class ParseError(DataError):
    """Failed to parse data format."""

    def __init__(self, format_type: str, detail: str = "") -> None:
        self.format_type = format_type
        message = f"Failed to parse {format_type}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


class SOFTParseError(ParseError):
    """Failed to parse SOFT format."""

    def __init__(self, accession: str, detail: str = "") -> None:
        self.accession = accession
        full_detail = f"accession={accession}"
        if detail:
            full_detail = f"{full_detail}, {detail}"
        super().__init__("SOFT", full_detail)


class NoDataAvailableError(DataError):
    """GSM/GSE has no downloadable data."""

    def __init__(self, accession: str, detail: str = "", hint: str | None = None) -> None:
        """
        Args:
            accession: The record that has no data
            detail: Why, in the record's own terms, appended to the message
            hint: Overrides the default "inspect the record" hint when the caller knows
                the better next step (for example, which function to use instead)
        """
        self.accession = accession
        self.detail = detail
        url = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}"
        message = f"No downloadable data available for {accession}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(
            message,
            hint=hint or f"Inspect the record via `pyncbi inspect {accession}` or check {url}",
        )


class AccessionNotFoundError(DataError):
    """Requested accession does not exist in NCBI."""

    def __init__(self, accession: str, detail: str = "") -> None:
        self.accession = accession
        msg = f"Accession '{accession}' was not found on NCBI"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(
            msg,
            hint="Verify the accession ID spelling and prefix (e.g., GSE, GSM, GPL, PRJNA, SRR)",
        )


class AccessionPrivateError(DataError):
    """Requested GEO accession is currently embargoed / private or deleted."""

    def __init__(self, accession: str, detail: str = "") -> None:
        self.accession = accession
        msg = f"Accession '{accession}' is currently private or unavailable on NCBI GEO"
        if detail:
            msg = f"{msg} ({detail})"
        super().__init__(
            msg,
            hint=f"Check public release status at https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}",
        )


class InvalidAccessionError(DataError):
    """Invalid GSM/GSE/GPL/PRJNA/SRA accession format."""

    def __init__(self, accession: str, expected_prefix: str = "") -> None:
        self.accession = accession
        self.expected_prefix = expected_prefix

        if expected_prefix:
            example = f"{expected_prefix}1234567"
            message = f"Invalid accession '{accession}', expected {expected_prefix}XXXXXX format"
        else:
            example = "GSM1234567, GSE12345, GPL13534, PRJNA12345, or SRR123456"
            message = f"Invalid accession format: '{accession}'"

        super().__init__(message, hint=f"Example valid accession: {example}")


class DataProcessingError(DataError):
    """Error during data processing."""

    def __init__(self, accession: str, step: str, detail: str = "") -> None:
        self.accession = accession
        self.step = step
        message = f"Failed to process {accession} during {step}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(
            message,
            hint="The data may be corrupted or in an unexpected format",
        )


# =============================================================================
# Cache Errors
# =============================================================================


class CacheError(PyNCBIError):
    """Base for cache-related errors."""


class CacheCorruptedError(CacheError):
    """Cache file is corrupted and cannot be read."""

    def __init__(self, path: str, cause: Exception | None = None) -> None:
        self.path = path
        self.cause = cause
        message = f"Cache file corrupted: {path}"
        if cause:
            message = f"{message} ({cause})"
        super().__init__(
            message,
            hint="Delete the cache file (`pyncbi cache clear`) and re-fetch the data",
        )


class CacheNotFoundError(CacheError):
    """Expected cache entry not found."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Cache entry not found: {key}")


class CacheWriteError(CacheError):
    """Failed to write to cache."""

    def __init__(self, path: str, cause: Exception | None = None) -> None:
        self.path = path
        self.cause = cause
        message = f"Failed to write cache file: {path}"
        if cause:
            message = f"{message} ({cause})"
        super().__init__(
            message,
            hint="Check disk space and write permissions for the cache directory",
        )


# =============================================================================
# File Operation Errors
# =============================================================================


class FileOperationError(PyNCBIError):
    """Base for file operation errors."""


class ArchiveExtractionError(FileOperationError):
    """Failed to extract archive file (tar, gz, etc)."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to extract {path}: {reason}")


class PathTraversalError(ArchiveExtractionError):
    """Archive contains path traversal attack."""

    def __init__(self, path: str, member: str) -> None:
        self.member = member
        super().__init__(path, f"path traversal detected in member '{member}'")


class SymlinkError(ArchiveExtractionError):
    """Archive contains symbolic/hard links."""

    def __init__(self, path: str, member: str) -> None:
        self.member = member
        super().__init__(path, f"refusing to extract symlink/hardlink '{member}'")


# =============================================================================
# Configuration Errors
# =============================================================================


class ConfigurationError(PyNCBIError):
    """Invalid configuration."""


class InvalidModeError(ConfigurationError):
    """Invalid mode parameter for GSE fetching."""

    def __init__(self, mode: str, valid_modes: list[str] | None = None) -> None:
        self.mode = mode
        self.valid_modes = valid_modes or ["auto", "per_gsm", "supp", "matrix", "rnaseq"]
        super().__init__(
            f"Invalid mode '{mode}'",
            hint=f"Use one of: {', '.join(self.valid_modes)}",
        )


TRANSIENT_ERRORS = (
    ConnectionFailedError,
    RequestTimeoutError,
    RateLimitExceededError,
)

USER_ERRORS = (
    InvalidAccessionError,
    InvalidModeError,
    InvalidAPIKeyError,
    ConfigurationError,
)

DATA_ERRORS = (
    NoDataAvailableError,
    AccessionNotFoundError,
    AccessionPrivateError,
    ParseError,
    DataProcessingError,
)


# =============================================================================
# Stable error-code taxonomy for agents (CLI --json and MCP error payloads)
# =============================================================================

_ERROR_CODE_BY_CLASS: tuple[tuple[type[BaseException], str, bool], ...] = (
    # (exception class, error_code, retryable)
    (AccessionPrivateError, "private", False),
    (AccessionNotFoundError, "not_found", False),
    (NoDataAvailableError, "no_data", False),
    (InvalidAccessionError, "invalid_accession", False),
    (SOFTParseError, "parse_error", False),
    (ParseError, "parse_error", False),
    (DataProcessingError, "data_error", False),
    (DataError, "data_error", False),
    (RateLimitExceededError, "rate_limited", True),
    (InvalidAPIKeyError, "invalid_api_key", False),
    (BotChallengeError, "bot_challenge", True),
    (RequestTimeoutError, "timeout", True),
    (ConnectionFailedError, "connection_failed", True),
    (DownloadError, "download_failed", True),
    (HTTPError, "http_error", False),
    (NetworkError, "network_error", True),
    (CacheError, "cache_error", False),
    (PathTraversalError, "unsafe_archive", False),
    (SymlinkError, "unsafe_archive", False),
    (ArchiveExtractionError, "archive_error", False),
    (FileOperationError, "file_error", False),
    (InvalidModeError, "invalid_argument", False),
    (ConfigurationError, "configuration_error", False),
    (PyNCBIError, "pyncbi_error", False),
    (ValueError, "invalid_argument", False),
    (TypeError, "invalid_argument", False),
    (FileNotFoundError, "file_not_found", False),
    (PermissionError, "permission_denied", False),
    (OSError, "os_error", False),
)

ERROR_CODES: tuple[str, ...] = tuple(dict.fromkeys(code for _, code, _ in _ERROR_CODE_BY_CLASS)) + (
    "internal_error",
)


def error_code_for(exc: BaseException) -> str:
    """Map any exception to a stable, machine-readable error code."""
    if isinstance(exc, HTTPError) and exc.status_code == 404:
        return "not_found"
    for cls, code, _ in _ERROR_CODE_BY_CLASS:
        if isinstance(exc, cls):
            return code
    return "internal_error"


def is_retryable(exc: BaseException) -> bool:
    """Whether an agent should retry the same call later (transient failure)."""
    if isinstance(exc, HTTPError) and exc.status_code in (429, 500, 502, 503, 504):
        return True
    for cls, _, retryable in _ERROR_CODE_BY_CLASS:
        if isinstance(exc, cls):
            return retryable
    return False


def error_payload(exc: BaseException, *, command: str = "", target: str = "") -> dict[str, object]:
    """Build the structured error envelope used by `pyncbi --json` and the MCP server."""
    from PyNCBI.core.formatter import ENVELOPE_SCHEMA_VERSION, _package_version
    from PyNCBI.core.invocation import run_snapshot

    elapsed_ms, request_count = run_snapshot()
    payload: dict[str, object] = {
        "ok": False,
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "pyncbi_version": _package_version(),
        "command": command,
        "target": target,
        "error": type(exc).__name__,
        "error_code": error_code_for(exc),
        "message": redact_url(getattr(exc, "message", None) or str(exc)),
        "summary": redact_url(getattr(exc, "message", None) or str(exc)),
        "data": None,
        "saved_files": [],
        "next_steps": [],
        "warnings": [],
        "elapsed_ms": elapsed_ms,
        "request_count": request_count,
        "hint": getattr(exc, "hint", None),
        "retryable": is_retryable(exc),
    }
    retry_after = getattr(exc, "retry_after", None)
    if retry_after:
        payload["retry_after_sec"] = float(retry_after)
    status = getattr(exc, "status_code", None)
    if status:
        payload["http_status"] = int(status)
    completed = getattr(exc, "completed_paths", None)
    if completed:
        payload["completed_paths"] = [str(p) for p in completed]
    return payload
