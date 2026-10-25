"""Native table payloads: plain columns/rows dictionaries, with no table engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict, TypeGuard

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence


class Table(TypedDict):
    """A JSON-compatible table; identifiers are ordinary, explicit columns."""

    columns: list[str]
    rows: list[list[Any]]


def table(columns: Sequence[str] = (), rows: Iterable[Sequence[Any]] = ()) -> Table:
    """Build a table, rejecting ambiguous columns and ragged rows."""
    names = [str(name) for name in columns]
    if len(set(names)) != len(names):
        raise ValueError("Table column names must be unique")
    values = [list(row) for row in rows]
    if any(len(row) != len(names) for row in values):
        raise ValueError("Every table row must match its column count")
    return {"columns": names, "rows": values}


def is_table(value: Any) -> TypeGuard[Table]:
    """Recognize a native table without accepting arbitrary dictionaries as tables."""
    return (
        isinstance(value, dict)
        and set(value) == {"columns", "rows"}
        and isinstance(value["columns"], list)
        and isinstance(value["rows"], list)
    )


def iter_records(value: Table) -> Iterator[dict[str, Any]]:
    """Expose row dictionaries lazily; callers choose their own analysis library."""
    for row in value["rows"]:
        yield dict(zip(value["columns"], row, strict=True))


def from_records(
    records: Iterable[Mapping[str, Any]], columns: Sequence[str] | None = None
) -> Table:
    """Serialize native records as a compact table, retaining field order."""
    records = list(records)
    names = (
        list(columns)
        if columns is not None
        else list(dict.fromkeys(k for record in records for k in record))
    )
    return table(names, ([record.get(name) for name in names] for record in records))


def column(value: Table, name: str) -> list[Any]:
    """Read one field without a DataFrame conversion."""
    index = value["columns"].index(name)
    return [row[index] for row in value["rows"]]


def project(value: Table, names: Sequence[str]) -> Table:
    """Select fields for transport/output; no inference or data transformation."""
    positions = [value["columns"].index(name) for name in names]
    return table(names, ([row[i] for i in positions] for row in value["rows"]))


def by_id(value: Table, name: str) -> dict[str, dict[str, Any]]:
    """Index records by their explicit accession/feature identifier."""
    return {str(row[name]): row for row in iter_records(value)}


def preview_records(
    value: Table, max_rows: int = 15, max_cols: int | None = None
) -> list[dict[str, Any]]:
    """Convert a bounded preview to row dictionaries for agent/CLI responses."""
    names = value["columns"][:max_cols]
    return [dict(zip(names, row[:max_cols], strict=True)) for row in value["rows"][:max_rows]]


def csv_text(value: Table, delimiter: str = ",") -> str:
    """Render native table data as CSV/TSV text for resource transports."""
    import csv
    import io

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=delimiter)
    writer.writerow(value["columns"])
    writer.writerows(value["rows"])
    return output.getvalue()
