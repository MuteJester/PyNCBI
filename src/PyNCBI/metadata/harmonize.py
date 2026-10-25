"""Harmonize ragged GEO sample characteristics into canonical, provenance-tracked columns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from PyNCBI.metadata import vocabulary
from PyNCBI.metadata.schema import NormalizedValue

DERIVED_COLUMNS: dict[str, tuple[str, ...]] = {
    "age": ("age_unit", "age_years"),
    "time_point": ("time_point_unit",),
    "disease": ("is_control",),
}


@dataclass
class HarmonizedTable:
    """Result of :func:`harmonize_characteristics`."""

    normalized_df: pd.DataFrame
    raw_df: pd.DataFrame
    provenance_df: pd.DataFrame
    field_map: dict[str, str] = field(default_factory=dict)  # raw key -> canonical field
    unmapped_keys: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    coverage: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "sample_count": int(len(self.normalized_df)),
            "mapped_fields": sorted(set(self.field_map.values())),
            "field_map": dict(self.field_map),
            "unmapped_keys": list(self.unmapped_keys),
            "conflicts": len(self.conflicts),
            "coverage": round(float(self.coverage), 4),
            "vocabulary_version": vocabulary_version(),
        }


def vocabulary_version() -> str:
    """Stable identifier of the default vocabulary (field names + synonym count)."""
    fields = vocabulary.fields()
    return f"{len(fields)}f-{sum(len(f.synonyms) for f in fields.values())}s"


def harmonize_sample(
    characteristics: dict[str, Any], *, source: str = "geo:characteristics_ch1"
) -> dict[str, NormalizedValue]:
    """Harmonize one sample's ``{key: value}`` characteristics into canonical NormalizedValues.

    Unmapped keys are returned under ``raw:<key>`` with confidence ``unmapped`` so nothing is lost.
    """
    out: dict[str, NormalizedValue] = {}
    for key, raw in characteristics.items():
        canon = vocabulary.canonical_field(str(key))
        if canon is None:
            text = "" if raw is None else str(raw)
            out[f"raw:{key}"] = NormalizedValue(
                value=text.strip() or None,
                raw=text,
                confidence="unmapped",
                rule="unmapped_key",
                source_key=str(key),
                source=source,
            )
            continue
        nv = vocabulary.normalize_value(canon, raw, source_key=str(key))
        nv.source = source
        if (
            canon in out
            and out[canon].value is not None
            and nv.value is not None
            and out[canon].value != nv.value
        ):
            out[canon].extra.setdefault("conflicts", []).append(
                {"source_key": str(key), "raw": nv.raw, "value": nv.value}
            )
            continue
        if canon in out and out[canon].value is not None:
            continue
        out[canon] = nv
    return out


def harmonize_characteristics(
    char_df: pd.DataFrame, *, source: str = "geo:characteristics_ch1"
) -> HarmonizedTable:
    """Harmonize a samples x characteristics DataFrame (as returned by ``extract_gse_samples``).

    Returns canonical columns (e.g. ``sex``, ``age``, ``disease``) plus derived columns
    (``age_unit``, ``age_years``, ``is_control``) and ``raw:<key>`` columns for keys the
    conservative vocabulary does not map, with a per-cell provenance table.
    """
    if char_df is None or char_df.empty:
        empty = pd.DataFrame()
        return HarmonizedTable(
            normalized_df=empty,
            raw_df=empty,
            provenance_df=pd.DataFrame(
                columns=[
                    "gsm_id",
                    "field",
                    "source_key",
                    "raw",
                    "value",
                    "unit",
                    "rule",
                    "confidence",
                ]
            ),
        )

    field_map: dict[str, str] = {}
    unmapped: list[str] = []
    for key in char_df.columns:
        canon = vocabulary.canonical_field(str(key))
        if canon is None:
            unmapped.append(str(key))
        else:
            field_map[str(key)] = canon

    rows: list[dict[str, Any]] = []
    prov_rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    mapped_cells = 0
    total_cells = 0
    for gsm_id, row in char_df.iterrows():
        out_row: dict[str, Any] = {}
        seen: dict[str, str] = {}
        for key in char_df.columns:
            raw = row[key]
            if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                continue
            total_cells += 1
            canon = field_map.get(str(key))
            if canon is None:
                out_row[f"raw:{key}"] = str(raw)
                continue
            nv = vocabulary.normalize_value(canon, raw, source_key=str(key))
            if canon in seen:
                if (
                    nv.value is not None
                    and out_row.get(canon) is not None
                    and out_row.get(canon) != nv.value
                ):
                    conflicts.append(
                        {
                            "gsm_id": str(gsm_id),
                            "field": canon,
                            "kept_source_key": seen[canon],
                            "kept": out_row.get(canon),
                            "other_source_key": str(key),
                            "other": nv.value,
                        }
                    )
                continue
            seen[canon] = str(key)
            mapped_cells += 1
            out_row[canon] = nv.value
            if canon in DERIVED_COLUMNS:
                if "age_unit" in DERIVED_COLUMNS.get(canon, ()):
                    out_row["age_unit"] = nv.unit
                    out_row["age_years"] = nv.extra.get("age_years")
                if "time_point_unit" in DERIVED_COLUMNS.get(canon, ()):
                    out_row["time_point_unit"] = nv.unit
                if "is_control" in DERIVED_COLUMNS.get(canon, ()):
                    out_row["is_control"] = nv.extra.get("is_control")
            prov_rows.append(
                {
                    "gsm_id": str(gsm_id),
                    "field": canon,
                    "source": source,
                    "source_key": str(key),
                    "raw": nv.raw,
                    "value": nv.value,
                    "unit": nv.unit,
                    "rule": nv.rule,
                    "confidence": nv.confidence,
                }
            )
        rows.append(out_row)

    canonical_order = [f for f in vocabulary.fields() if f in set(field_map.values())]
    ordered_cols: list[str] = []
    for canon in canonical_order:
        ordered_cols.append(canon)
        ordered_cols.extend(DERIVED_COLUMNS.get(canon, ()))
    ordered_cols.extend(f"raw:{k}" for k in unmapped)
    index = pd.Index(char_df.index, name=char_df.index.name or "gsm_id")
    normalized = pd.DataFrame(rows, index=index)
    for col in ordered_cols:
        if col not in normalized.columns:
            normalized[col] = pd.NA
    normalized = normalized[[c for c in ordered_cols if c in normalized.columns]]
    provenance = pd.DataFrame(
        prov_rows,
        columns=[
            "gsm_id",
            "field",
            "source",
            "source_key",
            "raw",
            "value",
            "unit",
            "rule",
            "confidence",
        ],
    )
    coverage = (mapped_cells / total_cells) if total_cells else 0.0
    return HarmonizedTable(
        normalized_df=normalized,
        raw_df=char_df.copy(),
        provenance_df=provenance,
        field_map=field_map,
        unmapped_keys=unmapped,
        conflicts=conflicts,
        coverage=coverage,
    )
