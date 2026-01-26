"""Custom exception hierarchy for PyNCBI.

All PyNCBI exceptions inherit from PyNCBIError, allowing users to catch
all library-specific exceptions with a single except clause.

Exception Hierarchy:
    PyNCBIError (base)
    ├── NetworkError
    │   ├── ConnectionFailedError
    │   ├── RequestTimeoutError
    │   ├── HTTPError
    │   └── DownloadError
    ├── DataError
    │   ├── ParseError
    │   │   └── SOFTParseError
    │   ├── NoDataAvailableError
    │   ├── InvalidAccessionError
    │   └── DataProcessingError
    ├── CacheError
    │   ├── CacheCorruptedError
    │   ├── CacheNotFoundError
    │   └── CacheWriteError
    ├── FileOperationError
    │   ├── ArchiveExtractionError
    │   │   ├── PathTraversalError
    │   │   └── SymlinkError
    │   └── FileNotFoundError
    └── ConfigurationError
        ├── InvalidModeError
        └── UnsupportedArrayTypeError

Example:
    from PyNCBI import GSM
    from PyNCBI.exceptions import PyNCBIError, NoDataAvailableError

    try:
        gsm = GSM('GSM123456')
    except NoDataAvailableError as e:
        print(f"No data for {e.accession}")
    except PyNCBIError as e:
        print(f"PyNCBI error: {e}")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class PyNCBIError(Exception):
    """Base exception for all PyNCBI errors.

    All exceptions raised by PyNCBI inherit from this class,
    making it easy to catch all library-specific errors.

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
    """Base for network-related errors.

    Raised when there's a problem communicating with NCBI servers.
    """

    def __init__(self, message: str, *, url: str | None = None, **kwargs: object) -> None:
        self.url = url
        super().__init__(message, **kwargs)  # type: ignore[arg-type]


class ConnectionFailedError(NetworkError):
    """Failed to establish connection to NCBI.

    This typically indicates network issues, firewall blocks, or NCBI being down.
    """

    def __init__(self, url: str, cause: Exception | None = None) -> None:
        self.cause = cause
        message = f"Failed to connect to {url}"
        if cause:
            message = f"{message}: {cause}"
        super().__init__(
            message,
            url=url,
            hint="Check your internet connection or try again later",
        )


