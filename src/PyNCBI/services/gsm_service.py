"""GSM service for fetching and managing single sample data.

This service encapsulates the business logic for fetching GSM data,
extracting from the legacy GSM class constructor.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pandas as pd

from PyNCBI._types import DataStatus, GSMInfo
from PyNCBI.config import get_config
from PyNCBI.exceptions import NoDataAvailableError
from PyNCBI.io.cache import CompressedPickleCache
from PyNCBI.io.compression import gunzip_file
from PyNCBI.io.http_client import HttpClient, get_default_client
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from PyNCBI.core.protocols import Cache


class GSMService:
    """Service for fetching and managing GSM (sample) data.

    This service handles:
    - Fetching GSM metadata from NCBI
    - Downloading methylation data (IDAT or direct)
    - Caching for performance
    - Parsing SOFT format

    Example:
        service = GSMService()

        # Fetch with caching
        data = service.fetch("GSM1234567")

        # Force refresh
        data = service.fetch("GSM1234567", use_cache=False)

        # Metadata only
        info = service.fetch_info("GSM1234567")
    """

    def __init__(
        self,
        http_client: HttpClient | None = None,
        cache: Cache[dict[str, Any]] | None = None,
        parser: SOFTParser | None = None,
    ) -> None:
        """Initialize the GSM service.

        Args:
            http_client: HTTP client for network requests (uses default if None)
            cache: Cache for storing fetched data (uses default if None)
            parser: SOFT parser (uses default if None)
        """
        self.http_client = http_client or get_default_client()
        self.cache = cache or CompressedPickleCache()
        self.parser = parser or get_parser()
        self.config = get_config()

    def fetch(
        self,
        gsm_id: str,
        shell_only: bool = False,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Fetch GSM data with optional caching.

        Args:
            gsm_id: GSM accession ID (e.g., 'GSM1234567')
            shell_only: If True, fetch metadata only (no data download)
            use_cache: If True, use cached data if available

        Returns:
            Dictionary with GSM data:
                - gsm_id: GSM identifier
                - array_type: Platform ID
                - gse: Parent GSE ID
                - info: pandas Series with metadata
                - data: DataFrame with methylation data (or string if shell_only)
                - characteristics: pandas Series with sample characteristics

        Raises:
            NoDataAvailableError: If no data is available and shell_only=False
        """
        # Check cache first
        if use_cache and self.cache.exists(gsm_id):
            cached = self.cache.get(gsm_id)
            if cached is not None:
                return cached

        # Fetch fresh data
        data = self._fetch_fresh(gsm_id, shell_only)

        # Cache the result
        self.cache.set(gsm_id, data)

        return data

    def fetch_info(self, gsm_id: str) -> GSMInfo:
        """Fetch only GSM metadata (no data download).

        Args:
            gsm_id: GSM accession ID

        Returns:
            GSMInfo with parsed metadata
        """
        soft_text = self._fetch_soft(gsm_id)
        return self.parser.parse_gsm(soft_text, gsm_id)

    def get_data_status(self, gsm_id: str) -> DataStatus:
        """Check data availability status for a GSM.

        Args:
            gsm_id: GSM accession ID

        Returns:
            DataStatus indicating how data is available
        """
        soft_text = self._fetch_soft(gsm_id)
        return self.parser.detect_data_status(soft_text)

    def _fetch_soft(self, gsm_id: str) -> str:
        """Fetch SOFT format text for a GSM.

        Args:
            gsm_id: GSM accession ID

        Returns:
            Raw SOFT format text
        """
        url = self.config.get_ncbi_query_url(gsm_id)
        return self.http_client.get_text(url)

    def _fetch_fresh(self, gsm_id: str, shell_only: bool) -> dict[str, Any]:
        """Fetch fresh GSM data from NCBI.

        Args:
            gsm_id: GSM accession ID
            shell_only: If True, skip data download

        Returns:
            Dictionary with GSM data
        """
        # Fetch and parse metadata
        soft_text = self._fetch_soft(gsm_id)
        info_series = self.parser.parse_gsm_to_series(soft_text)
        info_series.name = gsm_id

        # Parse characteristics
        from PyNCBI.parsing.soft_parser import parse_characteristics

        char_text = info_series.get("characteristics_ch1", "")
        characteristics = parse_characteristics([char_text]).iloc[0] if char_text else pd.Series()

        # Build result
        result: dict[str, Any] = {
            "gsm_id": gsm_id,
            "array_type": info_series.get("platform_id", ""),
            "gse": info_series.get("series_id", ""),
            "info": info_series,
            "data": "Only Info Available",
            "characteristics": characteristics,
        }

        if shell_only:
            return result

        # Check data status and download
        data_status = self.parser.detect_data_status(soft_text)

        if data_status == DataStatus.NO_DATA:
            raise NoDataAvailableError(gsm_id)

        # Download and process data
        data = self._download_data(gsm_id, data_status, soft_text, result["array_type"])
        result["data"] = data

        return result

    def _download_data(
        self,
        gsm_id: str,
        data_status: DataStatus,
        soft_text: str,
        array_type: str,
    ) -> pd.DataFrame | pd.Series:
        """Download methylation data for a GSM.

        Args:
            gsm_id: GSM accession ID
            data_status: Data availability status
            soft_text: SOFT format text
            array_type: Platform/array type

        Returns:
            DataFrame or Series with methylation data
        """
        import numpy as np

        cache_folder = self.config.ensure_cache_dir()

        if data_status == DataStatus.DATA_ON_PAGE:
            # Download data directly from page
            base = self.config.NCBI_QUERY_BASE_URL
            data_url = f"{base}?acc={gsm_id}&targ=self&form=text&view=data"
            data_text = self.http_client.get_text(data_url)

            # Find data table start
            data_start = self.parser.find_data_table_start(data_text)
            lines = data_text.split("\n")
            data_lines = "\n".join(lines[data_start:-2])

            # Parse to DataFrame
            from io import StringIO

            df = pd.read_table(StringIO(data_lines))
            df = df.rename(columns={"ID_REF": "probe", "VALUE": gsm_id})
            df[gsm_id] = df[gsm_id].astype(np.float32)

            return df

        elif data_status == DataStatus.IDAT_FILES:
            # Download and process IDAT files
            return self._process_idat_files(gsm_id, soft_text, array_type, cache_folder)

        raise NoDataAvailableError(gsm_id)

    def _process_idat_files(
        self,
        gsm_id: str,
        soft_text: str,
        array_type: str,
        cache_folder: Path,
    ) -> pd.DataFrame:
        """Download and process IDAT files.

        Args:
            gsm_id: GSM accession ID
            soft_text: SOFT format text
            array_type: Platform/array type
            cache_folder: Cache folder path

        Returns:
            DataFrame with beta values
        """
        import re

        from PyNCBI.FileUtilities import parse_idat_files

        # Extract supplementary file URLs
        supp_pattern = r"!Sample_supplementary_file = (.+)"
        supp_matches = re.findall(supp_pattern, soft_text)
        supp_urls = [m.strip() for m in supp_matches]

        # Create temp folder
        temp_folder = cache_folder / str(uuid4())
        temp_folder.mkdir(parents=True, exist_ok=True)

        try:
            # Download IDAT files
            for url in supp_urls:
                filename = url.split("/")[-1]
                dest = temp_folder / filename
                self.http_client.download_file(url, dest)

                # Decompress if gzipped
                if filename.endswith(".gz"):
                    gunzip_file(dest, dest.with_suffix(""), delete_source=True)

            # Get array type string for methylprep
            array_type_map = self.config.ARRAY_TYPE_MAPPING
            arr = array_type_map.get(array_type)
            array_str = arr.value if arr else "450k"

            # Parse IDAT files
            parse_idat_files(str(temp_folder) + "/", array_str)

            # Load results
            result_file = temp_folder / "parsed_beta_values.parquet"
            if result_file.exists():
                return pd.read_parquet(result_file)

            raise NoDataAvailableError(gsm_id)

        finally:
            # Clean up temp folder
            if temp_folder.exists():
                shutil.rmtree(temp_folder)
