"""Object snapshot persistence and source-aware cache identity. No network access."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.types import GSEInfo
from PyNCBI.domains.geo.records import SampleFailure, SampleState, SeriesState

if TYPE_CHECKING:
    from PyNCBI.core.protocols import Cache

_VERSION = 1


def sample_payload(sample: SampleState, data: bool) -> dict[str, Any]:
    return {
        "gsm_id": sample.gsm_id,
        "info": sample.info,
        "characteristics": sample.characteristics,
        "platform_id": sample.platform_id,
        "gse_id": sample.gse_id,
        "supplementary_files": sample.supplementary_files,
        "data": sample.data if data else None,
        "source": sample.source if data else "metadata",
        "source_url": sample.source_url if data else "",
    }


def series_payload(series: SeriesState, data: bool) -> dict[str, Any]:
    return {
        "gse_id": series.gse_id,
        "info": series.info,
        "details": asdict(series.details),
        "samples_df": series.samples_df,
        "characteristics_df": series.characteristics_df,
        "samples": {g: sample_payload(s, data) for g, s in series.samples.items()},
        "failures": [asdict(f) for f in series.failures if data or f.stage == "metadata"],
        "matrix": series.matrix if data else None,
        "source": series.source if data else "metadata",
    }


def restore_sample(payload: dict[str, Any]) -> SampleState:
    if not isinstance(payload.get("info"), pd.Series) or not isinstance(
        payload.get("characteristics"), pd.Series
    ):
        raise ValueError("Invalid sample snapshot")
    if payload.get("data") is not None and not isinstance(
        payload["data"], (pd.Series, pd.DataFrame)
    ):
        raise ValueError("Invalid cached sample data")
    return SampleState(**payload)


def restore_series(payload: dict[str, Any]) -> SeriesState:
    if not isinstance(payload.get("info"), pd.Series) or not all(
        isinstance(payload.get(key), pd.DataFrame) for key in ("samples_df", "characteristics_df")
    ):
        raise ValueError("Invalid series snapshot")
    if payload.get("matrix") is not None and not isinstance(payload["matrix"], pd.DataFrame):
        raise ValueError("Invalid cached matrix")
    return SeriesState(
        gse_id=payload["gse_id"],
        info=payload["info"],
        details=GSEInfo(**payload["details"]),
        samples_df=payload["samples_df"],
        characteristics_df=payload["characteristics_df"],
        samples={g: restore_sample(s) for g, s in payload["samples"].items()},
        failures=[SampleFailure(**f) for f in payload["failures"]],
        matrix=payload["matrix"],
        source=payload["source"],
    )


class EntityCache:
    """Metadata is accession-keyed; full data is versioned and keyed by its request."""

    def __init__(
        self,
        backend: Cache[dict[str, Any]],
        accession: str,
        request: dict[str, Any] | None,
        shell_only: bool,
    ) -> None:
        self.backend, self.accession, self.request, self.shell_only = (
            backend,
            accession,
            request,
            shell_only,
        )

    @property
    def key(self) -> str | None:
        if self.shell_only:
            return self.accession
        if self.request is None:
            return None
        identity = json.dumps(self.request, sort_keys=True, separators=(",", ":"))
        return f"{self.accession}_object_{hashlib.sha256(identity.encode()).hexdigest()[:24]}"

    def get(self) -> SampleState | SeriesState | None:
        key = self.key
        payload = self.backend.get(key) if key is not None else None
        if not payload or payload.get("entity_cache_version") != _VERSION:
            return None
        if not self.shell_only and payload.get("request") != self.request:
            return None
        try:
            state = (
                restore_series(payload["state"])
                if self.accession.startswith("GSE")
                else restore_sample(payload["state"])
            )
            actual = state.gse_id if isinstance(state, SeriesState) else state.gsm_id
            return state if actual == self.accession else None
        except (KeyError, TypeError, ValueError):
            return None

    def save(self, state: SampleState | SeriesState) -> None:
        if isinstance(state, SeriesState):
            metadata_ok = not any(f.stage == "metadata" for f in state.failures)
            metadata = series_payload(state, False)
            payload = series_payload(state, True)
            complete = not state.failures
        else:
            metadata_ok = complete = True
            metadata = sample_payload(state, False)
            payload = sample_payload(state, True)
        key = self.key
        previous = self.backend.get(self.accession) or {}
        variants = {
            str(k)
            for k in previous.get("variants", [])
            if str(k).startswith(f"{self.accession}_object_")
        }
        if not self.shell_only and key is not None:
            if complete:
                self.backend.set(
                    key,
                    {"entity_cache_version": _VERSION, "request": self.request, "state": payload},
                )
                variants.add(key)
            else:
                self.backend.delete(key)
                variants.discard(key)
        if metadata_ok:
            self.backend.set(
                self.accession,
                {
                    "entity_cache_version": _VERSION,
                    "request": None,
                    "state": metadata,
                    "variants": sorted(variants),
                },
            )

    def exists(self) -> bool:
        return self.get() is not None

    def clear(self) -> bool:
        metadata = self.backend.get(self.accession) or {}
        keys = {self.accession}
        keys.update(
            str(k)
            for k in metadata.get("variants", [])
            if str(k).startswith(f"{self.accession}_object_")
        )
        if self.key is not None:
            keys.add(self.key)
        list_keys = getattr(self.backend, "list_keys", None)
        if callable(list_keys):
            keys.update(list_keys(f"{self.accession}_object_*"))
        removed = False
        for key in keys:
            removed = self.backend.delete(key) or removed
        return removed