class RequestTimeoutError(NetworkError):
    """Request timed out.

    The server didn't respond within the configured timeout period.
    """

    def __init__(self, url: str, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(
            f"Request to {url} timed out after {timeout}s",
            url=url,
            hint="Try increasing timeout via PYNCBI_REQUEST_TIMEOUT environment variable",
        )


class HTTPError(NetworkError):
    """HTTP error response from server.

    Common status codes:
        404: Accession not found (check if ID is correct)
        429: Rate limited (slow down requests)
        500: Server error (try again later)
        503: Service unavailable (NCBI maintenance)
    """

    def __init__(self, url: str, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        detail = f": {message}" if message else ""

        hints = {
            404: "Verify the accession ID is correct",
            429: "You're being rate limited - add delays between requests",
            500: "NCBI server error - try again in a few minutes",
            503: "NCBI service unavailable - may be under maintenance",
        }
        hint = hints.get(status_code, "Check the URL and try again")

        super().__init__(f"HTTP {status_code} from {url}{detail}", url=url, hint=hint)


class DownloadError(NetworkError):
    """Failed to download a file.

    Raised when file download fails after retries.
    """

    def __init__(self, url: str, reason: str) -> None:
        self.reason = reason
        super().__init__(
            f"Failed to download {url}: {reason}",
            url=url,
            hint="Check if the file URL is accessible and try again",
        )


# =============================================================================
# Data Errors
# =============================================================================


class DataError(PyNCBIError):
    """Base for data-related errors.

    Raised when there's a problem with the data itself (parsing, format, etc).
    """

    pass


class ParseError(DataError):
    """Failed to parse data format.

    The data was received but couldn't be interpreted correctly.
    """

    def __init__(self, format_type: str, detail: str = "") -> None:
        self.format_type = format_type
        message = f"Failed to parse {format_type}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


class SOFTParseError(ParseError):
    """Failed to parse SOFT format.

    SOFT (Simple Omnibus Format in Text) is NCBI GEO's metadata format.
    This error indicates the SOFT data is malformed or unexpected.
    """

    def __init__(self, accession: str, detail: str = "") -> None:
        self.accession = accession
        full_detail = f"accession={accession}"
        if detail:
            full_detail = f"{full_detail}, {detail}"
        super().__init__("SOFT", full_detail)


class NoDataAvailableError(DataError):
    """GSM/GSE has no downloadable methylation data.

    Some GEO records exist but don't have downloadable data files.
    The record may be:
    - A metadata-only entry
    - Using a platform we don't support
    - Marked as private/embargoed
    """

    def __init__(self, accession: str) -> None:
        self.accession = accession
        url = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}"
        super().__init__(
            f"No downloadable data available for {accession}",
            hint=f"Check the record at {url} to see what data formats are available",
        )


class InvalidAccessionError(DataError):
    """Invalid GSM/GSE/GPL accession format.

    Accession IDs must follow NCBI's format:
        GSM: GSM followed by digits (e.g., GSM1234567)
        GSE: GSE followed by digits (e.g., GSE12345)
        GPL: GPL followed by digits (e.g., GPL13534)
    """

    def __init__(self, accession: str, expected_prefix: str = "") -> None:
        self.accession = accession
        self.expected_prefix = expected_prefix

        if expected_prefix:
            example = f"{expected_prefix}1234567"
            message = f"Invalid accession '{accession}', expected {expected_prefix}XXXXXX format"
        else:
            example = "GSM1234567, GSE12345, or GPL13534"
            message = f"Invalid accession format: '{accession}'"

        super().__init__(message, hint=f"Example valid accession: {example}")


class DataProcessingError(DataError):
    """Error during data processing (IDAT parsing, etc).

    Raised when downloaded data can't be processed correctly.
    """

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

    pass


class CacheCorruptedError(CacheError):
    """Cache file is corrupted and cannot be read.

    The cached data exists but is unreadable (possibly due to
    interrupted writes or disk errors).
    """

    def __init__(self, path: str, cause: Exception | None = None) -> None:
        self.path = path
        self.cause = cause
        message = f"Cache file corrupted: {path}"
        if cause:
            message = f"{message} ({cause})"
        super().__init__(
            message,
            hint="Delete the cache file and re-fetch the data",
        )


class CacheNotFoundError(CacheError):
    """Expected cache entry not found.

    This shouldn't normally occur in user code - it's used internally
    when cache lookup fails.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Cache entry not found: {key}")


class CacheWriteError(CacheError):
    """Failed to write to cache.

    Possible causes: disk full, permissions, or path issues.
    """

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

    pass


class ArchiveExtractionError(FileOperationError):
    """Failed to extract archive file (tar, gz, etc).

    The archive may be corrupted or contain unsafe entries.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to extract {path}: {reason}")


class PathTraversalError(ArchiveExtractionError):
    """Archive contains path traversal attack.

    The archive contains entries that would extract outside the
    target directory (e.g., '../../../etc/passwd'). This is a
    security measure to prevent malicious archives.
    """

    def __init__(self, path: str, member: str) -> None:
        self.member = member
        super().__init__(path, f"path traversal detected in member '{member}'")


class SymlinkError(ArchiveExtractionError):
    """Archive contains symbolic/hard links.

    For security, we don't extract symlinks or hardlinks from archives
    as they could point to sensitive files outside the extraction directory.
    """

    def __init__(self, path: str, member: str) -> None:
        self.member = member
        super().__init__(path, f"refusing to extract symlink/hardlink '{member}'")


# =============================================================================
# Configuration Errors
# =============================================================================


class ConfigurationError(PyNCBIError):
    """Invalid configuration.

    Raised when PyNCBI is configured incorrectly.
    """

    pass


class InvalidModeError(ConfigurationError):
    """Invalid mode parameter for GSE fetching.

    Valid modes:
        'per_gsm': Fetch each sample individually (slower but more reliable)
        'supp' or 'supplementary': Use supplementary files (faster for large datasets)
    """

    def __init__(self, mode: str, valid_modes: list[str] | None = None) -> None:
        self.mode = mode
        self.valid_modes = valid_modes or ["per_gsm", "supp"]
        super().__init__(
            f"Invalid mode '{mode}'",
            hint=f"Use one of: {', '.join(self.valid_modes)}",
        )


class UnsupportedArrayTypeError(ConfigurationError):
    """Unsupported methylation array platform.

    PyNCBI supports specific Illumina methylation platforms.
    If you need support for additional platforms, consider
    opening a feature request.
    """

    def __init__(self, platform_id: str, supported: list[str] | None = None) -> None:
        self.platform_id = platform_id
        self.supported = supported or ["GPL13534", "GPL16304", "GPL21145", "GPL23976", "GPL8490"]
        super().__init__(
            f"Unsupported array platform '{platform_id}'",
            hint=f"Supported platforms: {', '.join(self.supported)}",
        )


# =============================================================================
# Convenience Exception Groups
# =============================================================================

# All exceptions that indicate temporary issues (worth retrying)
TRANSIENT_ERRORS = (ConnectionFailedError, RequestTimeoutError, HTTPError)

# All exceptions that indicate the user made an error
USER_ERRORS = (InvalidAccessionError, InvalidModeError)

# All exceptions related to data availability
DATA_ERRORS = (NoDataAvailableError, DataProcessingError, ParseError)


__all__ = [
    # Base
    "PyNCBIError",
    # Network
    "NetworkError",
    "ConnectionFailedError",
    "RequestTimeoutError",
    "HTTPError",
    "DownloadError",
    # Data
    "DataError",
    "ParseError",
    "SOFTParseError",
    "NoDataAvailableError",
    "InvalidAccessionError",
    "DataProcessingError",
    # Cache
    "CacheError",
    "CacheCorruptedError",
    "CacheNotFoundError",
    "CacheWriteError",
    # File
    "FileOperationError",
    "ArchiveExtractionError",
    "PathTraversalError",
    "SymlinkError",
    # Configuration
    "ConfigurationError",
    "InvalidModeError",
    "UnsupportedArrayTypeError",
    # Groups
    "TRANSIENT_ERRORS",
    "USER_ERRORS",
    "DATA_ERRORS",
]
