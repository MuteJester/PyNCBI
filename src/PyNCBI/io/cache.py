"""Cache management for PyNCBI.

This module provides a caching system with inspection and management APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Generic, TypeVar

from PyNCBI.config import get_config
from PyNCBI.exceptions import CacheCorruptedError, CacheNotFoundError
from PyNCBI.io.compression import compress_data, decompress_data

T = TypeVar("T")


@dataclass
class CacheEntry:
    """Metadata about a cache entry.

    Attributes:
        key: Cache key (without extension)
        path: Full path to the cache file
        size_bytes: Size of the cache file in bytes
        created: Creation timestamp
        modified: Last modification timestamp
    """

    key: str
    path: Path
    size_bytes: int
    created: datetime
    modified: datetime

    @property
    def age_seconds(self) -> float:
        """Get the age of the cache entry in seconds."""
        return (datetime.now() - self.modified).total_seconds()

    @property
    def size_human(self) -> str:
        """Get human-readable size string."""
        size = self.size_bytes
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class CompressedPickleCache(Generic[T]):
    """Cache implementation using compressed pickle files.

    This cache stores Python objects as compressed pickle files with
    a `.ch` extension. It provides inspection and management APIs.

    Example:
        cache = CompressedPickleCache()

        # Store and retrieve
        cache.set("my_key", {"data": [1, 2, 3]})
        data = cache.get("my_key")

        # Inspect cache
        for key in cache.list_keys("GSM*"):
            info = cache.info(key)
            print(f"{key}: {info.size_human}, age={info.age_seconds}s")

        # Clean up
        cache.delete("my_key")
        cache.clear()  # Remove all
    """

    EXTENSION = ".ch"

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        """Initialize the cache.

        Args:
            cache_dir: Directory for cache files (uses config default if not provided)
        """
        if cache_dir is None:
            cache_dir = get_config().cache_folder
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_to_path(self, key: str) -> Path:
        """Convert cache key to file path."""
        return self.cache_dir / f"{key}{self.EXTENSION}"

    def _path_to_key(self, path: Path) -> str:
        """Convert file path to cache key."""
        return path.stem

    def get(self, key: str) -> T | None:
        """Get cached item or None if not found.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/corrupted
        """
        path = self._key_to_path(key)
        if not path.exists():
            return None
        try:
            compressed = path.read_bytes()
            return decompress_data(compressed)
        except Exception:
            return None

    def get_or_raise(self, key: str) -> T:
        """Get cached item or raise CacheNotFoundError.

        Args:
            key: Cache key

        Returns:
            Cached value

        Raises:
            CacheNotFoundError: If key not found
            CacheCorruptedError: If cache file is corrupted
        """
        path = self._key_to_path(key)
        if not path.exists():
            raise CacheNotFoundError(key)
        try:
            compressed = path.read_bytes()
            return decompress_data(compressed)
        except Exception as e:
            raise CacheCorruptedError(str(path)) from e

    def set(self, key: str, value: T) -> None:
        """Store item in cache.

        Args:
            key: Cache key
            value: Value to cache (must be picklable)
        """
        path = self._key_to_path(key)
        compressed = compress_data(value)
        path.write_bytes(compressed)

    def exists(self, key: str) -> bool:
        """Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists
        """
        return self._key_to_path(key).exists()

    def delete(self, key: str) -> bool:
        """Delete cached item.

        Args:
            key: Cache key

        Returns:
            True if item existed and was deleted
        """
        path = self._key_to_path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    def clear(self) -> int:
        """Clear all cached items.

        Returns:
            Number of items deleted
        """
        count = 0
        for path in self.cache_dir.glob(f"*{self.EXTENSION}"):
            path.unlink()
            count += 1
        return count

    def list_keys(self, pattern: str = "*") -> list[str]:
        """List all cache keys matching pattern.

        Args:
            pattern: Glob pattern to match (default: all keys)

        Returns:
            Sorted list of matching cache keys
        """
        keys = []
        for path in self.cache_dir.glob(f"*{self.EXTENSION}"):
            key = self._path_to_key(path)
            if fnmatch(key, pattern):
                keys.append(key)
        return sorted(keys)

    def info(self, key: str) -> CacheEntry | None:
        """Get metadata about a cache entry.

        Args:
            key: Cache key

        Returns:
            CacheEntry with metadata, or None if not found
        """
        path = self._key_to_path(key)
        if not path.exists():
            return None
        stat = path.stat()
        return CacheEntry(
            key=key,
            path=path,
            size_bytes=stat.st_size,
            created=datetime.fromtimestamp(stat.st_ctime),
            modified=datetime.fromtimestamp(stat.st_mtime),
        )

    def total_size(self) -> int:
        """Get total size of all cached items in bytes.

        Returns:
            Total size in bytes
        """
        total = 0
        for path in self.cache_dir.glob(f"*{self.EXTENSION}"):
            total += path.stat().st_size
        return total

    def __len__(self) -> int:
        """Get number of cached items."""
        return len(list(self.cache_dir.glob(f"*{self.EXTENSION}")))

    def __contains__(self, key: str) -> bool:
        """Check if key is in cache."""
        return self.exists(key)

    def __repr__(self) -> str:
        """String representation."""
        return f"CompressedPickleCache({self.cache_dir!r}, items={len(self)})"
