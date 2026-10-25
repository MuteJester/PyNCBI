"""Protocol definitions for PyNCBI.

Defines structural interfaces (Protocols) for the backend client, caching,
parsing, file selection, and progress reporting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from PyNCBI.core.types import DataStatus, GSEInfo, GSMInfo


@runtime_checkable
class ProgressCallback(Protocol):
    """Protocol for progress reporting during file downloads."""

    def __call__(
        self,
        current: int,
        total: int,
        description: str = "",
    ) -> None:
        """Report download progress."""
        ...


@runtime_checkable
class FileSelector(Protocol):
    """Protocol for deterministic selection of supplementary files."""

    def select(
        self,
        files: list[str],
        gse_id: str,
    ) -> int:
        """Select a file from the list (returns 0-based index)."""
        ...


@runtime_checkable
class HttpClient(Protocol):
    """Protocol for NCBI backend HTTP operations."""

    def get_text(
        self,
        url: str,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Fetch URL and return decoded text content."""
        ...

    def get_json(
        self,
        url: str,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch URL and parse as JSON dictionary."""
        ...

    def get_bytes(
        self,
        url: str,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        """Fetch URL and return raw bytes."""
        ...

    def get_range_bytes(
        self,
        url: str,
        max_bytes: int = 32768,
        timeout: float | None = None,
    ) -> bytes:
        """Fetch only the first `max_bytes` of a remote file."""
        ...

    def inspect_url(
        self,
        url: str,
        source: str = "supplementary",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Inspect remote file size and headers via HTTP HEAD."""
        ...

    def download_file(
        self,
        url: str,
        destination: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        """Download file to destination path."""
        ...

    def stream_lines(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Iterator[str]:
        """Stream a remote (optionally gzip-compressed) text resource line by line."""
        ...


@runtime_checkable
class Cache(Protocol[T]):
    """Protocol for caching operations."""

    def get(self, key: str) -> T | None:
        """Get cached item or None."""
        ...

    def set(self, key: str, value: T) -> None:
        """Store item in cache."""
        ...

    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        ...

    def delete(self, key: str) -> bool:
        """Delete item from cache."""
        ...


@runtime_checkable
class Parser(Protocol):
    """Protocol for SOFT format parsing."""

    def parse_gsm(self, soft_text: str, gsm_id: str) -> GSMInfo:
        """Parse GSM SOFT text into structured metadata."""
        ...

    def parse_gse(self, soft_text: str, gse_id: str) -> GSEInfo:
        """Parse GSE SOFT text into structured metadata."""
        ...

    def extract_gsm_ids(self, gse_soft_text: str) -> list[str]:
        """Extract GSM IDs from GSE SOFT text."""
        ...

    def detect_data_status(self, gsm_soft_text: str) -> DataStatus:
        """Detect data availability status for a GSM."""
        ...
