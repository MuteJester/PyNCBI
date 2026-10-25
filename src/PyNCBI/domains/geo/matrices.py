"""One definition of sample-column alignment and per-sample measurement selection."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

_GSM_RE = re.compile(r"GSM\d+", re.I)

VALUE_COLUMN_HINTS: tuple[str, ...] = (
    "value",
    "count",
    "counts",
    "tpm",
    "fpkm",
    "rpkm",
    "cpm",
    "expression",
    "beta",
    "signal",
    "intensity",
    "abundance",
)


def _normalize_label(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).strip().lower())


def map_columns_to_gsms(
    columns: list[str],
    samples_df: pd.DataFrame,
    *,
    identifying_fields: tuple[str, ...] = (
        "title",
        "description",
        "source_name_ch1",
        "source_name",
    ),
) -> tuple[dict[str, str], list[str]]:
    """Map matrix column labels onto GSM IDs.

    Strategies, in order: the column *is* a GSM accession, it *contains* one, it matches a
    sample title exactly, or it matches another identifying sample field. Matches must be
    unique; ambiguous labels are left unmapped (and reported) rather than guessed.

    Returns:
        ``(mapping, notes)`` where `mapping` maps original column -> GSM ID.
    """
    if samples_df is None or samples_df.empty:
        return {}, ["Sample sheet unavailable for this series; matrix columns kept as published"]

    gsm_index = {str(g).upper(): str(g) for g in samples_df.index}
    mapping: dict[str, str] = {}
    by_strategy: dict[str, list[str]] = {}
    unmapped: list[str] = []

    lookups: list[tuple[str, dict[str, str]]] = []
    for col_name in identifying_fields:
        if col_name not in samples_df.columns:
            continue
        counts: dict[str, list[str]] = {}
        for gsm, value in samples_df[col_name].items():
            if value is None or (isinstance(value, float) and pd.isna(value)):
                continue
            key = _normalize_label(value)
            if key:
                counts.setdefault(key, []).append(str(gsm))
        lookups.append((col_name, {k: v[0] for k, v in counts.items() if len(v) == 1}))

    for column in columns:
        label = str(column)
        upper = label.strip().upper()
        if upper in gsm_index:
            mapping[label] = gsm_index[upper]
            by_strategy.setdefault("accession", []).append(label)
            continue
        found = _GSM_RE.search(label)
        if found and found.group(0).upper() in gsm_index:
            mapping[label] = gsm_index[found.group(0).upper()]
            by_strategy.setdefault("embedded accession", []).append(label)
            continue
        key = _normalize_label(label)
        hit = False
        for col_name, table in lookups:
            if key and key in table:
                mapping[label] = table[key]
                by_strategy.setdefault(col_name, []).append(label)
                hit = True
                break
        if not hit:
            unmapped.append(label)

    notes: list[str] = []
    if "title" in by_strategy:
        notes.append(
            f"Mapped by sample title: {len(by_strategy['title'])} column(s) carried titles, not GSM IDs"
        )
    for strategy, cols in by_strategy.items():
        if strategy in ("title", "accession"):
            continue
        notes.append(f"Mapped by sample field '{strategy}': {len(cols)} column(s)")
    if unmapped:
        shown = ", ".join(unmapped[:10]) + (
            f", +{len(unmapped) - 10} more" if len(unmapped) > 10 else ""
        )
        notes.append(f"Unmapped columns ignored: {shown}")
    return mapping, notes


# ---------------------------------------------------------------------------
# Table reading helpers
# ---------------------------------------------------------------------------


def _value_series(df: pd.DataFrame, gsm_id: str) -> pd.Series | None:
    """Pick the single measurement column of a per-sample table and name it after the GSM."""
    # Some on-page tables retain the feature ID as both an index and a column.
    # Numeric-looking identifiers must never be selected as measurements.
    df = df.drop(
        columns=[
            c for c in df.columns if _normalize_label(c) in {"probe", "idref", "geneid", "feature"}
        ]
    )
    if df.empty or df.shape[1] == 0:
        return None
    chosen: str | None = None
    if gsm_id in df.columns:
        chosen = gsm_id
    elif df.shape[1] == 1:
        chosen = str(df.columns[0])
    else:
        for col in df.columns:
            if any(
                hint == _normalize_label(col) or hint in _normalize_label(col)
                for hint in VALUE_COLUMN_HINTS
            ):
                chosen = str(col)
                break
        if chosen is None:
            for col in df.columns:
                if pd.to_numeric(df[col], errors="coerce").notna().any():
                    chosen = str(col)
                    break
    if chosen is None:
        return None
    series = df[chosen].copy()
    series.name = gsm_id
    return series
