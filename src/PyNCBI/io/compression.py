"""Compression utilities for PyNCBI.

This module provides functions for compressing, decompressing, and
extracting archive files (gzip, zlib, tar).
"""

from __future__ import annotations

import gzip
import io
import pickle
import shutil
import zlib
from pathlib import Path
from typing import Any

from PyNCBI.exceptions import ArchiveExtractionError, PathTraversalError


def compress_data(data: Any) -> bytes:
    """Compress data using pickle and zlib.

    Args:
        data: Any picklable Python object

    Returns:
        Compressed bytes
    """
    buffer = io.BytesIO()
    pickle.dump(data, buffer)
    return zlib.compress(buffer.getbuffer())


def decompress_data(compressed: bytes) -> Any:
    """Decompress data compressed with compress_data.

    Args:
        compressed: Compressed bytes from compress_data

    Returns:
        Original Python object

    Raises:
        zlib.error: If decompression fails
        pickle.UnpicklingError: If unpickling fails
    """
    decompressed = zlib.decompress(compressed)
    return pickle.loads(decompressed)


def compress_and_store(data: Any, path: str | Path) -> None:
    """Compress data and store to file.

    Args:
        data: Any picklable Python object
        path: Destination file path
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    compressed = compress_data(data)
    path.write_bytes(compressed)


def load_and_decompress(path: str | Path) -> Any:
    """Load and decompress data from file.

    Args:
        path: Source file path

    Returns:
        Original Python object
    """
    path = Path(path)
    compressed = path.read_bytes()
    return decompress_data(compressed)


def gunzip_file(
    source: str | Path,
    destination: str | Path,
    block_size: int = 65536,
    delete_source: bool = False,
) -> Path:
    """Decompress a gzip file.

    Args:
        source: Path to the gzipped file
        destination: Path for the decompressed output
        block_size: Block size for copying (default 64KB)
        delete_source: Whether to delete the source file after decompression

    Returns:
        Path to the decompressed file
    """
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with gzip.open(source, "rb") as src, open(destination, "wb") as dst:
        shutil.copyfileobj(src, dst, block_size)

    if delete_source:
        source.unlink()

    return destination


def extract_tarfile(
    archive: str | Path,
    destination: str | Path,
    safe: bool = True,
) -> Path:
    """Extract a tar archive safely.

    Args:
        archive: Path to the tar archive
        destination: Directory to extract into
        safe: If True (default), check for path traversal attacks

    Returns:
        Path to the extraction directory

    Raises:
        PathTraversalError: If archive contains path traversal
        ArchiveExtractionError: If archive contains symlinks/hardlinks
    """
    import tarfile

    archive = Path(archive)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive) as tar:
        if safe:
            for member in tar.getmembers():
                # Check for symlinks and hardlinks
                if member.islnk() or member.issym():
                    raise ArchiveExtractionError(
                        str(archive),
                        f"refusing to extract symlink or hardlink: {member.name}",
                    )

                # Check for path traversal
                member_path = (destination / member.name).resolve()
                if destination not in member_path.parents and member_path != destination:
                    raise PathTraversalError(str(archive), member.name)

        tar.extractall(destination)

    return destination


def remove_non_idat_files(folder: str | Path) -> list[Path]:
    """Remove all non-.idat files from a folder.

    Gzipped .idat files are decompressed in place.

    Args:
        folder: Path to the folder

    Returns:
        List of remaining .idat file paths
    """
    folder = Path(folder)
    remaining = []

    for file_path in folder.iterdir():
        if not file_path.is_file():
            continue

        if ".idat" not in file_path.name:
            file_path.unlink()
        elif file_path.suffix == ".gz":
            # Decompress gzipped idat file
            decompressed = file_path.with_suffix("")
            gunzip_file(file_path, decompressed, delete_source=True)
            remaining.append(decompressed)
        else:
            remaining.append(file_path)

    return remaining
