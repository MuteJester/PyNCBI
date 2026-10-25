"""URL normalisation, size formatting, and file-type inference for NCBI endpoints."""

from __future__ import annotations

import re
from pathlib import Path

_CACHEABLE_PATH_RE = re.compile(
    r"/geo/query/acc\.cgi|/entrez/eutils/\w+\.fcgi|/datasets/v\d|/ena/portal/api", re.I
)


_UNCACHEABLE_PATH_RE = re.compile(r"blast\.cgi|/geo/download/|/suppl/|/matrix/|/soft/", re.I)


_API_KEY_PARAM_RE = re.compile(r"[?&]api_key=[^&#]*")


def normalize_ncbi_url(url: str) -> str:
    """Normalize NCBI URLs so `ftp://ftp.ncbi.nlm.nih.gov/...` uses `https://`."""
    url = url.strip()
    if url.startswith("ftp://ftp.ncbi.nlm.nih.gov/"):
        return "https://" + url[len("ftp://") :]
    if url.startswith("http://ftp.ncbi.nlm.nih.gov/"):
        return "https://" + url[len("http://") :]
    if url.startswith("ftp://"):
        return "https://" + url[len("ftp://") :]
    return url


def format_size_human(size_bytes: int | None) -> str:
    """Format byte size into human-readable string (e.g., '14.2 MB')."""
    if size_bytes is None or size_bytes < 0:
        return "unknown"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def supplementary_urls(raw: object) -> list[str]:
    """Normalise a raw GEO `supplementary_file` value into a list of usable HTTPS URLs.

    GEO writes this field as one string with newline-separated URLs, as a list of
    strings, or as the literal `NONE` when a record has no files. Every caller needs
    the same three steps, so they live here: split, drop placeholders, rewrite
    `ftp://` to `https://`.

    Args:
        raw: The field value as parsed from SOFT (string, list, or missing)

    Returns:
        URLs in their original order, with placeholders and blanks removed.
    """
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        for part in value.split("\n"):
            part = part.strip()
            if not part or part.upper() == "NONE":
                continue
            out.append(normalize_ncbi_url(part))
    return out


def infer_file_type(filename: str) -> str:
    """Infer a concise file type label from a filename."""
    lower = filename.lower()
    for compound in (
        ".series_matrix.txt.gz",
        "_series_matrix.txt.gz",
        ".tar.gz",
        ".csv.gz",
        ".tsv.gz",
        ".txt.gz",
        ".idat.gz",
        ".mtx.gz",
        ".bed.gz",
        ".bw",
        ".bigwig",
        ".h5ad",
        ".h5",
        ".parquet",
        ".tar",
        ".csv",
        ".tsv",
        ".txt",
        ".idat",
        ".xlsx",
        ".pdf",
    ):
        if lower.endswith(compound):
            return compound.lstrip("._")
    ext = Path(lower).suffix.lstrip(".")
    return ext or "file"


def download_filename(url: str) -> str:
    """Resolve a URL's file parameter/path to one portable local filename."""
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(url)
    name = (
        parse_qs(parsed.query).get("file", [unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])])[0]
        or "download"
    )
    if (
        name in (".", "..")
        or any(c in name for c in '/\\<>:"|?*')
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError(f"Unsafe download filename: {name!r}")
    return name
