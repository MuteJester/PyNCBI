"""GEO DataSet (GDS) SOFT parser for PyNCBI.

GDS records are NCBI-curated value tables (`ID_REF`, `IDENTIFIER`, one column per GSM)
plus experimental subsets (e.g. `genotype/variation: wild type` -> GSM list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PyNCBI.core.exceptions import DataError
from PyNCBI.core.tables import Table, from_records, project, table
from PyNCBI.parsing.tabular import parse_delimited_table

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass
class GDSResult:
    """Parsed GEO DataSet."""

    gds_id: str
    info: dict[str, str] = field(default_factory=dict)
    subsets: list[dict[str, Any]] = field(default_factory=list)
    column_descriptions: dict[str, str] = field(default_factory=dict)
    table: Table = field(default_factory=table)

    @property
    def sample_groups(self) -> Table:
        """Samples x subset-type matrix (e.g. 'genotype/variation' -> 'wild type')."""
        rows: dict[str, dict[str, str]] = {}
        for sub in self.subsets:
            for gsm in sub.get("sample_ids", []):
                rows.setdefault(gsm, {})[str(sub.get("type", "subset"))] = str(
                    sub.get("description", "")
                )
        return from_records({"gsm_id": gsm, **values} for gsm, values in rows.items())

    def expression_table(self) -> Table:
        """Hosted measurement fields, with the explicit feature identifier column."""
        return project(
            self.table,
            [
                name
                for name in self.table["columns"]
                if name == "feature_id" or name.startswith("GSM")
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gds_id": self.gds_id,
            "info": self.info,
            "subsets": self.subsets,
            "table": self.table,
            "shape": [len(self.table["rows"]), len(self.table["columns"])],
            "columns": self.table["columns"],
        }


def parse_gds_soft(lines: Iterable[str], gds_id: str = "") -> GDSResult:
    """Parse GDS SOFT text (or a streamed line iterator) into a `GDSResult`."""
    info: dict[str, str] = {}
    subsets: list[dict[str, Any]] = []
    col_desc: dict[str, str] = {}
    current_subset: dict[str, Any] | None = None
    header: list[str] | None = None
    rows: list[list[str]] = []
    in_table = False
    saw_table = False

    for line in lines:
        if in_table:
            if line.startswith("!dataset_table_end"):
                break
            if not line.strip():
                continue
            if header is None:
                header = line.rstrip("\n").split("\t")
            else:
                rows.append(line.rstrip("\n").split("\t"))
            continue
        if line.startswith("!dataset_table_begin"):
            in_table = True
            saw_table = True
            continue
        if line.startswith("^DATASET"):
            current_subset = None
            if not gds_id and "=" in line:
                gds_id = line.split("=", 1)[1].strip()
            continue
        if line.startswith("^SUBSET"):
            current_subset = {
                "id": line.split("=", 1)[1].strip() if "=" in line else "",
                "description": "",
                "sample_ids": [],
                "type": "",
            }
            subsets.append(current_subset)
            continue
        if line.startswith("!subset_") and current_subset is not None and "=" in line:
            key, val = line[len("!subset_") :].split("=", 1)
            key, val = key.strip(), val.strip()
            if key == "sample_id":
                current_subset["sample_ids"] = [s.strip() for s in val.split(",") if s.strip()]
            elif key == "description":
                current_subset["description"] = val
            elif key == "type":
                current_subset["type"] = val
            continue
        if line.startswith("!dataset_") and "=" in line:
            key, val = line[len("!dataset_") :].split("=", 1)
            info[key.strip()] = val.strip()
            continue
        if line.startswith("#") and "=" in line:
            key, val = line[1:].split("=", 1)
            col_desc[key.strip()] = val.strip()

    if not saw_table or header is None:
        raise DataError(f"No data table found in {gds_id or 'GDS'} SOFT file")
    width = len(header)
    norm = [r[:width] + [""] * (width - len(r)) if len(r) != width else r for r in rows]
    text = "\n".join("\t".join(row) for row in [header, *norm])
    df = parse_delimited_table(
        text,
        f"{gds_id}.tsv",
        text_columns=[col for col in header[1:] if not col.startswith("GSM")],
    )
    invalid = [
        name
        for index, name in enumerate(df["columns"])
        if name.startswith("GSM")
        and any(
            row[index] is not None and not isinstance(row[index], (int, float))
            for row in df["rows"]
        )
    ]
    if invalid:
        raise DataError(
            f"Non-numeric measurements in {gds_id or 'GDS'} column(s): {', '.join(invalid)}"
        )
    return GDSResult(
        gds_id=gds_id, info=info, subsets=subsets, column_descriptions=col_desc, table=df
    )
