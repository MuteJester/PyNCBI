"""Type definitions, enums, file selectors, and accession validation for PyNCBI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto

from PyNCBI.core.exceptions import InvalidAccessionError
from PyNCBI.core.files import file_name, matches_file
from PyNCBI.core.protocols import FileSelector, ProgressCallback

# --- Validation ---

_ACCESSION_PATTERNS = {
    "GSM": re.compile(r"^GSM\d+$"),
    "GSE": re.compile(r"^GSE\d+$"),
    "GPL": re.compile(r"^GPL\d+$"),
    "GDS": re.compile(r"^GDS\d+$"),
    "PRJNA": re.compile(r"^PRJNA\d+$"),
    "SAMN": re.compile(r"^SAMN\d+$"),
    "SRR": re.compile(r"^SRR\d+$"),
    "SRX": re.compile(r"^SRX\d+$"),
    "SRS": re.compile(r"^SRS\d+$"),
    "SRP": re.compile(r"^SRP\d+$"),
}


def validate_accession(accession: str, expected_prefix: str = "") -> str:
    """Validate an NCBI GEO / SRA / BioProject accession ID.

    Args:
        accession: The accession ID to validate (e.g., 'GSM1234567', 'GSE85506')
        expected_prefix: Expected prefix ('GSM', 'GSE', 'GPL'), or empty to accept any

    Returns:
        The validated accession ID (stripped and uppercase)

    Raises:
        InvalidAccessionError: If the accession format is invalid
    """
    if not isinstance(accession, str) or not accession.strip():
        raise InvalidAccessionError(str(accession), expected_prefix)

    accession = accession.strip().upper()

    if expected_prefix:
        pattern = _ACCESSION_PATTERNS.get(expected_prefix.upper())
        if pattern is None:
            raise ValueError(f"Unknown accession prefix: {expected_prefix}")
        if not pattern.match(accession):
            raise InvalidAccessionError(accession, expected_prefix)
    else:
        if not any(p.match(accession) for p in _ACCESSION_PATTERNS.values()):
            raise InvalidAccessionError(accession)

    return accession


# --- Enums ---


class DataStatus(Enum):
    """Status of data availability on a GSM record.

    Attributes:
        DATA_ON_PAGE: Data table is directly embedded in the GSM SOFT file.
        SUPPLEMENTARY_FILES: Data is hosted in supplementary files on NCBI FTP/HTTPS.
        NO_DATA: No downloadable data is available on the GSM record.
    """

    DATA_ON_PAGE = 0
    SUPPLEMENTARY_FILES = 1
    NO_DATA = -1


class FetchMode(Enum):
    """Mode for GSE data fetching.

    Attributes:
        AUTO: Automatically choose the best available source.
        PER_GSM: Fetch each GSM sample individually.
        SUPPLEMENTARY: Download GSE supplementary file(s).
        SERIES_MATRIX: Download and parse the GEO Series Matrix file.
        RNASEQ_COUNTS: Download NCBI-generated RNA-seq count matrix.
    """

    AUTO = auto()
    PER_GSM = auto()
    SUPPLEMENTARY = auto()
    SERIES_MATRIX = auto()
    RNASEQ_COUNTS = auto()

    @classmethod
    def from_string(cls, value: str) -> FetchMode:
        """Convert string mode to FetchMode enum."""
        mapping = {
            "auto": cls.AUTO,
            "per_gsm": cls.PER_GSM,
            "supp": cls.SUPPLEMENTARY,
            "supplementary": cls.SUPPLEMENTARY,
            "matrix": cls.SERIES_MATRIX,
            "series_matrix": cls.SERIES_MATRIX,
            "rnaseq": cls.RNASEQ_COUNTS,
            "rnaseq_counts": cls.RNASEQ_COUNTS,
            "rnaseq-counts": cls.RNASEQ_COUNTS,
        }
        normalized = value.lower().strip()
        if normalized not in mapping:
            valid = list(mapping.keys())
            raise ValueError(f"Invalid mode '{value}', must be one of {valid}")
        return mapping[normalized]


# --- Data Classes ---


@dataclass
class GSMInfo:
    """Parsed GSM metadata."""

    gsm_id: str
    title: str = ""
    series_id: str = ""
    platform_id: str = ""
    organism: str = ""
    source_name: str = ""
    molecule: str = ""
    library_strategy: str = ""
    characteristics: dict[str, str] = field(default_factory=dict)
    supplementary_files: list[str] = field(default_factory=list)
    sra_url: str = ""
    biosample_url: str = ""
    data_row_count: int = 0


@dataclass
class GSEInfo:
    """Parsed GSE metadata."""

    gse_id: str
    title: str = ""
    summary: str = ""
    overall_design: str = ""
    platform_id: str = ""
    platforms: list[str] = field(default_factory=list)
    organisms: list[str] = field(default_factory=list)
    experiment_types: list[str] = field(default_factory=list)
    pubmed_ids: list[str] = field(default_factory=list)
    bioproject_id: str = ""
    gsm_ids: list[str] = field(default_factory=list)
    supplementary_files: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)


# --- Non-Interactive File Selectors ---


class FirstFileSelector:
    """Select the first available supplementary file."""

    def select(self, files: list[str], gse_id: str) -> int:
        if not files:
            raise ValueError(f"No supplementary files available for {gse_id}")
        return 0


class IndexFileSelector:
    """Select supplementary file by 0-based index."""

    def __init__(self, index: int) -> None:
        self.index = index

    def select(self, files: list[str], gse_id: str) -> int:
        if not files:
            raise ValueError(f"No supplementary files available for {gse_id}")
        if self.index < 0 or self.index >= len(files):
            raise ValueError(f"Index {self.index} out of range for {len(files)} files in {gse_id}")
        return self.index


class PatternFileSelector:
    """Select supplementary file matching a glob or substring pattern."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern

    def select(self, files: list[str], gse_id: str) -> int:
        if not files:
            raise ValueError(f"No supplementary files available for {gse_id}")
        for idx, url in enumerate(files):
            if matches_file(url, self.pattern):
                return idx
        available = [file_name(u) for u in files]
        raise ValueError(
            f"No supplementary file matching '{self.pattern}' in {gse_id}. "
            f"Available files: {available}"
        )


