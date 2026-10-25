"""Filename interpretation and matching shared by file selectors and retrieval."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePath
from urllib.parse import parse_qs, unquote, urlsplit


def file_name(value: str | PurePath) -> str:
    """Read a filename from a local path, URL path, or GEO download's file parameter.

    This interprets names for selection and format detection. Callers writing a
    remote name to disk must also apply ``io.urls.download_filename`` validation.
    """
    if isinstance(value, PurePath):
        return value.name
    parsed = urlsplit(value)
    files = parse_qs(parsed.query).get("file")
    if files:
        return files[0]
    path = parsed.path
    if parsed.scheme not in ("http", "https", "ftp"):
        path = path.replace("\\", "/")
    return unquote(path.rstrip("/").rsplit("/", 1)[-1])


def matches_file(value: str | PurePath, pattern: str | None) -> bool:
    """Apply the package's case-insensitive glob-or-substring filename policy."""
    if not pattern:
        return True
    name, pattern = file_name(value).lower(), pattern.lower()
    return fnmatchcase(name, pattern) or pattern in name
