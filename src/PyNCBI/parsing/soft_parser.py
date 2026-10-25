"""SOFT format parser for NCBI GEO data.

Provides a unified parser for SOFT (Simple Omnibus Format in Text) files
used by NCBI GEO, including single-record (GSM, GSE, GPL) parsing and
O(1) batch GSE sample family parsing.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from io import StringIO
from typing import TYPE_CHECKING

import pandas as pd

from PyNCBI.core.exceptions import DataError, NoDataAvailableError, SOFTParseError
from PyNCBI.core.types import DataStatus, GSEInfo, GSMInfo, table_read_options
from PyNCBI.io.urls import supplementary_urls

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


def _ensure_list(val: str | list[str] | None) -> list[str]:
    if not val:
        return []
    if isinstance(val, str):
        return [v.strip() for v in val.split("\n") if v.strip()]
    return [str(v).strip() for v in val if str(v).strip()]


def _ensure_str(val: str | list[str] | None) -> str:
    if not val:
        return ""
    if isinstance(val, list):
        return "\n".join(str(v) for v in val)
    return str(val)


@dataclass
class SOFTParser:
    """Parser for NCBI GEO SOFT format files (GSM, GSE, GPL, and batch GSMs)."""

    _GSE_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"GSE\d+"), init=False, repr=False
    )
    _GPL_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"GPL\d+"), init=False, repr=False
    )
    _GSM_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"GSM\d+"), init=False, repr=False
    )
    _PRJNA_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"PRJNA\d+"), init=False, repr=False
    )
    _ROW_COUNT_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"!Sample_data_row_count\s*=\s*(\d+)"),
        init=False,
        repr=False,
    )
    _SUPP_FILE_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"!Sample_supplementary_file[^\s=]*\s*=\s*(.+)"),
        init=False,
        repr=False,
    )

    def parse_gsm_to_series(self, soft_text: str) -> pd.Series:
        """Parse GSM SOFT text into a pandas Series."""
        lines = soft_text.split("\n")
        lines = [line for line in lines if "!Sample_" in line or "^SAMPLE" in line]
        lines = [line.replace("!Sample_", "") for line in lines]
        lines = [line for line in lines if len(line) > 0]

        processed_data: dict[str, list[str]] = {}
        for line in lines:
            parts = line.split("=", 1)
            if len(parts) != 2:
                continue

            key = parts[0].strip()
            value = parts[1].strip()

            if key in processed_data:
                processed_data[key].append(value)
            else:
                processed_data[key] = [value]

        result: dict[str, str] = {}
        for key, values in processed_data.items():
            result[key] = "\n".join(values)

        return pd.Series(result)

    def parse_gsm(self, soft_text: str, gsm_id: str | None = None) -> GSMInfo:
        """Parse GSM SOFT text into a structured GSMInfo dataclass."""
        try:
            series = self.parse_gsm_to_series(soft_text)

            if gsm_id is None:
                gsm_id = str(series.get("^SAMPLE", "")).strip()
                if not gsm_id:
                    match = self._GSM_PATTERN.search(soft_text)
                    gsm_id = match.group() if match else ""

            char_text = _ensure_str(series.get("characteristics_ch1", ""))
            char_ch2 = _ensure_str(series.get("characteristics_ch2", ""))
            if char_ch2:
                char_text = f"{char_text}\n{char_ch2}".strip()
            characteristics = self._parse_characteristics(char_text)

            supp_files = supplementary_urls(self._SUPP_FILE_PATTERN.findall(soft_text))

            row_match = self._ROW_COUNT_PATTERN.search(soft_text)
            row_count = int(row_match.group(1)) if row_match else 0

            platform_id = _ensure_str(series.get("platform_id", "")).split("\n")[0].strip()

            relations = _ensure_list(series.get("relation", ""))
            sra_url = ""
            biosample_url = ""
            for rel in relations:
                if "SRA:" in rel:
                    sra_url = rel.split("SRA:", 1)[1].strip()
                elif "BioSample:" in rel:
                    biosample_url = rel.split("BioSample:", 1)[1].strip()

            return GSMInfo(
                gsm_id=gsm_id,
                title=_ensure_str(series.get("title", "")),
                series_id=_ensure_str(series.get("series_id", "")),
                platform_id=platform_id,
                organism=_ensure_str(series.get("organism_ch1", "")),
                source_name=_ensure_str(series.get("source_name_ch1", "")),
                molecule=_ensure_str(series.get("molecule_ch1", "")),
                library_strategy=_ensure_str(series.get("library_strategy", "")),
                characteristics=characteristics,
                supplementary_files=supp_files,
                sra_url=sra_url,
                biosample_url=biosample_url,
                data_row_count=row_count,
            )

        except Exception as e:
            raise SOFTParseError(gsm_id or "unknown", str(e)) from e

    def parse_gse_to_series(self, soft_text: str) -> pd.Series:
        """Parse GSE SOFT text into a pandas Series."""
        lines = [
            line
            for line in soft_text.split("\n")
            if "!Series_sample_id" not in line and len(line) > 1
        ]

        aggregated: dict[str, list[str]] = {}
        for line in lines:
            parts = line.split("=", 1)
            if len(parts) != 2:
                continue

            key = parts[0][8:].strip() if parts[0].startswith("!Series_") else parts[0].strip()
            value = parts[1].strip()

            if key not in aggregated:
                aggregated[key] = [value]
            else:
                aggregated[key].append(value)

        result: dict[str, str | list[str]] = {}
        for key, values in aggregated.items():
            result[key] = values[0] if len(values) == 1 else values

        return pd.Series(result)

    def parse_gse(self, soft_text: str, gse_id: str | None = None) -> GSEInfo:
        """Parse GSE SOFT text into a structured GSEInfo dataclass."""
        try:
            gsm_ids = self.extract_gsm_ids(soft_text)

            if gse_id is None:
                match = self._GSE_PATTERN.search(soft_text)
                gse_id = match.group() if match else ""

            series = self.parse_gse_to_series(soft_text)

            supp_files = supplementary_urls(_ensure_list(series.get("supplementary_file")))

            platforms = _ensure_list(series.get("platform_id"))
            primary_platform = platforms[0] if platforms else ""

            organisms = _ensure_list(
                series.get("sample_organism") or series.get("platform_organism")
            )
            experiment_types = _ensure_list(series.get("type"))
            pubmed_ids = _ensure_list(series.get("pubmed_id"))
            relations = _ensure_list(series.get("relation"))

            bioproject_id = ""
            for rel in relations:
                m = self._PRJNA_PATTERN.search(rel)
                if m:
                    bioproject_id = m.group(0)
                    break

            return GSEInfo(
                gse_id=gse_id,
                title=_ensure_str(series.get("title", "")),
                summary=_ensure_str(series.get("summary", "")),
                overall_design=_ensure_str(series.get("overall_design", "")),
                platform_id=primary_platform,
                platforms=platforms,
                organisms=organisms,
                experiment_types=experiment_types,
                pubmed_ids=pubmed_ids,
                bioproject_id=bioproject_id,
                gsm_ids=gsm_ids,
                supplementary_files=supp_files,
                relations=relations,
            )

        except Exception as e:
            raise SOFTParseError(gse_id or "unknown", str(e)) from e

    def parse_gpl_to_series(self, soft_text: str) -> pd.Series:
        """Parse GPL (Platform) SOFT text into a pandas Series."""
        lines = [
            line
            for line in soft_text.split("\n")
            if line.startswith("!Platform_") or line.startswith("^PLATFORM")
        ]
        aggregated: dict[str, list[str]] = {}
        for line in lines:
            parts = line.split("=", 1)
            if len(parts) != 2:
                continue
            key = parts[0].replace("!Platform_", "").strip()
            value = parts[1].strip()
            aggregated.setdefault(key, []).append(value)

        result: dict[str, str | list[str]] = {
            k: (v[0] if len(v) == 1 else v) for k, v in aggregated.items()
        }
        return pd.Series(result)

    def parse_gse_samples_batch(self, batch_soft_text: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Parse a batch GSM SOFT response (`targ=gsm&view=brief`) into DataFrames.

        This parses all GSM samples in a GSE from a single HTTP response in O(1)
        network round-trips.

        Args:
            batch_soft_text: Raw SOFT text containing multiple `^SAMPLE = GSM...` blocks

        Returns:
            Tuple of `(info_df, characteristics_df)`:
                - `info_df`: All `!Sample_*` metadata fields indexed by GSM ID
                - `characteristics_df`: Unpacked `key: value` columns from
                  `!Sample_characteristics_ch1` aligned by key name across all samples
        """
        raw_blocks = re.split(r"(?=\^SAMPLE\s*=)", batch_soft_text)
        info_rows: list[dict[str, str]] = []
        char_rows: list[dict[str, str]] = []
        gsm_index: list[str] = []

        for block in raw_blocks:
            block = block.strip()
            if not block.startswith("^SAMPLE"):
                continue

            series = self.parse_gsm_to_series(block)
            gsm_id = str(series.get("^SAMPLE", "")).strip()
            if not gsm_id:
                continue

            gsm_index.append(gsm_id)
            row_dict = {str(k): str(v) for k, v in series.items()}
            info_rows.append(row_dict)

            char_text = row_dict.get("characteristics_ch1", "")
            char_ch2 = row_dict.get("characteristics_ch2", "")
            if char_ch2:
                char_text = f"{char_text}\n{char_ch2}".strip()
            char_dict = self._parse_characteristics(char_text)
            char_rows.append(char_dict)

        if not gsm_index:
            return pd.DataFrame(), pd.DataFrame()

        info_df = pd.DataFrame(info_rows, index=pd.Index(gsm_index, name="gsm_id"))
        char_df = pd.DataFrame(char_rows, index=pd.Index(gsm_index, name="gsm_id"))
        return info_df, char_df

    def extract_gsm_ids(self, gse_soft_text: str) -> list[str]:
        """Extract GSM IDs from GSE SOFT text."""
        lines = gse_soft_text.split("\n")
        gsm_ids = [
            line.split("=")[1].strip()
            for line in lines
            if "!Series_sample_id" in line and "=" in line
        ]
        return gsm_ids

    def detect_data_status(self, gsm_soft_text: str) -> DataStatus:
        """Detect data availability status for a GSM.

        Status meanings:
            DATA_ON_PAGE (0): Embedded data table directly on GSM page (`row_count > 0`)
            SUPPLEMENTARY_FILES / IDAT_FILES (1): Data hosted in supplementary files
            NO_DATA (-1): Neither embedded table nor supplementary files available
        """
        valid_supp = supplementary_urls(self._SUPP_FILE_PATTERN.findall(gsm_soft_text))

        row_match = self._ROW_COUNT_PATTERN.search(gsm_soft_text)
        row_count = int(row_match.group(1)) if row_match else 0

        # Embedded table on page takes priority when row_count > 0
        if row_count > 0:
            return DataStatus.DATA_ON_PAGE

        # Otherwise if there are downloadable supplementary files
        if len(valid_supp) > 0:
            return DataStatus.SUPPLEMENTARY_FILES

        return DataStatus.NO_DATA

    def find_data_table_start(self, gsm_data_text: str, max_lines: int = 50) -> int:
        """Find the line number where the tabular data header starts in GSM/GPL data text."""
        lines = gsm_data_text.split("\n")
        for i, line in enumerate(lines):
            if i >= max_lines:
                break
            if (
                line.startswith("!sample_table_begin") or line.startswith("!platform_table_begin")
            ) and i + 1 < len(lines):
                return i + 1
            if "ID_REF\tVALUE" in line or ("ID_REF" in line and "\t" in line):
                return i
            if line.startswith("ID\t") and not line.startswith("!"):
                return i

        raise ValueError(f"GSM data table start not found in first {max_lines} lines")

    def parse_gsm_data_table(self, data_text: str, gsm_id: str) -> pd.DataFrame:
        """Parse GSM `view=data` text into a clean DataFrame indexed by `probe` (`ID_REF`)."""
        data_start = self.find_data_table_start(data_text)
        lines = data_text.split("\n")

        # Filter out trailing SOFT footer markers (!sample_table_end, empty lines)
        clean_lines: list[str] = []
        for line in lines[data_start:]:
            stripped = line.strip()
            if not stripped or stripped.startswith("!sample_table_end") or stripped.startswith("^"):
                continue
            clean_lines.append(line)

        if not clean_lines:
            return pd.DataFrame()

        df = pd.read_csv(
            StringIO("\n".join(clean_lines)), sep="\t", low_memory=False, **table_read_options()
        )
        df = df.rename(columns={"ID_REF": "probe", "VALUE": gsm_id})

        if "probe" in df.columns:
            df["probe"] = df["probe"].astype(str)
            df.index = pd.Index(df["probe"], name="probe")

        return df

    def parse_gpl_table_lines(
        self,
        lines: Iterable[str],
        *,
        columns: list[str] | None = None,
        limit: int | None = None,
        gpl_id: str = "",
    ) -> pd.DataFrame:
        """Parse a (possibly streamed) GPL platform table into a DataFrame indexed by `ID`.

        Unlike GSM data tables, GPL annotation tables are wide, heterogeneous, and must
        keep every column verbatim (`Gene Symbol`, `ENTREZ_GENE_ID`, `UCSC_RefGene_Name`,
        ...). Values are kept as strings so multi-valued cells (`780 /// 100616237`) and
        leading-zero identifiers are never coerced.

        Args:
            lines: Iterable of text lines (full `view=data` SOFT text or a streaming iterator)
            columns: Optional subset of column names to keep (the `ID` column is always kept)
            limit: Optional maximum number of data rows to read (stops streaming early)
            gpl_id: Platform accession for error messages

        Raises:
            NoDataAvailableError: The platform genuinely has no table (sequencing platforms
                are virtual and report 0 rows)
            DataError: A table was expected but the reply contained no table section
        """
        iterator: Iterator[str] = iter(lines)
        header: list[str] | None = None
        rows: list[list[str]] = []
        in_table = False
        saw_table_marker = False
        technology = ""
        declared_rows: int | None = None

        for line in iterator:
            if not in_table:
                lowered = line.lower()
                if lowered.startswith("!platform_technology"):
                    technology = line.partition("=")[2].strip()
                elif lowered.startswith("!platform_data_row_count"):
                    with contextlib.suppress(ValueError):
                        declared_rows = int(line.partition("=")[2].strip())
                if line.startswith("!platform_table_begin"):
                    in_table = True
                    saw_table_marker = True
                elif (
                    not saw_table_marker
                    and header is None
                    and line.startswith("ID\t")
                    and not line.startswith(("!", "#", "^"))
                ):
                    # Table without SOFT markers (e.g., already-extracted TSV)
                    in_table = True
                    header = line.split("\t")
                continue

            if line.startswith("!platform_table_end"):
                break
            if not line.strip():
                continue
            if header is None:
                header = line.split("\t")
                continue
            if limit is not None and len(rows) >= limit:
                break
            rows.append(line.split("\t"))

        if header is None:
            label = gpl_id or "this platform"
            if "sequencing" in technology.lower() or declared_rows == 0:
                detail = (
                    f"{label} is a {technology or 'virtual'} platform and carries no probe "
                    "annotation table; GEO reports 0 table rows for it"
                )
                raise NoDataAvailableError(
                    gpl_id or "GPL",
                    detail=detail,
                    hint=(
                        "Probe tables exist only for array platforms. For sequencing studies the "
                        "gene annotation comes from the counts table instead: use "
                        "annotate_gene_ids() for Entrez GeneIDs, or load_rnaseq_annotation() for "
                        "the full NCBI annotation table."
                    ),
                )
            raise DataError(
                f"No platform data table found in {label} SOFT text",
                hint=(
                    "The reply contained no !platform_table_begin section. Check the accession, "
                    "or fetch it with fetch_gpl_table() which requests the table view."
                ),
            )

        n_cols = len(header)
        normalized_rows = [
            r[:n_cols] + [""] * (n_cols - len(r)) if len(r) != n_cols else r for r in rows
        ]
        df = pd.DataFrame(normalized_rows, columns=header, dtype=str)
        id_col = header[0]
        df = df.set_index(id_col)
        df.index.name = "ID" if id_col.upper() in ("ID", "ID_REF") else id_col
        df.index = df.index.astype(str)

        if columns:
            missing = [c for c in columns if c not in df.columns]
            if missing:
                raise DataError(
                    f"Column(s) {missing} not found in {gpl_id or 'GPL'} table",
                    hint=f"Available columns: {list(df.columns)[:40]}",
                )
            df = df[list(columns)]
        return df

    def parse_gpl_data_table(self, data_text: str, gpl_id: str = "") -> pd.DataFrame:
        """Parse full GPL `view=data` SOFT text into a DataFrame indexed by `ID`."""
        return self.parse_gpl_table_lines(data_text.splitlines(), gpl_id=gpl_id)

    def _parse_characteristics(self, char_text: str) -> dict[str, str]:
        """Parse characteristics section into a dictionary by key name."""
        result: dict[str, str] = {}

        for line in char_text.split("\n"):
            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            clean_key = key.strip()
            clean_val = value.strip()
            if not clean_key:
                continue

            if clean_key in result and result[clean_key] != clean_val:
                result[clean_key] = f"{result[clean_key]}; {clean_val}"
            else:
                result[clean_key] = clean_val

        return result


_parser: SOFTParser | None = None


def get_parser() -> SOFTParser:
    """Get the default SOFT parser instance."""
    global _parser
    if _parser is None:
        _parser = SOFTParser()
    return _parser


def parse_characteristics(
    char_sections: list[str], indices: list[str] | None = None
) -> pd.DataFrame:
    """Parse characteristics sections into a DataFrame."""
    parser = get_parser()
    rows = [parser._parse_characteristics(section) for section in char_sections]
    df = pd.DataFrame(rows)
    if indices is not None:
        df.index = pd.Index(indices)
    return df
