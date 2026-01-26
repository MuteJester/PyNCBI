"""Type definitions for PyNCBI.

This module contains enums, TypedDicts, dataclasses, and protocols
used throughout the package for type safety and documentation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol, TypeVar, runtime_checkable

from PyNCBI.exceptions import InvalidAccessionError

# --- Validation ---

# Patterns for valid accession IDs
_ACCESSION_PATTERNS = {
    "GSM": re.compile(r"^GSM\d+$"),
    "GSE": re.compile(r"^GSE\d+$"),
    "GPL": re.compile(r"^GPL\d+$"),
}


def validate_accession(accession: str, expected_prefix: str = "") -> str:
    """Validate an NCBI GEO accession ID.

    Args:
        accession: The accession ID to validate (e.g., 'GSM1234567')
        expected_prefix: Expected prefix ('GSM', 'GSE', 'GPL'), or empty to accept any

    Returns:
        The validated accession ID (stripped and uppercase)

    Raises:
        InvalidAccessionError: If the accession format is invalid
    """
    accession = accession.strip().upper()

    if expected_prefix:
        pattern = _ACCESSION_PATTERNS.get(expected_prefix.upper())
        if pattern is None:
            raise ValueError(f"Unknown accession prefix: {expected_prefix}")
        if not pattern.match(accession):
            raise InvalidAccessionError(accession, expected_prefix)
    else:
        # Accept any valid accession type
        if not any(p.match(accession) for p in _ACCESSION_PATTERNS.values()):
            raise InvalidAccessionError(accession)

    return accession


# --- Enums ---


class DataStatus(Enum):
    """Status of data availability on a GSM card.

    Attributes:
        DATA_ON_PAGE: Beta values are directly available in the SOFT file.
        IDAT_FILES: Data is in supplementary IDAT files requiring parsing.
        NO_DATA: No downloadable data is available.
    """

    DATA_ON_PAGE = 0
    IDAT_FILES = 1
    NO_DATA = -1


class FetchMode(Enum):
    """Mode for GSE data fetching.

    Attributes:
        PER_GSM: Fetch each GSM sample individually. Slower but more reliable.
        SUPPLEMENTARY: Use GSE supplementary file. Faster for large datasets.
    """

    PER_GSM = auto()
    SUPPLEMENTARY = auto()

    @classmethod
    def from_string(cls, value: str) -> FetchMode:
        """Convert string mode to enum (for backward compatibility).

        Args:
            value: Mode string ('per_gsm' or 'supp')

        Returns:
            Corresponding FetchMode enum value

        Raises:
            ValueError: If value is not a valid mode string
        """
        mapping = {
            "per_gsm": cls.PER_GSM,
            "supp": cls.SUPPLEMENTARY,
            "supplementary": cls.SUPPLEMENTARY,
        }
        normalized = value.lower().strip()
        if normalized not in mapping:
            valid = list(mapping.keys())
            raise ValueError(f"Invalid mode '{value}', must be one of {valid}")
        return mapping[normalized]


class ArrayType(Enum):
    """Supported methylation array types.

    Attributes:
        ARRAY_27K: Illumina HumanMethylation27 BeadChip
        ARRAY_450K: Illumina HumanMethylation450 BeadChip
        EPIC: Illumina MethylationEPIC BeadChip
    """

    ARRAY_27K = "27k"
    ARRAY_450K = "450k"
    EPIC = "epic"

    @classmethod
    def from_platform_id(cls, platform_id: str) -> ArrayType | None:
        """Get array type from GPL platform ID.

        Args:
            platform_id: NCBI GPL identifier (e.g., 'GPL13534')

        Returns:
            ArrayType if platform is supported, None otherwise
        """
        mapping = {
            "GPL13534": cls.ARRAY_450K,
            "GPL16304": cls.ARRAY_450K,
            "GPL21145": cls.EPIC,
            "GPL23976": cls.EPIC,
            "GPL8490": cls.ARRAY_27K,
        }
        return mapping.get(platform_id.upper())


# --- Protocols ---

T = TypeVar("T")


@runtime_checkable
class ProgressCallback(Protocol):
    """Protocol for progress reporting."""

    def __call__(
        self,
        current: int,
        total: int,
        description: str = "",
    ) -> None:
        """Report progress.

        Args:
            current: Current progress value
            total: Total expected value (0 if unknown)
            description: Optional description of current operation
        """
        ...


@runtime_checkable
class FileSelector(Protocol):
    """Protocol for selecting supplementary files.

    Implementations can be interactive (prompting user) or programmatic
    (automatic selection based on rules).
    """

    def select(
        self,
        files: list[str],
        gse_id: str,
    ) -> int:
        """Select a file from the list.

        Args:
            files: List of available supplementary file URLs
            gse_id: The GSE ID for context

        Returns:
            Index of selected file (0-based)

        Raises:
            ValueError: If selection is cancelled or invalid
        """
        ...


# --- Data Classes ---


@dataclass
class GSMInfo:
    """Parsed GSM metadata.

    Attributes:
        gsm_id: The GSM accession identifier
        title: Sample title
        series_id: Parent GSE series ID
        platform_id: GPL platform identifier
        characteristics: Parsed sample characteristics
        supplementary_files: List of supplementary file URLs
        data_row_count: Number of data rows in SOFT file
        array_type: Detected array type (if supported)
    """

    gsm_id: str
    title: str = ""
    series_id: str = ""
    platform_id: str = ""
    characteristics: dict[str, str] = field(default_factory=dict)
    supplementary_files: list[str] = field(default_factory=list)
    data_row_count: int = 0
    array_type: ArrayType | None = None


@dataclass
class GSEInfo:
    """Parsed GSE metadata.

    Attributes:
        gse_id: The GSE accession identifier
        title: Series title
        platform_id: GPL platform identifier
        gsm_ids: List of GSM sample IDs in this series
        supplementary_files: List of supplementary file URLs
    """

    gse_id: str
    title: str = ""
    platform_id: str = ""
    gsm_ids: list[str] = field(default_factory=list)
    supplementary_files: list[str] = field(default_factory=list)


# --- File Selector Implementations ---


class FirstFileSelector:
    """Auto-select first file (for automation/testing)."""

    def select(self, files: list[str], gse_id: str) -> int:
        """Always select the first file."""
        if not files:
            raise ValueError(f"No supplementary files available for {gse_id}")
        return 0


class IndexFileSelector:
    """Select file by index."""

    def __init__(self, index: int) -> None:
        """Initialize with target index.

        Args:
            index: The index of the file to select (0-based)
        """
        self.index = index

    def select(self, files: list[str], gse_id: str) -> int:
        """Select the file at the configured index."""
        if not files:
            raise ValueError(f"No supplementary files available for {gse_id}")
        if self.index < 0 or self.index >= len(files):
            raise ValueError(
                f"Index {self.index} out of range for {len(files)} files in {gse_id}"
            )
        return self.index


class InteractiveFileSelector:
    """Interactive file selector using terminal input."""

    def select(self, files: list[str], gse_id: str) -> int:
        """Prompt user to select a file interactively.

        Args:
            files: List of available supplementary file URLs
            gse_id: The GSE ID for context

        Returns:
            Index of selected file (0-based)
        """
        if not files:
            raise ValueError(f"No supplementary files available for {gse_id}")

        print(f"\nPlease select a supplementary file for {gse_id}:\n")
        for idx, file_url in enumerate(files, 1):
            filename = file_url.split("/")[-1]
            print(f"  {idx}. {filename}")
        print()

        while True:
            try:
                selection = int(input("Enter selection (number): ")) - 1
                if 0 <= selection < len(files):
                    return selection
                print(f"Please enter a number between 1 and {len(files)}")
            except ValueError:
                print("Please enter a valid number")
            except (KeyboardInterrupt, EOFError):
                raise ValueError("File selection cancelled")
