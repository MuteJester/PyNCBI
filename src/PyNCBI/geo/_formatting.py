"""Pure presentation for GEO entities; no fetching, caching or state mutation."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from PyNCBI.core.formatter import summarize_characteristics
from PyNCBI.io.urls import supplementary_urls

if TYPE_CHECKING:
    from PyNCBI.domains.geo.records import SampleState, SeriesState


def series_summary(state: SeriesState) -> dict[str, Any]:
    platform = state.info.get("platform_id", "")
    if isinstance(platform, list):
        platform = platform[0] if platform else ""
    ids = list(state.samples) or state.details.gsm_ids
    return {
        "gse_id": state.gse_id,
        "title": str(state.info.get("title", "")),
        "platform_id": str(platform),
        "sample_count": len(ids),
        "gsm_ids": list(ids),
        "supplementary_files": supplementary_urls(state.info.get("supplementary_file")),
        "characteristics_summary": [
            s.to_dict() for s in summarize_characteristics(state.characteristics_table)
        ],
        "info": dict(state.info),
        "failed_samples": state.failed_samples,
        "failures": [asdict(f) for f in state.failures],
        "source": state.source,
    }


def sample_summary(state: SampleState) -> dict[str, Any]:
    return {
        "gsm_id": state.gsm_id,
        "title": str(state.info.get("title", "")),
        "gse": state.gse_id,
        "platform_id": state.platform_id,
        "characteristics": dict(state.characteristics),
        "supplementary_files": list(state.supplementary_files),
        "has_data": state.data is not None or state._matrix is not None,
        "data_shape": [len(state._matrix["rows"]), 2]
        if state._matrix is not None
        else [len(state.data["rows"]), len(state.data["columns"])]
        if state.data is not None
        else None,
        "info": dict(state.info),
    }
