"""Compression utilities for PyNCBI: gunzip and safe tar extraction."""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

from PyNCBI.core.exceptions import ArchiveExtractionError, PathTraversalError


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

    with gzip.open(source, "rb") as src, destination.open("wb") as dst:
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

        # Python 3.14 filters members by default; be explicit so behaviour is identical
        # on every supported version. `data` also refuses device files and absolute paths,
        # which the member scan above already rejects.
        if hasattr(tarfile, "data_filter"):
            tar.extractall(destination, filter="data")
        else:  # pragma: no cover - only on interpreters older than 3.10.12
            tar.extractall(destination)

    return destination


def extract_zipfile(
    archive: str | Path,
    destination: str | Path,
    safe: bool = True,
) -> Path:
    """Extract a zip archive safely (refuses path traversal and absolute member names).

    Raises:
        PathTraversalError: If a member would land outside `destination`
    """
    import zipfile

    archive = Path(archive)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as zf:
        if safe:
            for member in zf.infolist():
                name = member.filename
                target = (destination / name).resolve()
                if (
                    name.startswith(("/", "\\"))
                    or destination not in target.parents
                    and target != destination
                ):
                    raise PathTraversalError(str(archive), name)
        zf.extractall(destination)
    return destination
