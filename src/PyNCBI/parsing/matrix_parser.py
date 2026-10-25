"""GEO Series Matrix (`GSE*_series_matrix.txt.gz`) parser for PyNCBI.

Parses NCBI GEO Series Matrix files into:
1. `matrix_df`: The data matrix (rows = `ID_REF` / probes / genes, columns = `GSM` IDs)
2. `samples_df`: Sample metadata and unpacked `!Sample_characteristics_ch1` fields
3. `series_info`: Series-level metadata (`!Series_*`)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO

import pandas as pd

from PyNCBI.core.types import table_read_options


@dataclass
class SeriesMatrixResult:
    """Parsed GEO Series Matrix content."""

    gse_id: str
    series_info: pd.Series
    samples_df: pd.DataFrame
    characteristics_df: pd.DataFrame
    matrix_df: pd.DataFrame


class SeriesMatrixParser:
    """Parser for NCBI GEO Series Matrix text files."""

    def parse(self, text: str, gse_id: str = "") -> SeriesMatrixResult:
        """Parse decompressed Series Matrix text into structured DataFrames."""
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

        samples_df, char_df = self._build_sample_tables(sample_rows)
        matrix_df = self._build_matrix_df(table_lines)

        return SeriesMatrixResult(
            gse_id=gse_id,
            series_info=pd.Series(series_dict),
            samples_df=samples_df,
            characteristics_df=char_df,
            matrix_df=matrix_df,
        )

    @staticmethod
    def _unquote(val: str) -> str:
        val = val.strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            return val[1:-1]
        return val

    def _build_sample_tables(
        self, sample_rows: list[tuple[str, list[str]]]
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not sample_rows:
            return pd.DataFrame(), pd.DataFrame()

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

        index = pd.Index(gsm_ids, name="gsm_id")
        samples_df = pd.DataFrame(meta_per_sample, index=index)
        char_df = pd.DataFrame(char_per_sample, index=index)
        return samples_df, char_df

    def _build_matrix_df(self, table_lines: list[str]) -> pd.DataFrame:
        if len(table_lines) < 2:
            return pd.DataFrame()

        try:
            df = pd.read_csv(
                StringIO("\n".join(table_lines)),
                sep="\t",
                index_col=0,
                low_memory=False,
                **table_read_options(),
            )
            df.index = df.index.astype(str)
            df.index.name = "probe"
            df.columns = [str(c).strip('"') for c in df.columns]
            return df
        except Exception:
            return pd.DataFrame()
