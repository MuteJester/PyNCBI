"""GEO Series Matrix (`GSE*_series_matrix.txt.gz`) parser for PyNCBI.

Parses NCBI GEO Series Matrix files into:
1. `matrix`: The data matrix (rows = `ID_REF` / probes / genes, columns = `GSM` IDs)
2. `sample_table`: Sample metadata and unpacked `!Sample_characteristics_ch1` fields
3. `series_info`: Series-level metadata (`!Series_*`)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Any

from PyNCBI.core.tables import Table, from_records, table
from PyNCBI.parsing.tabular import parse_delimited_table


@dataclass
class SeriesMatrixResult:
    """Parsed GEO Series Matrix content."""

    gse_id: str
    series_info: dict[str, Any]
    sample_table: Table
    characteristics_table: Table
    matrix: Table


class SeriesMatrixParser:
    """Parser for NCBI GEO Series Matrix text files."""

    def parse(self, text: str, gse_id: str = "") -> SeriesMatrixResult:
        """Parse decompressed Series Matrix text into structured native tables."""
        series_meta: dict[str, list[str]] = {}
        sample_rows: list[tuple[str, list[str]]] = []
        table_lines: list[str] = []
        in_table = False

        for line in text.splitlines():
            if not line.strip():
                continue

            if line.startswith("!series_matrix_table_begin"):
                in_table = True
                continue
            if line.startswith("!series_matrix_table_end"):
                in_table = False
                continue

            if in_table:
                table_lines.append(line)
                continue

            if line.startswith("!Series_"):
                parts = line.split("\t")
                key = parts[0][len("!Series_") :].strip()
                vals = [self._unquote(v) for v in parts[1:] if v.strip()]
                if vals:
                    series_meta.setdefault(key, []).extend(vals)
            elif line.startswith("!Sample_"):
                reader = csv.reader([line], delimiter="\t", quotechar='"')
                row = next(reader)
                if len(row) >= 2:
                    key = row[0][len("!Sample_") :].strip()
                    vals = [v.strip() for v in row[1:]]
                    sample_rows.append((key, vals))

        series_dict: dict[str, str | list[str]] = {
            k: (v[0] if len(v) == 1 else v) for k, v in series_meta.items()
        }
        if not gse_id and "geo_accession" in series_dict:
            acc_val = series_dict["geo_accession"]
            gse_id = acc_val if isinstance(acc_val, str) else acc_val[0]

        sample_table, char_df = self._build_sample_tables(sample_rows)
        matrix = self._build_matrix(table_lines)

        return SeriesMatrixResult(
            gse_id=gse_id,
            series_info=series_dict,
            sample_table=sample_table,
            characteristics_table=char_df,
            matrix=matrix,
        )

    @staticmethod
    def _unquote(val: str) -> str:
        val = val.strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            return val[1:-1]
        return val

    def _build_sample_tables(self, sample_rows: list[tuple[str, list[str]]]) -> tuple[Table, Table]:
        if not sample_rows:
            return table(), table()

        # Find sample geo_accession row to determine GSM IDs and sample count
        gsm_ids: list[str] = []
        for key, vals in sample_rows:
            if key == "geo_accession":
                gsm_ids = vals
                break

        if not gsm_ids:
            max_len = max(len(vals) for _, vals in sample_rows)
            gsm_ids = [f"Sample_{i + 1}" for i in range(max_len)]

        n_samples = len(gsm_ids)
        meta_per_sample: list[dict[str, str]] = [{} for _ in range(n_samples)]
        char_per_sample: list[dict[str, str]] = [{} for _ in range(n_samples)]

        for key, vals in sample_rows:
            for idx in range(min(n_samples, len(vals))):
                val = vals[idx]
                if not val:
                    continue

                if key.startswith("characteristics_ch") and ":" in val:
                    ckey, cval = val.split(":", 1)
                    ckey = ckey.strip()
                    cval = cval.strip()
                    if ckey:
                        if ckey in char_per_sample[idx] and char_per_sample[idx][ckey] != cval:
                            char_per_sample[idx][ckey] += f"; {cval}"
                        else:
                            char_per_sample[idx][ckey] = cval

                if key in meta_per_sample[idx]:
                    meta_per_sample[idx][key] += f"\n{val}"
                else:
                    meta_per_sample[idx][key] = val

        return (
            from_records(
                {"gsm_id": gsm, **row} for gsm, row in zip(gsm_ids, meta_per_sample, strict=True)
            ),
            from_records(
                {"gsm_id": gsm, **row} for gsm, row in zip(gsm_ids, char_per_sample, strict=True)
            ),
        )

    def _build_matrix(self, table_lines: list[str]) -> Table:
        if not table_lines:
            return table()
        return parse_delimited_table("\n".join(table_lines), "series_matrix.tsv")
