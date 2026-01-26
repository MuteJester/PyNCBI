"""I/O layer for PyNCBI.

This package contains modules for:
- HTTP client operations
- Caching with inspection API
- Compression utilities (gzip, zlib, tar)
"""

from PyNCBI.io.cache import CacheEntry, CompressedPickleCache
from PyNCBI.io.compression import (
    compress_data,
    decompress_data,
    extract_tarfile,
    gunzip_file,
)
from PyNCBI.io.http_client import HttpClient, HttpClientConfig

__all__ = [
    # HTTP
    "HttpClient",
    "HttpClientConfig",
    # Cache
    "CompressedPickleCache",
    "CacheEntry",
    # Compression
    "compress_data",
    "decompress_data",
    "gunzip_file",
    "extract_tarfile",
]
