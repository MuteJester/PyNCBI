"""Configuration management for PyNCBI.

This module provides typed configuration with environment variable support
and runtime configurability. It replaces the old Constants.py module.

Environment Variables:
    PYNCBI_CACHE_DIR: Override default cache directory
    PYNCBI_REQUEST_TIMEOUT: Override default HTTP timeout (seconds)
    XDG_CACHE_HOME: Standard XDG cache directory (fallback)

Example:
    from PyNCBI.config import get_config, Config

    # Get current configuration
    config = get_config()
    print(config.cache_folder)

    # Create custom configuration
    custom_config = Config(request_timeout=60.0)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from PyNCBI._types import ArrayType


def _get_default_cache_folder() -> Path:
    """Determine the default cache folder location.

    Priority:
        1. PYNCBI_CACHE_DIR environment variable
        2. XDG_CACHE_HOME/PyNCBI (Linux standard)
        3. ~/.cache/PyNCBI (fallback)
    """
    if env_cache := os.environ.get("PYNCBI_CACHE_DIR"):
        return Path(env_cache)

    if xdg_cache := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg_cache) / "PyNCBI"

    return Path.home() / ".cache" / "PyNCBI"


def _get_default_timeout() -> float:
    """Get default request timeout from environment or use default."""
    env_timeout = os.environ.get("PYNCBI_REQUEST_TIMEOUT")
    if env_timeout:
        try:
            return float(env_timeout)
        except ValueError:
            pass
    return 30.0


@dataclass
class Config:
    """PyNCBI configuration.

    Attributes:
        cache_folder: Directory for caching downloaded data
        downloads_folder: Directory for downloads (temporary files)
        request_timeout: HTTP request timeout in seconds
        max_retries: Maximum number of retry attempts for failed requests
        retry_backoff: Backoff factor for retries (seconds)
    """

    cache_folder: Path = field(default_factory=_get_default_cache_folder)
    downloads_folder: Path = field(default_factory=lambda: Path.home() / "Downloads")
    request_timeout: float = field(default_factory=_get_default_timeout)
    max_retries: int = 3
    retry_backoff: float = 0.5

    # NCBI API URLs
    NCBI_QUERY_BASE_URL: ClassVar[str] = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
    NCBI_QUERY_URL: ClassVar[str] = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc="
    NCBI_QUERY_URL_TEMPLATE: ClassVar[str] = (
        "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?"
        "acc={accession}&targ=self&form=text&view=quick"
    )

    # Sample sheet columns required for IDAT processing
    SAMPLE_SHEET_COLUMNS: ClassVar[list[str]] = [
        "GSM_ID",
        "Sample_Name",
        "Sentrix_ID",
        "Sentrix_Position",
    ]

    # Mapping from GPL platform IDs to array types
    ARRAY_TYPE_MAPPING: ClassVar[dict[str, ArrayType]] = {
        "GPL13534": ArrayType.ARRAY_450K,
        "GPL16304": ArrayType.ARRAY_450K,
        "GPL21145": ArrayType.EPIC,
        "GPL23976": ArrayType.EPIC,
        "GPL8490": ArrayType.ARRAY_27K,
    }

    def __post_init__(self) -> None:
        """Ensure paths are Path objects and create cache directory."""
        if isinstance(self.cache_folder, str):
            self.cache_folder = Path(self.cache_folder)
        if isinstance(self.downloads_folder, str):
            self.downloads_folder = Path(self.downloads_folder)

    @property
    def local_downloads_folder(self) -> Path:
        """Alias for downloads_folder (backward compatibility)."""
        return self.downloads_folder

    def ensure_cache_dir(self) -> Path:
        """Ensure cache directory exists and return its path."""
        self.cache_folder.mkdir(parents=True, exist_ok=True)
        return self.cache_folder

    def get_ncbi_query_url(self, accession: str) -> str:
        """Get the NCBI query URL for an accession.

        Args:
            accession: GSM, GSE, or GPL accession ID

        Returns:
            Formatted URL for querying NCBI
        """
        return self.NCBI_QUERY_URL_TEMPLATE.format(accession=accession)

    def get_array_type(self, platform_id: str) -> ArrayType | None:
        """Get array type from GPL platform ID.

        Args:
            platform_id: NCBI GPL identifier (e.g., 'GPL13534')

        Returns:
            ArrayType if platform is supported, None otherwise
        """
        return self.ARRAY_TYPE_MAPPING.get(platform_id.upper())


# Global configuration instance
_config: Config | None = None


def get_config() -> Config:
    """Get the global configuration instance.

    Returns:
        The current Config instance (creates default if not set)
    """
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance.

    Args:
        config: The Config instance to use globally
    """
    global _config
    _config = config


def reset_config() -> None:
    """Reset configuration to defaults."""
    global _config
    _config = None


# --- Backward Compatibility Exports ---
# These are deprecated and will be removed in a future version.
# Use get_config() instead.


@lru_cache(maxsize=1)
def _get_legacy_constants() -> dict[str, str | float | list[str] | dict[str, str]]:
    """Get legacy constant values (cached)."""
    config = get_config()
    return {
        "NCBI_QUERY_BASE_URL": config.NCBI_QUERY_BASE_URL,
        "NCBI_QUERY_URL": config.NCBI_QUERY_URL_TEMPLATE.replace("{accession}", "{}"),
        "LOCAL_DOWNLOADS_FOLDER": str(config.downloads_folder) + "/",
        "CACHE_FOLDER": str(config.cache_folder) + "/",
        "DEFAULT_REQUEST_TIMEOUT": config.request_timeout,
        "SAMPLE_SHEET_COLUMNS": config.SAMPLE_SHEET_COLUMNS,
        "ARRAY_TYPES": {k: v.value for k, v in config.ARRAY_TYPE_MAPPING.items()},
    }


# Legacy constant names (deprecated)
def __getattr__(name: str) -> str | float | list[str] | dict[str, str]:
    """Provide backward-compatible access to legacy constants."""
    constants = _get_legacy_constants()
    if name in constants:
        import warnings

        warnings.warn(
            f"Accessing '{name}' directly is deprecated. "
            f"Use 'from PyNCBI.config import get_config' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return constants[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
