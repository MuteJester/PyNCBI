"""PyNCBI - Simple API for Python Integration with NCBI GEO Database.

This package provides tools for downloading and processing DNA methylation
data from NCBI's Gene Expression Omnibus (GEO) database.

Main Classes:
    GSM: Represents a single Gene Sample Microarray
    GSE: Represents a Gene Series Expression (collection of samples)
    GEOReader: Low-level REST API connector

New Architecture (v0.2+):
    Services: GSMService, GSEService for modern dependency-injected usage
    Types: DataStatus, FetchMode enums for type-safe API
    Exceptions: PyNCBIError hierarchy for proper error handling

Example:
    # Simple usage (legacy API)
    from PyNCBI import GSM, GSE

    gsm = GSM('GSM1234567')
    print(gsm.info)

    gse = GSE('GSE12345', mode='per_gsm')
    print(gse['GSM1234567'].data)

    # Modern usage (v0.2+)
    from PyNCBI import FetchMode
    from PyNCBI.services import GSMService, GSEService

    service = GSMService()
    data = service.fetch('GSM1234567')
"""

from __future__ import annotations

__version__ = "0.2.0"
__author__ = "Thomas Konstantinovsky"

from pathlib import Path

# --- New architecture exports ---
from PyNCBI._types import (
    ArrayType,
    DataStatus,
    FetchMode,
    FirstFileSelector,
    GSEInfo,
    GSMInfo,
    IndexFileSelector,
    InteractiveFileSelector,
)
from PyNCBI.config import Config, get_config, set_config
from PyNCBI.logging import (
    LogLevel,
    configure_logging,
    get_logger,
    log_level,
    set_level,
    silence,
    verbose,
)

# --- Legacy exports (backward compatibility) ---
from PyNCBI.Constants import (
    ARRAY_TYPES,
    CACHE_FOLDER,
    DEFAULT_REQUEST_TIMEOUT,
    LOCAL_DOWNLOADS_FOLDER,
    NCBI_QUERY_BASE_URL,
    NCBI_QUERY_URL,
    SAMPLE_SHEET_COLUMNS,
)
from PyNCBI.exceptions import (
    CacheCorruptedError,
    CacheError,
    CacheNotFoundError,
    CacheWriteError,
    ConfigurationError,
    ConnectionFailedError,
    DataError,
    DataProcessingError,
    DownloadError,
    HTTPError,
    InvalidAccessionError,
    InvalidModeError,
    NetworkError,
    NoDataAvailableError,
    ParseError,
    PathTraversalError,
    PyNCBIError,
    RequestTimeoutError,
    SOFTParseError,
    SymlinkError,
    UnsupportedArrayTypeError,
)
from PyNCBI.FileUtilities import (
    check_for_sample_sheet,
    generate_sample_sheet,
    parse_idat_files,
)
from PyNCBI.GEOReader import GEOReader
from PyNCBI.GSE import GSE
from PyNCBI.GSM import GSM
from PyNCBI.Utilities import (
    compress_and_store,
    download_gsm_data,
    get_data_locally,
    gse_of_gsm,
    gsm_data_file_table_start,
    gsm_page_data_status,
    gsms_from_gse_soft,
    gunzip_shutil,
    load_and_decompress,
    parse_and_compress_gse_info,
    parse_characteristics,
    platform_of_gsm,
)

# Create cache folder on import
Path(get_config().cache_folder).mkdir(parents=True, exist_ok=True)

__all__ = [
    # Version info
    "__version__",
    "__author__",
    # Main classes (legacy)
    "GSM",
    "GSE",
    "GEOReader",
    # New types and enums
    "DataStatus",
    "FetchMode",
    "ArrayType",
    "GSMInfo",
    "GSEInfo",
    # File selectors
    "FirstFileSelector",
    "IndexFileSelector",
    "InteractiveFileSelector",
    # Configuration
    "Config",
    "get_config",
    "set_config",
    # Logging
    "LogLevel",
    "configure_logging",
    "get_logger",
    "set_level",
    "silence",
    "verbose",
    "log_level",
    # Exceptions
    "PyNCBIError",
    "NetworkError",
    "ConnectionFailedError",
    "RequestTimeoutError",
    "HTTPError",
    "DownloadError",
    "DataError",
    "ParseError",
    "SOFTParseError",
    "NoDataAvailableError",
    "InvalidAccessionError",
    "DataProcessingError",
    "CacheError",
    "CacheCorruptedError",
    "CacheNotFoundError",
    "CacheWriteError",
    "ConfigurationError",
    "InvalidModeError",
    "UnsupportedArrayTypeError",
    "PathTraversalError",
    "SymlinkError",
    # Legacy utilities
    "parse_characteristics",
    "gse_of_gsm",
    "platform_of_gsm",
    "get_data_locally",
    "gsm_page_data_status",
    "download_gsm_data",
    "gsm_data_file_table_start",
    "compress_and_store",
    "load_and_decompress",
    "gsms_from_gse_soft",
    "parse_and_compress_gse_info",
    "gunzip_shutil",
    # File utilities
    "check_for_sample_sheet",
    "generate_sample_sheet",
    "parse_idat_files",
    # Legacy constants
    "NCBI_QUERY_BASE_URL",
    "NCBI_QUERY_URL",
    "CACHE_FOLDER",
    "LOCAL_DOWNLOADS_FOLDER",
    "DEFAULT_REQUEST_TIMEOUT",
    "SAMPLE_SHEET_COLUMNS",
    "ARRAY_TYPES",
]
