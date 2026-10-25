"""Local cache for PyNCBI (pickle-free).

Entries are stored as gzip-compressed JSON (`<key>.json.gz`). pandas objects are encoded
with a versioned, lossless JSON representation so cached data can never execute code on load,
unlike the pickle-based `.ch` files used before 1.0 (those are ignored and removed by
`clear()`).
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path, PurePath
from tempfile import NamedTemporaryFile
from typing import Any, Generic, TypeVar, cast

import numpy as np
import pandas as pd

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import CacheCorruptedError, CacheNotFoundError, CacheWriteError

T = TypeVar("T")

_TAG = "__pyncbi__"
LEGACY_EXTENSION = ".ch"


def _axis_value(axis: pd.Index) -> dict[str, Any]:
    return {
        "values": encode_cache_value(axis.tolist()),
        "names": encode_cache_value(list(axis.names)),
        "multi": isinstance(axis, pd.MultiIndex),
        "dtype": str(axis.dtype),
    }


def _restore_axis(data: dict[str, Any]) -> pd.Index:
    values = decode_cache_value(data["values"])
    names = decode_cache_value(data["names"])
    if data["multi"]:
        return pd.MultiIndex.from_tuples(values, names=names)
    return cast(
        "pd.Index", pd.Index(values, dtype=data["dtype"], name=names[0], tupleize_cols=False)
    )


def _dtype_value(series: pd.Series) -> dict[str, Any]:
    if isinstance(series.dtype, pd.CategoricalDtype):
        return {
            "name": "category",
            "categories": encode_cache_value(series.cat.categories.tolist()),
            "ordered": series.cat.ordered,
        }
    return {"name": str(series.dtype)}


def _restore_series(values: Any, dtype: dict[str, Any], index: pd.Index) -> pd.Series:
    kind = dtype["name"]
    if kind == "category":
        kind = pd.CategoricalDtype(
            decode_cache_value(dtype["categories"]), ordered=dtype["ordered"]
        )
    return cast("pd.Series", pd.Series(decode_cache_value(values), index=index, dtype=kind))


def encode_cache_value(value: Any) -> Any:
    """Encode cache values without pandas' decimal rounding or dtype inference."""
    if isinstance(value, pd.DataFrame):
        return {
            _TAG: "DataFrameV2",
            "index": _axis_value(value.index),
            "columns": _axis_value(value.columns),
            "values": [
                encode_cache_value(value.iloc[:, i].tolist()) for i in range(len(value.columns))
            ],
            "dtypes": [_dtype_value(value.iloc[:, i]) for i in range(len(value.columns))],
        }
    if isinstance(value, pd.Series):
        return {
            _TAG: "SeriesV2",
            "index": _axis_value(value.index),
            "values": encode_cache_value(value.tolist()),
            "dtype": _dtype_value(value),
            "name": encode_cache_value(value.name),
        }
    if value is pd.NA:
        return {_TAG: "NA"}
    if value is pd.NaT:
        return {_TAG: "NaT"}
    if isinstance(value, np.generic):
        return encode_cache_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return {_TAG: "float", "value": str(value)}
    if isinstance(value, pd.Timestamp):
        return {_TAG: "timestamp", "value": value.isoformat()}
    if isinstance(value, datetime):
        return {_TAG: "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {_TAG: "date", "value": value.isoformat()}
    if isinstance(value, timedelta):
        return {_TAG: "timedelta", "value": value.total_seconds()}
    if isinstance(value, tuple):
        return {_TAG: "tuple", "value": [encode_cache_value(v) for v in value]}
    if isinstance(value, dict):
        return {str(k): encode_cache_value(v) for k, v in value.items()}
    if isinstance(value, (list, set, frozenset, np.ndarray)):
        return [encode_cache_value(v) for v in value]
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return encode_cache_value(value.to_dict())
    return str(value)


def decode_cache_value(value: Any) -> Any:
    """Decode typed JSON. Old rounded pandas entries become cache misses."""
    if isinstance(value, dict):
        tag = value.get(_TAG)
        if tag in ("DataFrame", "Series"):
            raise ValueError("Legacy rounded table cache; fetch the original measurements again")
        if tag == "DataFrameV2":
            index = _restore_axis(value["index"])
            columns = _restore_axis(value["columns"])
            parts = [
                _restore_series(v, d, index)
                for v, d in zip(value["values"], value["dtypes"], strict=True)
            ]
            frame = pd.concat(parts, axis=1) if parts else pd.DataFrame(index=index)
            frame.columns = columns
            return frame
        if tag == "SeriesV2":
            series = _restore_series(value["values"], value["dtype"], _restore_axis(value["index"]))
            series.name = decode_cache_value(value["name"])
            return series
        if tag == "NA":
            return pd.NA
        if tag == "NaT":
            return pd.NaT
        if tag == "float":
            return float(value["value"])
        if tag == "timestamp":
            return pd.Timestamp(value["value"])
        if tag == "datetime":
            return datetime.fromisoformat(value["value"])
        if tag == "date":
            return date.fromisoformat(value["value"])
        if tag == "timedelta":
            return timedelta(seconds=value["value"])
        if tag == "tuple":
            return tuple(decode_cache_value(v) for v in value["value"])
        return {k: decode_cache_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_cache_value(v) for v in value]
    return value


@dataclass
class CacheEntry:
    """Metadata about a cache entry."""

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
        size = float(self.size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def to_dict(self) -> dict[str, Any]:
        """Serialize cache entry metadata to dictionary."""
        return {
            "key": self.key,
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "size_human": self.size_human,
            "created": self.created.isoformat(),
            "modified": self.modified.isoformat(),
            "age_seconds": round(self.age_seconds, 1),
        }


class LocalCache(Generic[T]):
    """Gzip-compressed JSON cache keyed by accession (`<key>.json.gz`)."""

    EXTENSION = ".json.gz"

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        if cache_dir is None:
            cache_dir = get_config().cache_folder
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_to_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}{self.EXTENSION}"

    def _path_to_key(self, path: Path) -> str:
        name = path.name
        return name[: -len(self.EXTENSION)] if name.endswith(self.EXTENSION) else path.stem

    def _iter_paths(self) -> list[Path]:
        return sorted(self.cache_dir.glob(f"*{self.EXTENSION}"))

    def _read(self, path: Path) -> T:
        raw = gzip.decompress(path.read_bytes()).decode("utf-8")
        return decode_cache_value(json.loads(raw))  # type: ignore[no-any-return]

    def get(self, key: str) -> T | None:
        """Get cached item or None if not found or unreadable."""
        path = self._key_to_path(key)
        if not path.exists():
            return None
        try:
            return self._read(path)
        except Exception:  # noqa: BLE001 - a corrupt entry behaves like a miss
            return None

    def get_or_raise(self, key: str) -> T:
        """Get cached item or raise CacheNotFoundError / CacheCorruptedError."""
        path = self._key_to_path(key)
        if not path.exists():
            raise CacheNotFoundError(key)
        try:
            return self._read(path)
        except Exception as e:
            raise CacheCorruptedError(str(path)) from e

    def set(self, key: str, value: T) -> None:
        """Store item in cache atomically."""
        path = self._key_to_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp: Path | None = None
        try:
            payload = json.dumps(encode_cache_value(value), allow_nan=False).encode("utf-8")
            with NamedTemporaryFile(
                dir=path.parent, prefix=path.name + ".", suffix=".part", delete=False
            ) as staging:
                tmp = Path(staging.name)
                staging.write(gzip.compress(payload))
            tmp.replace(path)
        except Exception as e:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            raise CacheWriteError(str(path), e) from e

    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        return self._key_to_path(key).exists()

    def delete(self, key: str) -> bool:
        """Delete cached item (including any legacy pickle entry of the same key)."""
        removed = False
        for path in (self._key_to_path(key), self.cache_dir / f"{key}{LEGACY_EXTENSION}"):
            if path.exists():
                path.unlink()
                removed = True
        return removed

    def clear(self, pattern: str = "*") -> int:
        """Clear cached items matching pattern (legacy `.ch` pickles are always removed).

        Returns:
            Number of files deleted
        """
        count = 0
        for path in self._iter_paths():
            if fnmatch(self._path_to_key(path), pattern):
                path.unlink()
                count += 1
        for path in self.cache_dir.glob(f"*{LEGACY_EXTENSION}"):
            if fnmatch(path.stem, pattern):
                path.unlink()
                count += 1
        return count

    def list_keys(self, pattern: str = "*") -> list[str]:
        """List all cache keys matching pattern."""
        return sorted(
            self._path_to_key(p)
            for p in self._iter_paths()
            if fnmatch(self._path_to_key(p), pattern)
        )

    def info(self, key: str) -> CacheEntry | None:
        """Get metadata about a cache entry."""
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
        """Get total size of all cached items in bytes."""
        return sum(p.stat().st_size for p in self._iter_paths())

    def status_summary(self) -> dict[str, Any]:
        """Return a structured summary of the cache folder."""
        from PyNCBI.io.http_client import format_size_human

        entries = [e for e in (self.info(k) for k in self.list_keys()) if e is not None]
        valid_entries = [e.to_dict() for e in entries]
        total_bytes = sum(int(e["size_bytes"]) for e in valid_entries)
        legacy = len(list(self.cache_dir.glob(f"*{LEGACY_EXTENSION}")))
        return {
            "cache_dir": str(self.cache_dir),
            "item_count": len(valid_entries),
            "total_size_bytes": total_bytes,
            "total_size_human": format_size_human(total_bytes),
            "legacy_pickle_files": legacy,
            "items": valid_entries,
        }

    def __len__(self) -> int:
        return len(self._iter_paths())

    def __contains__(self, key: str) -> bool:
        return self.exists(key)

    def __repr__(self) -> str:
        return f"LocalCache({self.cache_dir!r}, items={len(self)})"


# Backward-compatible name (the implementation is no longer pickle based)
CompressedPickleCache = LocalCache
