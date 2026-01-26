"""Constants for PyNCBI.

This module re-exports configuration values from the config module
for backward compatibility.

Note:
    For new code, prefer using:
        from PyNCBI.config import get_config
        config = get_config()
"""

from __future__ import annotations

from PyNCBI.config import get_config

# Get the singleton config instance
_config = get_config()

# Re-export constants for backward compatibility
NCBI_QUERY_BASE_URL: str = _config.NCBI_QUERY_BASE_URL
NCBI_QUERY_URL: str = _config.NCBI_QUERY_URL
SAMPLE_SHEET_COLUMNS: list[str] = _config.SAMPLE_SHEET_COLUMNS
LOCAL_DOWNLOADS_FOLDER: str = str(_config.local_downloads_folder)
CACHE_FOLDER: str = str(_config.cache_folder) + "/"
DEFAULT_REQUEST_TIMEOUT: float = _config.request_timeout

# Array type mapping (GPL ID to array name)
ARRAY_TYPES: dict[str, str] = {
    gpl: array_type.value for gpl, array_type in _config.ARRAY_TYPE_MAPPING.items()
}

__all__ = [
    "NCBI_QUERY_BASE_URL",
    "NCBI_QUERY_URL",
    "SAMPLE_SHEET_COLUMNS",
    "LOCAL_DOWNLOADS_FOLDER",
    "CACHE_FOLDER",
    "DEFAULT_REQUEST_TIMEOUT",
    "ARRAY_TYPES",
]
