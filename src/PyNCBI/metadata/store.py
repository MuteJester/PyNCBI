"""Durable storage for study records: TTL-aware store and a checksummed on-disk layout."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyNCBI.core.exceptions import DataError
from PyNCBI.core.serialization import json_dumps
from PyNCBI.io.cache import LocalCache
from PyNCBI.metadata import vocabulary
from PyNCBI.metadata.schema import SCHEMA_VERSION, StudyRecord, now_iso

DEFAULT_TTL_SECONDS = 7 * 24 * 3600
_KEY_PREFIX = "meta_"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MetadataStore:
    """TTL-aware, content-hashed store of StudyRecords built on the pickle-free LocalCache."""

    def __init__(
        self, cache_dir: str | Path | None = None, *, ttl_seconds: float = DEFAULT_TTL_SECONDS
    ) -> None:
        base = Path(cache_dir) if cache_dir is not None else None
        self._cache: LocalCache[dict[str, Any]] = (
            LocalCache(base) if base is not None else LocalCache()
        )
        if base is None:
            self._cache = LocalCache(self._cache.cache_dir / "metadata")
        self.ttl_seconds = float(ttl_seconds)

    @property
    def cache_dir(self) -> Path:
        return self._cache.cache_dir

    def _entry(self, gse_id: str) -> dict[str, Any] | None:
        return self._cache.get(f"{_KEY_PREFIX}{gse_id.upper()}")

    def get(self, gse_id: str, *, max_age: float | None = None) -> StudyRecord | None:
        """Return the stored record if present and younger than ``max_age`` (default: TTL)."""
        entry = self._entry(gse_id)
        if not entry:
            return None
        limit = self.ttl_seconds if max_age is None else float(max_age)
        stored_at = (
            datetime.fromisoformat(str(entry.get("stored_at", "")).replace("Z", "+00:00"))
            if entry.get("stored_at")
            else None
        )
        if stored_at is None:
            return None
        age = (datetime.now(timezone.utc) - stored_at).total_seconds()
        if age > limit:
            return None
        return StudyRecord.from_dict(entry["record"])

    def put(self, record: StudyRecord) -> dict[str, Any]:
        entry = {
            "stored_at": now_iso(),
            "schema_version": SCHEMA_VERSION,
            "content_hash": record.content_hash(),
            "gse_id": record.gse_id,
            "title": record.title,
            "sample_count": record.sample_count,
            "record": record.to_dict(),
        }
        self._cache.set(f"{_KEY_PREFIX}{record.gse_id.upper()}", entry)
        return {k: v for k, v in entry.items() if k != "record"}

    def get_or_build(
        self, gse_id: str, *, max_age: float | None = None, **build_kwargs: Any
    ) -> StudyRecord:
        """Return a fresh stored record or build (and store) a new one."""
        cached = self.get(gse_id, max_age=max_age)
        if cached is not None:
            return cached
        from PyNCBI.metadata.builder import build_study_record

        record = build_study_record(gse_id, **build_kwargs)
        self.put(record)
        return record

    def delete(self, gse_id: str) -> bool:
        return self._cache.delete(f"{_KEY_PREFIX}{gse_id.upper()}")

    def index(self) -> list[dict[str, Any]]:
        """Summaries of every stored record (no sample payloads)."""
        out: list[dict[str, Any]] = []
        for key in self._cache.list_keys(f"{_KEY_PREFIX}*"):
            entry = self._cache.get(key)
            if not entry:
                continue
            out.append(
                {
                    k: entry.get(k)
                    for k in (
                        "gse_id",
                        "title",
                        "sample_count",
                        "stored_at",
                        "schema_version",
                        "content_hash",
                    )
                }
            )
        return sorted(out, key=lambda e: str(e.get("stored_at", "")), reverse=True)


# ---------------------------------------------------------------------------
# On-disk layout
# ---------------------------------------------------------------------------

LAYOUT_FILES: tuple[str, ...] = (
    "study.json",
    "samples.csv",
    "samples_harmonized.csv",
    "provenance.csv",
    "quality.json",
    "vocabulary.json",
)


def save_study_record(record: StudyRecord, output_dir: str | Path) -> dict[str, Any]:
    """Write the documented study layout and a checksummed manifest; returns the manifest."""
    from PyNCBI import __version__

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "study.json").write_text(json_dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
    record.samples_df().to_csv(out / "samples.csv")
    record.harmonized_df().to_csv(out / "samples_harmonized.csv")
    record.provenance_df().to_csv(out / "provenance.csv", index=False)
    (out / "quality.json").write_text(
        json_dumps(record.quality.to_dict() if record.quality else {}, indent=2) + "\n",
        encoding="utf-8",
    )
    (out / "vocabulary.json").write_text(
        json_dumps(
            {
                "version": record.source_versions.get("vocabulary", ""),
                "fields": {k: v.to_dict() for k, v in vocabulary.fields().items()},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    files = [
        {"path": name, "sha256": _sha256_file(out / name), "bytes": (out / name).stat().st_size}
        for name in LAYOUT_FILES
    ]
    manifest = {
        "layout": "pyncbi-study",
        "layout_version": "1",
        "schema_version": SCHEMA_VERSION,
        "pyncbi_version": __version__,
        "gse_id": record.gse_id,
        "created_at": now_iso(),
        "content_hash": record.content_hash(),
        "sample_count": record.sample_count,
        "files": files,
    }
    (out / "manifest.json").write_text(json_dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_study_record(directory: str | Path, *, verify: bool = True) -> StudyRecord:
    """Load a saved study layout, verifying every file against ``manifest.json`` by default."""
    folder = Path(directory)
    manifest_path = folder / "manifest.json"
    if verify:
        if not manifest_path.exists():
            raise DataError(
                f"No manifest.json in {folder}",
                hint="Was this directory written by StudyRecord.save()/save_study_record()?",
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("files", []):
            path = folder / str(entry["path"])
            if not path.exists():
                raise DataError(
                    f"Checksum verification failed: {entry['path']} is missing from {folder}"
                )
            if _sha256_file(path) != entry.get("sha256"):
                raise DataError(
                    f"Checksum verification failed for {entry['path']} in {folder}",
                    hint="The file was modified after the bundle was written; pass verify=False to load anyway",
                )
    data = json.loads((folder / "study.json").read_text(encoding="utf-8"))
    return StudyRecord.from_dict(data)
