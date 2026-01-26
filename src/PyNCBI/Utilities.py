"""Utility functions for PyNCBI.

This module provides helper functions for NCBI GEO data processing.
Most functions delegate to the new modular architecture.

Note:
    For new code, prefer using the modular APIs:
    - PyNCBI.io.http_client for HTTP operations
    - PyNCBI.io.compression for compression operations
    - PyNCBI.io.cache for caching operations
    - PyNCBI.parsing.soft_parser for SOFT parsing
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from PyNCBI.config import get_config
from PyNCBI.io.cache import CompressedPickleCache
from PyNCBI.io.compression import (
    extract_tarfile,
    gunzip_file,
)
from PyNCBI.io.compression import (
    remove_non_idat_files as _remove_non_idat,
)
from PyNCBI.io.http_client import get_default_client
from PyNCBI.logging import get_logger
from PyNCBI.parsing.soft_parser import get_parser, parse_characteristics

logger = get_logger(__name__)

# Re-export parse_characteristics from soft_parser
__all__ = [
    "parse_characteristics",
    "gse_of_gsm",
    "platform_of_gsm",
    "is_info_dataframe_in_downloads",
    "get_data_locally",
    "progress_bar",
    "gsm_data_file_table_start",
    "gunzip_shutil",
    "gsm_page_data_status",
    "download_gsm_data",
    "compress_and_store",
    "load_and_decompress",
    "gsms_from_gse_soft",
    "parse_and_compress_gse_info",
    "chunkify",
    "unzip_tarfile",
    "remove_non_idat_files",
]


def gse_of_gsm(gsm_number: str) -> str | list[str]:
    """Get the GSE ID(s) associated with a GSM.

    Args:
        gsm_number: GSM identifier (e.g., 'GSM1234567')

    Returns:
        Single GSE ID string if unique, or list of GSE IDs if multiple
    """
    config = get_config()
    client = get_default_client()

    url = config.get_ncbi_query_url(gsm_number)
    response = client.get_text(url)

    gses = list(set(re.findall(r"GSE[0-9]+", response)))
    return gses[0] if len(gses) == 1 else gses


def platform_of_gsm(gsm_number: str) -> str | list[str]:
    """Get the platform GPL ID(s) for a GSM.

    Args:
        gsm_number: GSM identifier

    Returns:
        Single GPL ID string if unique, or list of GPL IDs if multiple
    """
    config = get_config()
    client = get_default_client()

    url = config.get_ncbi_query_url(gsm_number)
    response = client.get_text(url)

    platforms = list(set(re.findall(r"GPL[0-9]+", response)))
    return platforms[0] if len(platforms) == 1 else platforms


def is_info_dataframe_in_downloads(gse: str) -> bool:
    """Check if a GSE info CSV file exists in Downloads folder.

    Args:
        gse: GSE identifier

    Returns:
        True if the file exists
    """
    file_path = Path.home() / "Downloads" / f"{gse}_INFO.csv"
    return file_path.exists()


def get_data_locally(link: str, timeout: float | None = None) -> str:  # noqa: ARG001
    """Download text content from a URL.

    Args:
        link: URL to download from
        timeout: Request timeout in seconds (uses default if None)

    Returns:
        Text content of the response
    """
    client = get_default_client()
    return client.get_text(link)


def progress_bar(current: int, total: int, width: int = 80) -> None:  # noqa: ARG001
    """Display a progress bar (legacy wget utility).

    Args:
        current: Current progress value
        total: Total progress value
        width: Width of progress bar (unused, kept for compatibility)
    """
    progress_message = "Downloading: %d%% [%d / %d] bytes" % (
        current / total * 100,
        current,
        total,
    )
    sys.stdout.write("\r" + progress_message)
    sys.stdout.flush()


def gsm_data_file_table_start(gsm_file: str) -> int:
    """Find the line number where data starts in a GSM file.

    Args:
        gsm_file: Text content of GSM data file

    Returns:
        Line number where data table begins

    Raises:
        ValueError: If data start not found in first 10 lines
    """
    parser = get_parser()
    return parser.find_data_table_start(gsm_file)


def gunzip_shutil(
    source_filepath: str | Path,
    dest_filepath: str | Path,
    block_size: int = 65536,  # noqa: ARG001
) -> None:
    """Decompress a gzip file.

    Args:
        source_filepath: Path to gzip file
        dest_filepath: Path for decompressed output
        block_size: Read block size (unused, kept for compatibility)
    """
    gunzip_file(Path(source_filepath), Path(dest_filepath), delete_source=False)


def gsm_page_data_status(gsm: str) -> int:
    """Check data availability status for a GSM.

    Args:
        gsm: GSM identifier

    Returns:
        0: Data on page
        1: IDAT files available
        -1: No data available
    """
    from PyNCBI._types import DataStatus

    config = get_config()
    client = get_default_client()
    parser = get_parser()

    url = config.get_ncbi_query_url(gsm)
    soft_text = client.get_text(url)

    status = parser.detect_data_status(soft_text)

    status_map = {
        DataStatus.DATA_ON_PAGE: 0,
        DataStatus.IDAT_FILES: 1,
        DataStatus.NO_DATA: -1,
    }
    return status_map.get(status, -1)


def download_gsm_data(
    gsm: str,
    to_path: str | None = None,
    return_file_names: bool = False,
) -> list[str] | None:
    """Download methylation data for a GSM.

    Args:
        gsm: GSM identifier
        to_path: Destination folder (default: cache folder)
        return_file_names: If True, return list of saved file names

    Returns:
        List of file names if return_file_names=True, else None
    """
    config = get_config()
    client = get_default_client()

    data_status = gsm_page_data_status(gsm)
    save_path = Path(to_path) if to_path else config.ensure_cache_dir()
    save_path.mkdir(parents=True, exist_ok=True)

    if data_status == 1:  # IDAT files
        # Get SOFT data
        url = config.get_ncbi_query_url(gsm)
        soft_text = client.get_text(url)

        # Find supplementary file URLs
        sup_files = re.findall(r"!Sample_supplementary_file = (.+)", soft_text)
        sup_files = [f.strip() for f in sup_files]

        file_names = []
        for file_url in sup_files:
            filename = file_url.split("/")[-1]
            dest = save_path / filename

            # Download
            client.download_file(file_url, dest)

            # Decompress if gzipped
            if filename.endswith(".gz"):
                decompressed = dest.with_suffix("")
                gunzip_file(dest, decompressed, delete_source=True)
                file_names.append(decompressed.name)
            else:
                file_names.append(filename)

        if return_file_names:
            return file_names

    elif data_status == 0:  # Data on page
        # Download probe data
        data_url = f"{config.NCBI_QUERY_BASE_URL}?acc={gsm}&targ=self&form=text&view=data"
        probe_data = client.get_text(data_url)

        # Parse data
        data_start = gsm_data_file_table_start(probe_data)
        lines = probe_data.split("\n")
        data_text = "\n".join(lines[data_start:-2])

        df = pd.read_table(StringIO(data_text))
        df = df.rename(columns={"ID_REF": "probe", "VALUE": gsm})
        df[gsm] = df[gsm].astype(np.float32)

        # Save
        csv_path = save_path / f"{gsm}.csv"
        df.to_csv(csv_path, index=False)

        if return_file_names:
            return [f"{gsm}.csv"]

    else:
        logger.warning("No data exists on GSM card for %s", gsm)

    return None


def compress_and_store(data: Any, path: str | Path) -> None:
    """Compress and store data using zlib/pickle.

    Args:
        data: Data to store
        path: Destination file path
    """
    cache = CompressedPickleCache()
    # Extract key from path
    key = Path(path).stem
    cache.set(key, data)


def load_and_decompress(path: str | Path) -> Any:
    """Load and decompress data from a compressed pickle file.

    Args:
        path: Path to compressed file

    Returns:
        Decompressed data
    """
    import pickle
    import zlib

    with open(path, "rb") as handler:
        zbytes = handler.read()
    data = zlib.decompress(zbytes)
    return pickle.loads(data)


def gsms_from_gse_soft(gse_soft_file: str) -> list[str]:
    """Extract GSM IDs from GSE SOFT format text.

    Args:
        gse_soft_file: SOFT format text for a GSE

    Returns:
        List of GSM IDs
    """
    parser = get_parser()
    return parser.extract_gsm_ids(gse_soft_file)


def parse_and_compress_gse_info(gse_soft_file: str) -> pd.Series:
    """Parse GSE SOFT text to pandas Series.

    Args:
        gse_soft_file: SOFT format text for a GSE

    Returns:
        pandas Series with parsed metadata
    """
    parser = get_parser()
    return parser.parse_gse_to_series(gse_soft_file)


def chunkify(lst: list, n: int) -> Iterator[list]:
    """Yield successive n-sized chunks from a list.

    Args:
        lst: List to chunk
        n: Chunk size

    Yields:
        List chunks of size n
    """
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def unzip_tarfile(file: str | Path, dest_folder: str | Path) -> None:
    """Extract a tar file safely.

    Args:
        file: Path to tar file (can include glob pattern)
        dest_folder: Destination folder

    Raises:
        PathTraversalError: If tar contains path traversal attempts
    """
    import glob as glob_module

    for f in glob_module.glob(str(file)):
        extract_tarfile(Path(f), Path(dest_folder))


def remove_non_idat_files(folder_path: str | Path) -> None:
    """Remove all non-IDAT files from a folder.

    Also decompresses any .idat.gz files.

    Args:
        folder_path: Path to folder to clean
    """
    _remove_non_idat(Path(folder_path))