TABULAR_EXTENSIONS: tuple[str, ...] = (
    ".csv.gz",
    ".tsv.gz",
    ".txt.gz",
    ".tab.gz",
    ".csv",
    ".tsv",
    ".txt",
    ".tab",
)
"""Extensions of delimited-text tables PyNCBI can read straight from a URL."""

COMPRESSED_OUTPUT_SUFFIXES: tuple[str, ...] = (
    ".gz",
    ".gzip",
    ".bz2",
    ".xz",
    ".zip",
    ".tar",
    ".tgz",
)
"""Destination names that mean "save the remote file here, as-is", not "fill this folder"."""


def is_tabular_file(name: str) -> bool:
    """True when `name` (a filename or URL) looks like a readable data table.

    Args:
        name: Filename or URL; the query string and path are both tolerated
    """
    fname = file_name(name).lower()
    return fname.endswith(TABULAR_EXTENSIONS)


class SmartFileSelector:
    """Non-interactive selector that prioritizes tabular/matrix files over raw archives.

    Never prompts on stdin, making it safe for AI agents and automated pipelines.
    """

    def select(self, files: list[str], gse_id: str) -> int:
        if not files:
            raise ValueError(f"No supplementary files available for {gse_id}")
        if len(files) == 1:
            return 0

        for idx, url in enumerate(files):
            if is_tabular_file(url):
                return idx

        return 0


__all__ = [
    "TABULAR_EXTENSIONS",
    "COMPRESSED_OUTPUT_SUFFIXES",
    "is_tabular_file",
    "validate_accession",
    "DataStatus",
    "FetchMode",
    "GSMInfo",
    "GSEInfo",
    "ProgressCallback",
    "FileSelector",
    "FirstFileSelector",
    "IndexFileSelector",
    "PatternFileSelector",
    "SmartFileSelector",
]
