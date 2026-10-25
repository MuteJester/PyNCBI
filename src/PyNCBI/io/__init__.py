"""I/O package for PyNCBI.

Contains the production `NCBIClient` (`HttpClient`), realm-aware token-bucket
rate limiters, request telemetry, caching, and safe archive decompression.
"""

from PyNCBI.io.cache import (
    CacheEntry,
    CompressedPickleCache,
    LocalCache,
    decode_cache_value,
    encode_cache_value,
)
from PyNCBI.io.compression import extract_tarfile, extract_zipfile, gunzip_file
from PyNCBI.io.http_client import (
    ClientMetrics,
    HttpClient,
    HttpClientConfig,
    NCBIClient,
    NCBIClientConfig,
    RequestEvent,
    format_size_human,
    get_client,
    get_default_client,
    infer_file_type,
    looks_like_bot_challenge,
    normalize_ncbi_url,
)
from PyNCBI.io.rate_limiter import NCBIRealmLimiter, TokenBucketLimiter, classify_ncbi_realm
from PyNCBI.io.response_cache import ResponseCache

__all__ = [
    "NCBIClient",
    "NCBIClientConfig",
    "HttpClient",
    "HttpClientConfig",
    "ClientMetrics",
    "RequestEvent",
    "get_client",
    "get_default_client",
    "normalize_ncbi_url",
    "format_size_human",
    "infer_file_type",
    "looks_like_bot_challenge",
    "TokenBucketLimiter",
    "NCBIRealmLimiter",
    "classify_ncbi_realm",
    "ResponseCache",
    "CompressedPickleCache",
    "LocalCache",
    "encode_cache_value",
    "decode_cache_value",
    "CacheEntry",
    "gunzip_file",
    "extract_tarfile",
    "extract_zipfile",
]
