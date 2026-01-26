"""GSM (Gene Sample Microarray) class for PyNCBI.

This module provides the GSM class for working with individual
methylation samples from the NCBI GEO database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI._types import DataStatus
from PyNCBI.config import get_config
from PyNCBI.exceptions import NoDataAvailableError
from PyNCBI.io.cache import CompressedPickleCache
from PyNCBI.io.http_client import get_default_client
from PyNCBI.parsing.soft_parser import get_parser, parse_characteristics

if TYPE_CHECKING:
    from PyNCBI.core.protocols import Cache, HttpClient


class GSM:
    """A class to represent a single GSM (Gene Sample Microarray) entity.

    The GSM class provides access to methylation data and metadata for a single
    sample from the NCBI GEO database. Data is automatically cached for fast
    subsequent access.

    Attributes:
        gsm_id: The GSM identifier (e.g., 'GSM1234567')
        array_type: The platform/array type (e.g., 'GPL13534')
        gse: The parent GSE series ID
        info: pandas Series containing all metadata
        data: DataFrame with methylation beta values (or None if shell_only)
        characteristics: pandas Series with sample characteristics

    Example:
        >>> gsm = GSM('GSM1234567')
        >>> print(gsm.title)
        >>> print(gsm.data.head())

        >>> # Metadata only (faster)
        >>> gsm = GSM('GSM1234567', shell_only=True)
        >>> print(gsm.characteristics)

        >>> # Force refresh from NCBI
        >>> gsm = GSM('GSM1234567', overwrite_cache=True)
    """

    def __init__(
        self,
        gsm_id: str,
        shell_only: bool = False,
        overwrite_cache: bool = False,
        *,
        _http_client: HttpClient | None = None,
        _cache: Cache[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize a GSM instance.

        Args:
            gsm_id: The GSM identifier (e.g., 'GSM1234567')
            shell_only: If True, only fetch metadata (no methylation data)
            overwrite_cache: If True, ignore cache and fetch fresh data
            _http_client: Optional HTTP client for dependency injection
            _cache: Optional cache for dependency injection
        """
        self._gsm_id = gsm_id
        self._shell_only = shell_only
        self._http_client = _http_client or get_default_client()
        self._cache: CompressedPickleCache[dict[str, Any]] = _cache or CompressedPickleCache()
        self._parser = get_parser()
        self._config = get_config()

        # Internal data storage
        self._array_type: str = ""
        self._gse: str = ""
        self._info: pd.Series | None = None
        self._data: pd.DataFrame | pd.Series | None = None
        self._characteristics: pd.Series | None = None

        # Load data
        if self._cache.exists(gsm_id) and not overwrite_cache:
            self._load_from_cache()
        else:
            self._fetch_from_ncbi(shell_only)
            self._save_to_cache()

    # --- Public Properties ---

    @property
    def gsm_id(self) -> str:
        """The GSM identifier."""
        return self._gsm_id

    @property
    def array_type(self) -> str:
        """The platform/array type (GPL ID)."""
        return self._array_type

    @property
    def gse(self) -> str:
        """The parent GSE series ID."""
        return self._gse

    @property
    def info(self) -> pd.Series:
        """All metadata as a pandas Series."""
        return self._info if self._info is not None else pd.Series()

    @property
    def data(self) -> pd.DataFrame | pd.Series | str:
        """Methylation data as DataFrame.

        Returns 'Only Info Available' if shell_only=True (for backward compatibility).
        Use `has_data` property to check if data is available.
        """
        if self._data is None:
            return "Only Info Available"
        return self._data

    @property
    def characteristics(self) -> pd.Series:
        """Sample characteristics as a pandas Series."""
        return self._characteristics if self._characteristics is not None else pd.Series()

    @property
    def has_data(self) -> bool:
        """Check if methylation data is available.

        Returns:
            True if data was fetched, False if shell_only or no data available.
        """
        return self._data is not None

    @property
    def title(self) -> str:
        """Sample title."""
        return str(self._info.get("title", "")) if self._info is not None else ""

    @property
    def data_status(self) -> DataStatus:
        """Check data availability status from NCBI."""
        soft_text = self._fetch_soft()
        return self._parser.detect_data_status(soft_text)

    # --- Backward Compatibility Setters ---

    @gsm_id.setter
    def gsm_id(self, value: str) -> None:
        self._gsm_id = value

    @array_type.setter
    def array_type(self, value: str) -> None:
        self._array_type = value

    @gse.setter
    def gse(self, value: str) -> None:
        self._gse = value

    @info.setter
    def info(self, value: pd.Series) -> None:
        self._info = value

    @data.setter
    def data(self, value: pd.DataFrame | pd.Series | str) -> None:
        if isinstance(value, str):
            self._data = None
        else:
            self._data = value

    @characteristics.setter
    def characteristics(self, value: pd.Series) -> None:
        self._characteristics = value

    # --- Cache Methods ---

    def is_cached(self) -> bool:
        """Check if this GSM is cached.

        Returns:
            True if a cached version exists.
        """
        return self._cache.exists(self._gsm_id)

    def store_cache(self) -> None:
        """Save current state to cache."""
        self._save_to_cache()

    def load_cache(self) -> None:
        """Load state from cache."""
        self._load_from_cache()

    def clear_cache(self) -> bool:
        """Clear this GSM from cache.

        Returns:
            True if cache was cleared.
        """
        return self._cache.delete(self._gsm_id)

    # --- Internal Methods ---

    def _fetch_soft(self) -> str:
        """Fetch SOFT format text from NCBI."""
        url = self._config.get_ncbi_query_url(self._gsm_id)
        return self._http_client.get_text(url)

    def _fetch_from_ncbi(self, shell_only: bool) -> None:
        """Fetch data from NCBI."""
        # Fetch and parse metadata
        soft_text = self._fetch_soft()
        self._info = self._parser.parse_gsm_to_series(soft_text)
        self._info.name = self._gsm_id

        # Parse characteristics
        char_text = self._info.get("characteristics_ch1", "")
        if char_text:
            self._characteristics = parse_characteristics([char_text]).iloc[0]
        else:
            self._characteristics = pd.Series()

        self._array_type = str(self._info.get("platform_id", ""))
        self._gse = str(self._info.get("series_id", ""))

        if shell_only:
            return

        # Check data status and download
        data_status = self._parser.detect_data_status(soft_text)

        if data_status == DataStatus.NO_DATA:
            raise NoDataAvailableError(self._gsm_id)

        self._download_data(data_status, soft_text)

    def _download_data(self, data_status: DataStatus, soft_text: str) -> None:
        """Download methylation data."""
        import re
        import shutil
        from io import StringIO
        from uuid import uuid4

        import numpy as np

        from PyNCBI.io.compression import gunzip_file

        cache_folder = self._config.ensure_cache_dir()

        if data_status == DataStatus.DATA_ON_PAGE:
            # Download data directly from page
            base = self._config.NCBI_QUERY_BASE_URL
            data_url = f"{base}?acc={self._gsm_id}&targ=self&form=text&view=data"
            data_text = self._http_client.get_text(data_url)

            # Find data table start
            data_start = self._parser.find_data_table_start(data_text)
            lines = data_text.split("\n")
            data_lines = "\n".join(lines[data_start:-2])

            # Parse to DataFrame
            df = pd.read_table(StringIO(data_lines))
            df = df.rename(columns={"ID_REF": "probe", "VALUE": self._gsm_id})
            df[self._gsm_id] = df[self._gsm_id].astype(np.float32)
            self._data = df

        elif data_status == DataStatus.IDAT_FILES:
            # Download and process IDAT files
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
                    self._http_client.download_file(url, dest)

                    # Decompress if gzipped
                    if filename.endswith(".gz"):
                        gunzip_file(dest, dest.with_suffix(""), delete_source=True)

                # Get array type for methylprep
                array_map = self._config.ARRAY_TYPE_MAPPING
                array_type = array_map.get(self._array_type)
                array_str = array_type.value if array_type else "450k"

                # Parse IDAT files
                parse_idat_files(str(temp_folder) + "/", array_str)

                # Load results
                result_file = temp_folder / "parsed_beta_values.parquet"
                if result_file.exists():
                    self._data = pd.read_parquet(result_file)
                else:
                    raise NoDataAvailableError(self._gsm_id)

            finally:
                # Clean up temp folder
                if temp_folder.exists():
                    shutil.rmtree(temp_folder)

    def _save_to_cache(self) -> None:
        """Save current state to cache."""
        cache_data = {
            "gsm_id": self._gsm_id,
            "array_type": self._array_type,
            "gse": self._gse,
            "info": self._info,
            "data": self._data,
            "characteristics": self._characteristics,
        }
        self._cache.set(self._gsm_id, cache_data)

    def _load_from_cache(self) -> None:
        """Load state from cache."""
        cache_data = self._cache.get(self._gsm_id)
        if cache_data:
            self._gsm_id = cache_data.get("gsm_id", self._gsm_id)
            self._array_type = cache_data.get("array_type", "")
            self._gse = cache_data.get("gse", "")
            self._info = cache_data.get("info")
            self._data = cache_data.get("data")
            self._characteristics = cache_data.get("characteristics")

    # --- Magic Methods ---

    def __repr__(self) -> str:
        """Rich string representation."""
        try:
            from termcolor import colored

            lines = [
                f"GSM: {colored(self._gsm_id, 'green', attrs=['bold'])} | "
                f"GSE: {colored(self._gse, 'green', attrs=['bold'])}"
            ]
            if self._characteristics is not None:
                for key in self._characteristics.index:
                    value = colored(str(self._characteristics[key]), "green", attrs=["bold"])
                    lines.append(f"  {key}: {value}")
            return "\n".join(lines)
        except ImportError:
            return str(self)

    def __str__(self) -> str:
        """String representation."""
        lines = [f"GSM: {self._gsm_id} | GSE: {self._gse}"]
        if self._characteristics is not None:
            for key in self._characteristics.index:
                lines.append(f"  {key}: {self._characteristics[key]}")
        return "\n".join(lines)

    def __eq__(self, other: object) -> bool:
        """Check equality by GSM ID."""
        if isinstance(other, GSM):
            return self._gsm_id == other._gsm_id
        return False

    def __hash__(self) -> int:
        """Hash by GSM ID."""
        return hash(self._gsm_id)

    def __getitem__(self, probe_id: str) -> float:
        """Get beta value for a specific probe.

        Args:
            probe_id: CpG probe identifier (e.g., 'cg00000029')

        Returns:
            Beta value for the probe
        """
        if self._data is None:
            raise KeyError(f"No data available for {self._gsm_id}")
        return self._data.loc[probe_id, self._gsm_id]

    def __contains__(self, probe_id: str) -> bool:
        """Check if probe exists in data."""
        if self._data is None:
            return False
        return probe_id in self._data.index
