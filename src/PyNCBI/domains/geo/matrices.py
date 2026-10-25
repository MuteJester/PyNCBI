"""One definition of sample-column alignment and per-sample measurement selection."""

from __future__ import annotations

import re
from typing import Any

from PyNCBI.core.exceptions import DataError
from PyNCBI.core.tables import Table, iter_records, table

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
    sample_table: Table,
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
    if not sample_table or not sample_table["rows"]:
        return {}, ["Sample sheet unavailable for this series; matrix columns kept as published"]

    gsm_index = {
        str(row["gsm_id"]).upper(): str(row["gsm_id"]) for row in iter_records(sample_table)
    }
    mapping: dict[str, str] = {}
    by_strategy: dict[str, list[str]] = {}
    unmapped: list[str] = []

    lookups: list[tuple[str, dict[str, str]]] = []
    for col_name in identifying_fields:
        if col_name not in sample_table["columns"]:
            continue
        counts: dict[str, list[str]] = {}
        for record in iter_records(sample_table):
            gsm, value = record["gsm_id"], record.get(col_name)
            if value is None:
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
        for col_name, lookup in lookups:
            if key and key in lookup:
                mapping[label] = lookup[key]
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


def sample_values(value: Table, gsm_id: str) -> Table | None:
    """Pick a sample measurement field without modifying or coercing any source value."""
    columns = [
        name
        for name in value["columns"]
        if _normalize_label(name) not in {"probe", "idref", "geneid", "feature", "featureid"}
    ]
    if not value["rows"] or not columns:
        return None
    chosen = gsm_id if gsm_id in columns else columns[0] if len(columns) == 1 else None
    if chosen is None:
        chosen = next(
            (
                name
                for name in columns
                if any(hint in _normalize_label(name) for hint in VALUE_COLUMN_HINTS)
            ),
            None,
        )
    if chosen is None:
        chosen = next(
            (
                name
                for name in columns
                if any(
                    isinstance(row[value["columns"].index(name)], (int, float))
                    for row in value["rows"]
                )
            ),
            None,
        )
    if chosen is None:
        return None
    position = value["columns"].index(chosen)
    return table(["feature_id", gsm_id], ([row[0], row[position]] for row in value["rows"]))


def join_sample_values(values: list[Table]) -> Table:
    """Align per-sample measurements by their published feature IDs, without imputation."""
    names = [value["columns"][1] for value in values]
    if len(set(names)) != len(names):
        raise DataError("More than one measurement table was selected for the same sample")
    rows: dict[str, list[Any]] = {}
    for column, value in enumerate(values, start=1):
        seen = set()
        for identifier, measurement in value["rows"]:
            if identifier in seen:
                raise DataError(
                    f"Duplicate feature identifier {identifier!r} in sample {value['columns'][1]}"
                )
            seen.add(identifier)
            if identifier not in rows:
                rows[identifier] = [identifier, *([None] * len(values))]
            rows[identifier][column] = measurement
    return {"columns": ["feature_id", *names], "rows": list(rows.values())}
