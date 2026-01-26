"""SOFT format parser for NCBI GEO data.

This module provides a unified parser for SOFT (Simple Omnibus Format in Text)
files used by NCBI GEO. It consolidates the parsing logic that was previously
duplicated across GSM.py, GEOReader.py, and Utilities.py.

SOFT format reference: https://www.ncbi.nlm.nih.gov/geo/info/soft.html
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from PyNCBI._types import ArrayType, DataStatus, GSEInfo, GSMInfo
from PyNCBI.exceptions import SOFTParseError


@dataclass
class SOFTParser:
    """Parser for NCBI GEO SOFT format files.

    This parser handles both GSM (sample) and GSE (series) SOFT files,
    extracting metadata into structured dataclasses.

    Example:
        parser = SOFTParser()

        # Parse GSM SOFT text
        gsm_info = parser.parse_gsm(soft_text)
        print(gsm_info.title)
        print(gsm_info.characteristics)

        # Parse GSE SOFT text
        gse_info = parser.parse_gse(soft_text)
        print(gse_info.gsm_ids)

        # Get raw pandas Series (for backward compatibility)
        series = parser.parse_gsm_to_series(soft_text)
    """

    # Regex patterns for extracting specific fields
    _GSE_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"GSE\d+"), init=False, repr=False
    )
    _GPL_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"GPL\d+"), init=False, repr=False
    )
    _GSM_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"GSM\d+"), init=False, repr=False
    )
    _ROW_COUNT_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"!Sample_data_row_count\s*=\s*(\d+)"),
        init=False,
        repr=False,
    )
    _SUPP_FILE_PATTERN: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"!Sample_supplementary_file\s*=\s*(.+)"),
        init=False,
        repr=False,
    )

    def parse_gsm_to_series(self, soft_text: str) -> pd.Series:
        """Parse GSM SOFT text into a pandas Series.

        This method provides backward compatibility with the original
        parsing logic in GSM.__extract_info() and GEOReader.parse_gsm_soft().

        Args:
            soft_text: Raw SOFT format text for a GSM

        Returns:
            pandas Series with parsed metadata
        """
        lines = soft_text.split("\n")

        # Filter to sample lines only
        lines = [line for line in lines if "!Sample_" in line or "^SAMPLE" in line]

        # Remove !Sample_ prefix
        lines = [line.replace("!Sample_", "") for line in lines]

        # Filter empty lines
        lines = [line for line in lines if len(line) > 0]

        # Parse key-value pairs
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

        # Collapse lists to newline-separated strings
        result: dict[str, str] = {}
        for key, values in processed_data.items():
            result[key] = "\n".join(values)

        return pd.Series(result)

    def parse_gsm(self, soft_text: str, gsm_id: str | None = None) -> GSMInfo:
        """Parse GSM SOFT text into a GSMInfo dataclass.

        Args:
            soft_text: Raw SOFT format text for a GSM
            gsm_id: Optional GSM ID (extracted from text if not provided)

        Returns:
            GSMInfo with parsed metadata

        Raises:
            SOFTParseError: If parsing fails
        """
        try:
            series = self.parse_gsm_to_series(soft_text)

            # Extract GSM ID from ^SAMPLE line if not provided
            if gsm_id is None:
                gsm_id = series.get("^SAMPLE", "")
                if not gsm_id:
                    # Try to find it in the text
                    match = self._GSM_PATTERN.search(soft_text)
                    gsm_id = match.group() if match else ""

            # Extract characteristics
            characteristics = self._parse_characteristics(
                series.get("characteristics_ch1", "")
            )

            # Extract supplementary files
            supp_matches = self._SUPP_FILE_PATTERN.findall(soft_text)
            supp_files = [m.strip() for m in supp_matches]

            # Extract row count
            row_match = self._ROW_COUNT_PATTERN.search(soft_text)
            row_count = int(row_match.group(1)) if row_match else 0

            # Detect array type
            platform_id = series.get("platform_id", "")
            array_type = ArrayType.from_platform_id(platform_id)

            return GSMInfo(
                gsm_id=gsm_id,
                title=series.get("title", ""),
                series_id=series.get("series_id", ""),
                platform_id=platform_id,
                characteristics=characteristics,
                supplementary_files=supp_files,
                data_row_count=row_count,
                array_type=array_type,
            )

        except Exception as e:
            raise SOFTParseError(gsm_id or "unknown", str(e)) from e

    def parse_gse_to_series(self, soft_text: str) -> pd.Series:
        """Parse GSE SOFT text into a pandas Series.

        This method provides backward compatibility with
        Utilities.parse_and_compress_gse_info().

        Args:
            soft_text: Raw SOFT format text for a GSE

        Returns:
            pandas Series with parsed metadata
        """
        # Remove GSM ID entries and filter empty lines
        lines = [
            line
            for line in soft_text.split("\n")
            if "!Series_sample_id" not in line and len(line) > 1
        ]

        # Parse key-value pairs
        aggregated: dict[str, list[str]] = {}
        for line in lines:
            parts = line.split("=", 1)
            if len(parts) != 2:
                continue

            # Skip !Series_ prefix (8 characters)
            key = parts[0][8:].strip() if parts[0].startswith("!Series_") else parts[0].strip()
            value = parts[1].strip()

            if key not in aggregated:
                aggregated[key] = [value]
            else:
                aggregated[key].append(value)

        # Convert single-item lists to strings
        result: dict[str, str | list[str]] = {}
        for key, values in aggregated.items():
            result[key] = values[0] if len(values) == 1 else values

        return pd.Series(result)

    def parse_gse(self, soft_text: str, gse_id: str | None = None) -> GSEInfo:
        """Parse GSE SOFT text into a GSEInfo dataclass.

        Args:
            soft_text: Raw SOFT format text for a GSE
            gse_id: Optional GSE ID (extracted from text if not provided)

        Returns:
            GSEInfo with parsed metadata

        Raises:
            SOFTParseError: If parsing fails
        """
        try:
            # Extract GSM IDs
            gsm_ids = self.extract_gsm_ids(soft_text)

            # Extract GSE ID if not provided
            if gse_id is None:
                match = self._GSE_PATTERN.search(soft_text)
                gse_id = match.group() if match else ""

            # Parse the rest of the metadata
            series = self.parse_gse_to_series(soft_text)

            # Extract supplementary files
            supp_files = series.get("supplementary_file", [])
            if isinstance(supp_files, str):
                supp_files = [supp_files]

            # Get platform
            platform = series.get("platform_id", "")
            if isinstance(platform, list):
                platform = platform[0] if platform else ""

            return GSEInfo(
                gse_id=gse_id,
                title=series.get("title", "") if isinstance(series.get("title", ""), str) else "",
                platform_id=platform,
                gsm_ids=gsm_ids,
                supplementary_files=supp_files,
            )

        except Exception as e:
            raise SOFTParseError(gse_id or "unknown", str(e)) from e

    def extract_gsm_ids(self, gse_soft_text: str) -> list[str]:
        """Extract GSM IDs from GSE SOFT text.

        This replaces Utilities.gsms_from_gse_soft() and
        GEOReader.gsms_from_gse_soft().

        Args:
            gse_soft_text: Raw SOFT format text for a GSE

        Returns:
            List of GSM IDs in the series
        """
        lines = gse_soft_text.split("\n")
        gsm_ids = [
            line.split("=")[1].strip()
            for line in lines
            if "!Series_sample_id" in line and "=" in line
        ]
        return gsm_ids

    def detect_data_status(self, gsm_soft_text: str) -> DataStatus:
        """Detect data availability status for a GSM.

        This replaces Utilities.gsm_page_data_status().

        Args:
            gsm_soft_text: Raw SOFT format text for a GSM

        Returns:
            DataStatus indicating how data is available

        Status meanings:
            DATA_ON_PAGE: Beta values directly in SOFT file
            IDAT_FILES: Data in supplementary IDAT files
            NO_DATA: No downloadable data available
        """
        # Find supplementary files
        supp_files = self._SUPP_FILE_PATTERN.findall(gsm_soft_text)

        # Get row count
        row_match = self._ROW_COUNT_PATTERN.search(gsm_soft_text)
        row_count = int(row_match.group(1)) if row_match else 0

        # Check for IDAT files (exactly 2 supplementary files with 'idat' in name)
        if len(supp_files) == 2 and any("idat" in f.lower() for f in supp_files):
            return DataStatus.IDAT_FILES

        # Check for data on page
        if len(supp_files) == 1:
            supp = supp_files[0].upper()
            if ("NONE" in supp or "Red" not in supp_files[0]) and row_count > 0:
                return DataStatus.DATA_ON_PAGE

        # Check if there's data even without supplementary files
        if row_count > 0 and len(supp_files) == 0:
            return DataStatus.DATA_ON_PAGE

        return DataStatus.NO_DATA

    def find_data_table_start(self, gsm_data_text: str, max_lines: int = 10) -> int:
        """Find the line number where data table starts in GSM data text.

        This replaces Utilities.gsm_data_file_table_start().

        Args:
            gsm_data_text: Text containing GSM data with headers
            max_lines: Maximum lines to search (default 10)

        Returns:
            Line number (0-indexed) where data starts

        Raises:
            ValueError: If data start not found within max_lines
        """
        for i, line in enumerate(gsm_data_text.split("\n")):
            if "ID_REF\tVALUE" in line or "ID_REF" in line and "VALUE" in line:
                return i
            if i >= max_lines:
                break

        raise ValueError(f"GSM data table start not found in first {max_lines} lines")

    def _parse_characteristics(self, char_text: str) -> dict[str, str]:
        """Parse characteristics section into a dictionary.

        Args:
            char_text: Characteristics text (newline-separated key:value pairs)

        Returns:
            Dictionary of characteristic key-value pairs
        """
        result: dict[str, str] = {}

        for line in char_text.split("\n"):
            if ":" not in line:
                continue

            # Split on first colon only (values may contain colons)
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()

        return result


# Module-level singleton for convenience
_parser: SOFTParser | None = None


def get_parser() -> SOFTParser:
    """Get the default SOFT parser instance.

    Returns:
        Default SOFTParser instance
    """
    global _parser
    if _parser is None:
        _parser = SOFTParser()
    return _parser


# Convenience functions for backward compatibility


def parse_characteristics(
    char_sections: list[str], indices: list[str] | None = None
) -> pd.DataFrame:
    """Parse characteristics sections into a DataFrame.

    This is a backward-compatible wrapper around SOFTParser._parse_characteristics().

    Args:
        char_sections: List of characteristics text strings
        indices: Optional index values for the DataFrame

    Returns:
        DataFrame with characteristics as columns
    """
    parser = get_parser()
    rows = [parser._parse_characteristics(section) for section in char_sections]
    df = pd.DataFrame(rows)
    if indices is not None:
        df.index = indices
    return df
