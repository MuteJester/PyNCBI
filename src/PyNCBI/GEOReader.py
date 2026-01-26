"""GEOReader class for PyNCBI.

This module provides the GEOReader class for direct interaction with the
NCBI GEO database via REST API.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from tqdm.auto import tqdm

from PyNCBI.config import get_config
from PyNCBI.io.http_client import get_default_client
from PyNCBI.logging import get_logger
from PyNCBI.parsing.soft_parser import get_parser

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient
    from PyNCBI.parsing.soft_parser import SOFTParser

logger = get_logger(__name__)


class GEOReader:
    """REST API connector for NCBI GEO database.

    This class provides methods for fetching and parsing GSM/GSE data
    directly from the NCBI GEO database.

    Attributes:
        download_folder: Path to local downloads folder

    Example:
        >>> reader = GEOReader()
        >>> info = reader.extract_gsm_info('GSM1234567')
        >>> print(info['title'])

        >>> # Extract all sample info for a series
        >>> df = reader.extract_gse_sample_info('GSE12345')
        >>> print(df.head())
    """

    def __init__(
        self,
        *,
        _http_client: HttpClient | None = None,
        _parser: SOFTParser | None = None,
    ) -> None:
        """Initialize the GEOReader.

        Args:
            _http_client: Optional HTTP client for dependency injection
            _parser: Optional SOFT parser for dependency injection
        """
        self._http_client = _http_client or get_default_client()
        self._parser = _parser or get_parser()
        self._config = get_config()
        self.download_folder = str(Path.home() / "Downloads")
        logger.debug("GEOReader initialized")

    def parse_gsm_soft(self, gsm_soft: str) -> pd.Series:
        """Parse SOFT format text for a GSM.

        This function extracts metadata from a SOFT format string containing
        GSM (sample) data.

        Args:
            gsm_soft: SOFT format text string

        Returns:
            pandas Series with parsed metadata
        """
        return self._parser.parse_gsm_to_series(gsm_soft)

    def gsms_from_gse_soft(self, gse_soft: str) -> list[str]:
        """Extract GSM IDs from GSE SOFT format text.

        Args:
            gse_soft: SOFT format text for a GSE

        Returns:
            List of GSM IDs found in the GSE
        """
        return self._parser.extract_gsm_ids(gse_soft)

    def extract_gsm_info(self, gsm_id: str, verbose: bool = False) -> pd.Series:
        """Extract metadata for a single GSM.

        Args:
            gsm_id: GSM identifier (e.g., 'GSM1234567')
            verbose: If True, log progress messages at INFO level

        Returns:
            pandas Series with GSM metadata
        """
        logger.debug("Extracting info for %s", gsm_id)
        url = self._config.get_ncbi_query_url(gsm_id)
        soft_text = self._http_client.get_text(url)

        gsm_info = self._parser.parse_gsm_to_series(soft_text)
        gsm_info.name = gsm_id

        if verbose:
            logger.info("%s info extracted successfully", gsm_id)
        else:
            logger.debug("%s info extracted successfully", gsm_id)

        return gsm_info

    def extract_gse_sample_info(
        self,
        gse_id: str,
        *,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Extract metadata for all GSM samples in a GSE.

        Args:
            gse_id: GSE identifier (e.g., 'GSE12345')
            use_cache: If True, use cached CSV if it exists

        Returns:
            DataFrame with metadata for all samples (samples as rows)
        """
        # Check for cached file
        cache_path = Path(self.download_folder) / f"{gse_id}_INFO.csv"
        if use_cache and cache_path.exists():
            logger.info("Loading cached info from %s", cache_path)
            return pd.read_csv(cache_path, index_col=0)

        # Fetch GSE SOFT data
        logger.info("Fetching %s metadata from NCBI", gse_id)
        url = self._config.get_ncbi_query_url(gse_id)
        soft_text = self._http_client.get_text(url)

        # Extract GSM IDs
        gsm_ids = self._parser.extract_gsm_ids(soft_text)
        logger.info("Found %d samples in %s", len(gsm_ids), gse_id)

        # Fetch info for each GSM
        retrieved_data = []
        failed_count = 0
        for gsm_id in tqdm(gsm_ids, desc="Extracting GSM info", leave=False):
            try:
                info = self.extract_gsm_info(gsm_id)
                retrieved_data.append(info)
            except Exception as e:
                logger.warning("Failed to extract %s: %s", gsm_id, e)
                failed_count += 1

        if not retrieved_data:
            logger.error("No GSM info could be extracted for %s", gse_id)
            return pd.DataFrame()

        if failed_count > 0:
            logger.warning("Failed to extract %d/%d samples", failed_count, len(gsm_ids))

        logger.info("GSM info extraction complete for %s", gse_id)

        # Build DataFrame
        df = pd.concat(retrieved_data, axis=1).T

        # Set index to GSM ID if available
        if "^SAMPLE" in df.columns:
            df = df.set_index("^SAMPLE")

        # Save to cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path)
        logger.info("Saved info to %s", cache_path)

        return df

    def download_gse_data(
        self,
        gse_id: str,
        to_path: str | Path | None = None,
    ) -> None:
        """Download methylation data for all GSMs in a GSE.

        Args:
            gse_id: GSE identifier
            to_path: Destination folder (default: cache folder)
        """
        from PyNCBI.Utilities import download_gsm_data

        logger.info("Downloading data for %s", gse_id)

        # Fetch GSE SOFT data
        url = self._config.get_ncbi_query_url(gse_id)
        soft_text = self._http_client.get_text(url)

        # Extract GSM IDs
        gsm_ids = self._parser.extract_gsm_ids(soft_text)
        logger.info("Found %d samples to download", len(gsm_ids))

        # Download each GSM
        success_count = 0
        for gsm_id in tqdm(gsm_ids, desc="Downloading GSMs"):
            try:
                download_gsm_data(gsm_id, str(to_path) if to_path else None)
                success_count += 1
            except Exception as e:
                logger.error("Failed to download %s: %s", gsm_id, e)

        logger.info("Downloaded %d/%d samples for %s", success_count, len(gsm_ids), gse_id)

    # --- Convenience methods ---

    def get_gsm_data_status(self, gsm_id: str) -> int:
        """Check data availability for a GSM.

        Args:
            gsm_id: GSM identifier

        Returns:
            0: Data on page
            1: IDAT files
            -1: No data available
        """
        url = self._config.get_ncbi_query_url(gsm_id)
        soft_text = self._http_client.get_text(url)
        status = self._parser.detect_data_status(soft_text)

        # Convert to legacy integer format for backward compatibility
        from PyNCBI._types import DataStatus

        status_map = {
            DataStatus.DATA_ON_PAGE: 0,
            DataStatus.IDAT_FILES: 1,
            DataStatus.NO_DATA: -1,
        }
        return status_map.get(status, -1)

    def get_gse_info(self, gse_id: str) -> pd.Series:
        """Get GSE series metadata.

        Args:
            gse_id: GSE identifier

        Returns:
            pandas Series with GSE metadata
        """
        url = self._config.get_ncbi_query_url(gse_id)
        soft_text = self._http_client.get_text(url)
        return self._parser.parse_gse_to_series(soft_text)

    def list_gse_samples(self, gse_id: str) -> list[str]:
        """Get list of GSM IDs in a GSE.

        Args:
            gse_id: GSE identifier

        Returns:
            List of GSM IDs
        """
        url = self._config.get_ncbi_query_url(gse_id)
        soft_text = self._http_client.get_text(url)
        return self._parser.extract_gsm_ids(soft_text)
