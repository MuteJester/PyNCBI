"""GEOReader class for PyNCBI.

Provides a clean object-oriented facade over PyNCBI's GEO inspection,
sample extraction, matrix parsing, and download engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PyNCBI.core.logging import get_logger
from PyNCBI.core.types import DataStatus
from PyNCBI.domains.geo import (
    download_geo_files,
    extract_gse_samples,
    inspect_accession,
    load_rnaseq_counts,
    load_series_matrix,
)
from PyNCBI.domains.geo.records import fetch_sample_record, fetch_series_metadata
from PyNCBI.io.http_client import get_default_client
from PyNCBI.parsing.soft_parser import get_parser

if TYPE_CHECKING:
    from pathlib import Path

    from PyNCBI.core.models import InspectionReport
    from PyNCBI.core.protocols import HttpClient
    from PyNCBI.core.tables import Table
    from PyNCBI.parsing.matrix_parser import SeriesMatrixResult
    from PyNCBI.parsing.soft_parser import SOFTParser

logger = get_logger(__name__)


class GEOReader:
    """High-level connector for the NCBI GEO database."""

    def __init__(
        self,
        *,
        _http_client: HttpClient | None = None,
        _parser: SOFTParser | None = None,
    ) -> None:
        self._http_client = _http_client or get_default_client()
        self._parser = _parser or get_parser()
        logger.debug("GEOReader initialized")

    def inspect(self, accession: str, check_file_sizes: bool = True) -> InspectionReport:
        """Inspect any GSE, GSM, or GPL record on NCBI GEO."""
        return inspect_accession(
            accession,
            check_file_sizes=check_file_sizes,
            http_client=self._http_client,
            parser=self._parser,
        )

    def parse_gsm_soft(self, gsm_soft: str) -> dict[str, Any]:
        """Parse SOFT format text for a GSM into a dictionary."""
        return self._parser.parse_gsm_record(gsm_soft)

    def gsms_from_gse_soft(self, gse_soft: str) -> list[str]:
        """Extract GSM IDs from GSE SOFT format text."""
        return self._parser.extract_gsm_ids(gse_soft)

    def extract_gsm_info(self, gsm_id: str) -> dict[str, Any]:
        """Extract metadata for a single GSM."""
        return fetch_sample_record(gsm_id, http_client=self._http_client, parser=self._parser).info

    def extract_gse_sample_info(
        self,
        gse_id: str,
        *,
        where: str | None = None,
        save_to: str | Path | None = None,
    ) -> Table:
        """Extract metadata and unpacked characteristics for all GSM samples in a GSE in O(1) batch request."""
        combined_df, _, _ = extract_gse_samples(
            gse_id,
            where=where,
            save_to=save_to,
            http_client=self._http_client,
            parser=self._parser,
        )
        return combined_df

    def download_gse_data(
        self,
        gse_id: str,
        to_path: str | Path | None = None,
        *,
        source: str = "auto",
        unpack: bool = False,
    ) -> list[Path]:
        """Download data files for a GSE (supplementary, matrix, or rnaseq)."""
        return download_geo_files(
            gse_id,
            output_dir=to_path,
            source=source,
            unpack=unpack,
            http_client=self._http_client,
            parser=self._parser,
        )

    def load_series_matrix(
        self,
        gse_id: str,
        *,
        platform_id: str | None = None,
        save_to: str | Path | None = None,
    ) -> SeriesMatrixResult:
        """Download and parse the GEO Series Matrix file for a GSE."""
        return load_series_matrix(
            gse_id,
            platform_id=platform_id,
            save_to=save_to,
            http_client=self._http_client,
        )

    def load_rnaseq_counts(
        self,
        gse_id: str,
        *,
        count_type: str = "raw_counts",
        save_to: str | Path | None = None,
    ) -> Table:
        """Download and parse NCBI-generated RNA-seq count tables for a GSE."""
        return load_rnaseq_counts(
            gse_id,
            count_type=count_type,
            save_to=save_to,
            http_client=self._http_client,
        )

    def get_gsm_data_status(self, gsm_id: str) -> int:
        """Check data availability for a GSM (0 = on page, 1 = supplementary files, -1 = no data)."""
        status = fetch_sample_record(
            gsm_id, http_client=self._http_client, parser=self._parser
        ).status

        status_map = {
            DataStatus.DATA_ON_PAGE: 0,
            DataStatus.SUPPLEMENTARY_FILES: 1,
            DataStatus.NO_DATA: -1,
        }
        return status_map.get(status, -1)

    def get_gse_info(self, gse_id: str) -> dict[str, Any]:
        """Get GSE series metadata."""
        return fetch_series_metadata(
            gse_id, http_client=self._http_client, parser=self._parser
        ).info

    def list_gse_samples(self, gse_id: str) -> list[str]:
        """Get list of GSM IDs in a GSE."""
        return fetch_series_metadata(
            gse_id, http_client=self._http_client, parser=self._parser
        ).details.gsm_ids
