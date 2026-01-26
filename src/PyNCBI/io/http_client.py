"""HTTP client for PyNCBI.

This module provides a unified HTTP client that replaces the mixed use
of requests and wget in the original codebase.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from PyNCBI.exceptions import ConnectionFailedError, HTTPError, RequestTimeoutError

if TYPE_CHECKING:
    from PyNCBI._types import ProgressCallback


@dataclass
class HttpClientConfig:
    """Configuration for HTTP client.

    Attributes:
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts
        retry_backoff: Backoff factor between retries
        retry_statuses: HTTP status codes to retry on
    """

    timeout: float = 30.0
    max_retries: int = 3
    retry_backoff: float = 0.5
    retry_statuses: tuple[int, ...] = (500, 502, 503, 504)


class HttpClient:
    """Unified HTTP client for NCBI requests.

    This client provides:
    - Automatic retries with exponential backoff
    - Proper timeout handling
    - Progress callbacks for downloads
    - Consistent error handling

    Example:
        client = HttpClient()
        text = client.get_text("https://example.com/data.txt")

        # With progress callback
        def on_progress(current, total, desc):
            print(f"{desc}: {current}/{total}")

        client.download_file(url, Path("output.txt"), progress_callback=on_progress)
    """

    def __init__(self, config: HttpClientConfig | None = None) -> None:
        """Initialize the HTTP client.

        Args:
            config: Client configuration (uses defaults if not provided)
        """
        self.config = config or HttpClientConfig()
        self._session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry configuration."""
        session = requests.Session()
        retry = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.retry_backoff,
            status_forcelist=self.config.retry_statuses,
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def get_text(self, url: str, timeout: float | None = None) -> str:
        """Fetch URL content as text.

        Args:
            url: URL to fetch
            timeout: Request timeout (uses config default if not specified)

        Returns:
            Response text content

        Raises:
            ConnectionFailedError: If connection fails
            RequestTimeoutError: If request times out
            HTTPError: If server returns error status
        """
        timeout = timeout or self.config.timeout
        try:
            response = self._session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.exceptions.Timeout as e:
            raise RequestTimeoutError(url, timeout) from e
        except requests.exceptions.ConnectionError as e:
            raise ConnectionFailedError(url, e) from e
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            raise HTTPError(url, status, str(e)) from e

    def get_bytes(self, url: str, timeout: float | None = None) -> bytes:
        """Fetch URL content as bytes.

        Args:
            url: URL to fetch
            timeout: Request timeout (uses config default if not specified)

        Returns:
            Response content as bytes

        Raises:
            ConnectionFailedError: If connection fails
            RequestTimeoutError: If request times out
            HTTPError: If server returns error status
        """
        timeout = timeout or self.config.timeout
        try:
            response = self._session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content
        except requests.exceptions.Timeout as e:
            raise RequestTimeoutError(url, timeout) from e
        except requests.exceptions.ConnectionError as e:
            raise ConnectionFailedError(url, e) from e
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            raise HTTPError(url, status, str(e)) from e

    def download_file(
        self,
        url: str,
        destination: Path,
        progress_callback: ProgressCallback | None = None,
        chunk_size: int = 8192,
    ) -> Path:
        """Download file with optional progress reporting.

        Args:
            url: URL to download from
            destination: Local file path to save to
            progress_callback: Optional callback for progress updates
            chunk_size: Size of chunks to download

        Returns:
            Path to the downloaded file

        Raises:
            ConnectionFailedError: If connection fails
            RequestTimeoutError: If request times out
            HTTPError: If server returns error status
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            with self._session.get(
                url, stream=True, timeout=self.config.timeout
            ) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                current = 0

                with open(destination, "wb") as f:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
                            current += len(chunk)
                            if progress_callback:
                                progress_callback(current, total, destination.name)

        except requests.exceptions.Timeout as e:
            raise RequestTimeoutError(url, self.config.timeout) from e
        except requests.exceptions.ConnectionError as e:
            raise ConnectionFailedError(url, e) from e
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            raise HTTPError(url, status, str(e)) from e

        return destination

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()

    def __enter__(self) -> HttpClient:
        """Context manager entry."""
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit."""
        self.close()


# Default progress callback for wget-style output
def _default_progress_callback(current: int, total: int, description: str = "") -> None:
    """Default wget-style progress callback."""
    if total > 0:
        percent = current / total * 100
        message = f"Downloading: {percent:.0f}% [{current:,} / {total:,}] bytes"
    else:
        message = f"Downloading: {current:,} bytes"
    sys.stdout.write("\r" + message)
    sys.stdout.flush()


# Singleton default client
_default_client: HttpClient | None = None


def get_default_client() -> HttpClient:
    """Get the default HTTP client singleton.

    Returns:
        Default HttpClient instance
    """
    global _default_client
    if _default_client is None:
        _default_client = HttpClient()
    return _default_client
