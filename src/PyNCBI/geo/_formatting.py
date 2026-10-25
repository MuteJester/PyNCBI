"""Pure presentation for GEO entities; no fetching, caching or state mutation."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from PyNCBI.core.formatter import summarize_characteristics_df
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
            s.to_dict() for s in summarize_characteristics_df(state.characteristics_df)
        ],
        "info": state.info.to_dict(),
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
        "characteristics": state.characteristics.to_dict(),
        "supplementary_files": list(state.supplementary_files),
        "has_data": state.data is not None,
        "data_shape": list(state.data.shape) if state.data is not None else None,
        "info": state.info.to_dict(),
    }
