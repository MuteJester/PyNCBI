"""Protocol definitions for PyNCBI.

This module defines abstract interfaces (Protocols) for the main
components of PyNCBI, enabling dependency injection and testability.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:

    from PyNCBI._types import DataStatus, GSEInfo, GSMInfo

T = TypeVar("T")


@runtime_checkable
class HttpClient(Protocol):
    """Protocol for HTTP operations.

    Any class implementing this protocol can be used as an HTTP client
    in PyNCBI services.
    """

    def get_text(self, url: str, timeout: float | None = None) -> str:
        """Fetch URL and return text content.

        Args:
            url: URL to fetch
            timeout: Optional timeout in seconds

        Returns:
            Response text content

        Raises:
            NetworkError: On connection failures
        """
        ...

    def download_file(
        self,
        url: str,
        destination: Path,
        progress_callback: Any | None = None,
    ) -> Path:
        """Download file to destination path.

        Args:
            url: URL to download from
            destination: Local file path
            progress_callback: Optional progress callback

        Returns:
            Path to downloaded file
        """
        ...


@runtime_checkable
class Cache(Protocol[T]):
    """Protocol for caching operations.

    Any class implementing this protocol can be used as a cache
    in PyNCBI services.
    """

    def get(self, key: str) -> T | None:
        """Get cached item or None.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        ...

    def set(self, key: str, value: T) -> None:
        """Store item in cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        ...

    def exists(self, key: str) -> bool:
        """Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists
        """
        ...

    def delete(self, key: str) -> bool:
        """Delete cached item.

        Args:
            key: Cache key

        Returns:
            True if item existed and was deleted
        """
        ...


@runtime_checkable
class Parser(Protocol):
    """Protocol for SOFT format parsing.

    Any class implementing this protocol can be used as a parser
    in PyNCBI services.
    """

    def parse_gsm(self, soft_text: str, gsm_id: str | None = None) -> GSMInfo:
        """Parse GSM SOFT text into structured data.

        Args:
            soft_text: Raw SOFT format text
            gsm_id: Optional GSM ID

        Returns:
            Parsed GSM information
        """
        ...

    def parse_gse(self, soft_text: str, gse_id: str | None = None) -> GSEInfo:
        """Parse GSE SOFT text into structured data.

        Args:
            soft_text: Raw SOFT format text
            gse_id: Optional GSE ID

        Returns:
            Parsed GSE information
        """
        ...

    def extract_gsm_ids(self, gse_soft_text: str) -> list[str]:
        """Extract GSM IDs from GSE SOFT text.

        Args:
            gse_soft_text: Raw SOFT format text for a GSE

        Returns:
            List of GSM IDs
        """
        ...

    def detect_data_status(self, gsm_soft_text: str) -> DataStatus:
        """Detect data availability status for a GSM.

        Args:
            gsm_soft_text: Raw SOFT format text

        Returns:
            Data availability status
        """
        ...
