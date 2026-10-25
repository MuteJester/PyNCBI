"""GEO DataSet (GDS) SOFT parser for PyNCBI.

GDS records are NCBI-curated value tables (`ID_REF`, `IDENTIFIER`, one column per GSM)
plus experimental subsets (e.g. `genotype/variation: wild type` -> GSM list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.exceptions import DataError

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass
class GDSResult:
    """Parsed GEO DataSet."""

    gds_id: str
    info: dict[str, str] = field(default_factory=dict)
    subsets: list[dict[str, Any]] = field(default_factory=list)
    column_descriptions: dict[str, str] = field(default_factory=dict)
    table_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def sample_groups_df(self) -> pd.DataFrame:
        """Samples x subset-type matrix (e.g. 'genotype/variation' -> 'wild type')."""
        rows: dict[str, dict[str, str]] = {}
        for sub in self.subsets:
            for gsm in sub.get("sample_ids", []):
                rows.setdefault(gsm, {})[str(sub.get("type", "subset"))] = str(
                    sub.get("description", "")
                )
        df = pd.DataFrame.from_dict(rows, orient="index")
        df.index.name = "gsm_id"
        return df

    def expression_df(self) -> pd.DataFrame:
        """Numeric value matrix only (GSM columns), indexed by `ID_REF`."""
        gsm_cols = [c for c in self.table_df.columns if str(c).startswith("GSM")]
        return self.table_df[gsm_cols]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gds_id": self.gds_id,
            "info": self.info,
            "subsets": self.subsets,
            "shape": list(self.table_df.shape),
            "columns": [str(c) for c in self.table_df.columns],
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
    df = pd.DataFrame(norm, columns=header, dtype=str)
    df = df.set_index(header[0])
    df.index = df.index.astype(str)
    df.index.name = header[0]
    for col in df.columns:
        if str(col).startswith("GSM"):
            df[col] = pd.to_numeric(
                df[col].mask(df[col].isin(["null", "", "NA", "NaN"])), errors="coerce"
            )
    return GDSResult(
        gds_id=gds_id, info=info, subsets=subsets, column_descriptions=col_desc, table_df=df
    )
