"""GSE (Gene Series Expression) class for PyNCBI.

This module provides the GSE class for working with collections of
methylation samples (series) from the NCBI GEO database.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pandas as pd

from PyNCBI._types import FetchMode, IndexFileSelector, InteractiveFileSelector
from PyNCBI.config import get_config
from PyNCBI.exceptions import InvalidModeError, NoDataAvailableError
from PyNCBI.io.cache import CompressedPickleCache
from PyNCBI.io.compression import extract_tarfile, gunzip_file, remove_non_idat_files
from PyNCBI.io.http_client import get_default_client
from PyNCBI.logging import get_logger
from PyNCBI.parsing.soft_parser import get_parser

if TYPE_CHECKING:
    from PyNCBI._types import FileSelector
    from PyNCBI.core.protocols import Cache, HttpClient
    from PyNCBI.GSM import GSM as GSMType

logger = get_logger(__name__)


class GSE:
    """A class to represent a GSE (Gene Series Expression) entity.

    The GSE class provides access to a collection of GSM samples from a single
    study in the NCBI GEO database. Data is automatically cached for fast
    subsequent access.

    Attributes:
        gse_id: The GSE identifier (e.g., 'GSE12345')
        GSMS: Dictionary mapping GSM IDs to GSM objects
        info: pandas Series containing series metadata
        failed_samples: List of GSM IDs that failed to fetch

    Example:
        >>> # Fetch each sample individually
        >>> gse = GSE('GSE12345', mode='per_gsm')
        >>> print(gse['GSM1234567'].data)

        >>> # Use supplementary file (faster for large datasets)
        >>> gse = GSE('GSE12345', mode='supp', file_index=0)

        >>> # With parallel fetching
        >>> gse = GSE('GSE12345', mode='per_gsm', n_threads=4)

        >>> # Modern API with enum
        >>> from PyNCBI import FetchMode
        >>> gse = GSE('GSE12345', mode=FetchMode.SUPPLEMENTARY, file_index=0)
    """

    def __init__(
        self,
        gse_id: str,
        mode: str | FetchMode,
        overwrite_cache: bool = False,
        n_threads: int = 1,
        remove_gsm_caches: bool = True,
        shell_only: bool = False,
        *,
        file_index: int | None = None,
        file_selector: FileSelector | None = None,
        _http_client: HttpClient | None = None,
        _cache: Cache[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize a GSE instance.

        Args:
            gse_id: The GSE identifier (e.g., 'GSE12345')
            mode: Fetch mode - 'per_gsm' or 'supp' (or FetchMode enum)
            overwrite_cache: If True, ignore cache and fetch fresh data
            n_threads: Number of threads for parallel fetching (per_gsm mode)
            remove_gsm_caches: If True, remove individual GSM caches after GSE cache
            shell_only: If True, only fetch metadata (no methylation data)
            file_index: Index of supplementary file to use (supp mode, 0-based)
            file_selector: Custom file selector for supp mode
            _http_client: Optional HTTP client for dependency injection
            _cache: Optional cache for dependency injection
        """
        self._gse_id = gse_id
        self._mode = self._normalize_mode(mode)
        self._http_client = _http_client or get_default_client()
        self._cache: CompressedPickleCache[dict[str, Any]] = _cache or CompressedPickleCache()
        self._parser = get_parser()
        self._config = get_config()

        # Internal storage
        self._gsms: dict[str, Any] = {}
        self._info: pd.Series | None = None
        self._failed_samples: list[str] = []
        self._gsm_ids: list[str] = []

        # File selection for supp mode
        self._file_index = file_index
        self._file_selector = file_selector

        # Load data
        if self._cache.exists(gse_id) and not overwrite_cache:
            self._load_from_cache()
        else:
            self._fetch_from_ncbi(shell_only, n_threads)
            self._save_to_cache()

            if remove_gsm_caches:
                self._remove_gsm_caches()

    # --- Public Properties ---

    @property
    def gse_id(self) -> str:
        """The GSE identifier."""
        return self._gse_id

    @gse_id.setter
    def gse_id(self, value: str) -> None:
        self._gse_id = value

    @property
    def GSMS(self) -> dict[str, Any]:
        """Dictionary of GSM objects (backward compatible)."""
        return self._gsms

    @GSMS.setter
    def GSMS(self, value: dict[str, Any] | list[str] | None) -> None:
        if value is None:
            self._gsms = {}
        elif isinstance(value, list):
            self._gsm_ids = value
            self._gsms = {}
        else:
            self._gsms = value

    @property
    def info(self) -> pd.Series:
        """Series metadata as a pandas Series."""
        return self._info if self._info is not None else pd.Series()

    @info.setter
    def info(self, value: pd.Series | None) -> None:
        self._info = value

    @property
    def no_data_GSMS(self) -> list[str]:
        """List of GSM IDs that failed to fetch (backward compatible)."""
        return self._failed_samples

    @property
    def failed_samples(self) -> list[str]:
        """List of GSM IDs that failed to fetch."""
        return self._failed_samples

    @property
    def title(self) -> str:
        """Series title."""
        return str(self._info.get("title", "")) if self._info is not None else ""

    @property
    def platform_id(self) -> str:
        """Platform GPL ID."""
        return str(self._info.get("platform_id", "")) if self._info is not None else ""

    @property
    def array_type(self) -> str:
        """Array type name (e.g., '450k', 'epic')."""
        array_map = self._config.ARRAY_TYPE_MAPPING
        platform = self.platform_id
        if platform in array_map:
            return array_map[platform].value
        return ""

    @property
    def sample_count(self) -> int:
        """Number of samples in this series."""
        return len(self._gsms)

    # --- Cache Methods ---

    def is_cached(self) -> bool:
        """Check if this GSE is cached."""
        return self._cache.exists(self._gse_id)

    def store_cache(self) -> None:
        """Save current state to cache."""
        self._save_to_cache()

    def load_cache(self) -> None:
        """Load state from cache."""
        self._load_from_cache()

    def clear_cache(self) -> bool:
        """Clear this GSE from cache."""
        return self._cache.delete(self._gse_id)

    def remove_gsm_cache(self) -> None:
        """Remove individual GSM caches (backward compatible)."""
        self._remove_gsm_caches()

    # --- Data Methods ---

    def to_dataframe(self, section: str) -> pd.DataFrame:
        """Export GSM data as a DataFrame.

        Args:
            section: Either 'info' for metadata or 'data' for methylation values

        Returns:
            DataFrame with samples as columns

        Raises:
            ValueError: If section is not 'info' or 'data'
        """
        if section == "info":
            all_info = [self._gsms[gsm].info for gsm in self._gsms]
            return pd.concat(all_info, axis=1)
        elif section == "data":
            all_data = []
            for gsm_id in self._gsms:
                gsm = self._gsms[gsm_id]
                if gsm.has_data:
                    data = gsm.data
                    if isinstance(data, pd.DataFrame):
                        all_data.append(data[gsm_id])
                    else:
                        all_data.append(data)
            return pd.concat(all_data, axis=1) if all_data else pd.DataFrame()
        else:
            raise ValueError("section must be 'info' or 'data'")

    def list_samples(self) -> list[str]:
        """Get list of all GSM IDs in this series."""
        return list(self._gsms.keys())

    # --- Internal Methods ---

    def _normalize_mode(self, mode: str | FetchMode) -> FetchMode:
        """Convert string mode to FetchMode enum."""
        if isinstance(mode, FetchMode):
            return mode

        mode_map = {
            "per_gsm": FetchMode.PER_GSM,
            "supp": FetchMode.SUPPLEMENTARY,
            "supplementary": FetchMode.SUPPLEMENTARY,
        }
        normalized = mode.lower().strip()
        if normalized not in mode_map:
            raise InvalidModeError(mode, list(mode_map.keys()))
        return mode_map[normalized]

    def _fetch_soft(self) -> str:
        """Fetch SOFT format text from NCBI."""
        url = self._config.get_ncbi_query_url(self._gse_id)
        return self._http_client.get_text(url)

    def _fetch_from_ncbi(self, shell_only: bool, n_threads: int) -> None:
        """Fetch data from NCBI."""
        # Fetch and parse metadata
        soft_text = self._fetch_soft()
        self._gsm_ids = self._parser.extract_gsm_ids(soft_text)
        self._info = self._parser.parse_gse_to_series(soft_text)

        if shell_only:
            self._fetch_gsm_shells(n_threads)
            return

        if self._mode == FetchMode.PER_GSM:
            self._fetch_per_gsm(n_threads)
        elif self._mode == FetchMode.SUPPLEMENTARY:
            self._fetch_from_supplementary()

    def _fetch_gsm_shells(self, n_threads: int) -> None:
        """Fetch GSM metadata only (no data)."""
        from tqdm.auto import tqdm

        from PyNCBI.GSM import GSM

        if n_threads <= 1:
            for gsm_id in tqdm(self._gsm_ids, desc="Fetching GSM info", leave=False):
                try:
                    self._gsms[gsm_id] = GSM(gsm_id, shell_only=True)
                except Exception:
                    self._failed_samples.append(gsm_id)
        else:
            self._fetch_parallel(self._gsm_ids, n_threads, shell_only=True)

    def _fetch_per_gsm(self, n_threads: int) -> None:
        """Fetch full GSM data for all samples."""
        from tqdm.auto import tqdm

        from PyNCBI.GSM import GSM

        if n_threads <= 1:
            for gsm_id in tqdm(self._gsm_ids, desc="Fetching GSM data", leave=False):
                try:
                    self._gsms[gsm_id] = GSM(gsm_id)
                except NoDataAvailableError:
                    self._failed_samples.append(gsm_id)
                except Exception as e:
                    logger.error("Failed to fetch %s: %s", gsm_id, e)
                    self._failed_samples.append(gsm_id)
        else:
            self._fetch_parallel(self._gsm_ids, n_threads, shell_only=False)

    def _fetch_parallel(
        self, gsm_ids: list[str], n_threads: int, shell_only: bool
    ) -> None:
        """Fetch GSMs in parallel using thread pool."""
        from tqdm.auto import tqdm

        from PyNCBI.GSM import GSM

        def fetch_one(gsm_id: str) -> tuple[str, Any | None, str | None]:
            """Fetch a single GSM, return (id, gsm, error)."""
            for attempt in range(3):  # Retry up to 3 times
                try:
                    gsm = GSM(gsm_id, shell_only=shell_only)
                    return (gsm_id, gsm, None)
                except NoDataAvailableError:
                    return (gsm_id, None, "no_data")
                except Exception as e:
                    if attempt == 2:  # Last attempt
                        return (gsm_id, None, str(e))
                    # Clean up any partial files
                    cache_folder = self._config.cache_folder
                    for f in cache_folder.glob(f"{gsm_id}*"):
                        try:
                            f.unlink()
                        except Exception:
                            pass
            return (gsm_id, None, "max_retries")

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = {executor.submit(fetch_one, gsm_id): gsm_id for gsm_id in gsm_ids}

            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Fetching GSMs",
                leave=False,
            ):
                gsm_id, gsm, error = future.result()
                if gsm is not None:
                    self._gsms[gsm_id] = gsm
                elif error:
                    self._failed_samples.append(gsm_id)

    def _fetch_from_supplementary(self) -> None:
        """Fetch data from supplementary files."""
        # Get supplementary files
        supp_files = self._info.get("supplementary_file", [])
        if isinstance(supp_files, str):
            supp_files = [supp_files]

        if not supp_files:
            raise NoDataAvailableError(self._gse_id)

        # Select file
        if self._file_index is not None:
            selector = IndexFileSelector(self._file_index)
        elif self._file_selector is not None:
            selector = self._file_selector
        elif len(supp_files) == 1:
            selector = IndexFileSelector(0)
        else:
            selector = InteractiveFileSelector()

        selected_idx = selector.select(supp_files, self._gse_id)
        selected_url = supp_files[selected_idx]
        file_name = selected_url.split("/")[-1]

        # Fetch GSM shells first
        self._fetch_gsm_shells(n_threads=3)

        # Download and parse supplementary file
        if ".csv.gz" in file_name.lower():
            self._download_csv_gz(file_name, selected_url)
        elif ".txt.gz" in file_name.lower():
            self._download_txt_gz(file_name, selected_url)
        elif ".tar" in file_name.lower():
            self._download_tar(file_name, selected_url)

    def _download_csv_gz(self, file_name: str, url: str) -> None:
        """Download and parse a CSV.gz supplementary file."""
        cache_folder = self._config.ensure_cache_dir()
        temp_folder = cache_folder / str(uuid4())
        temp_folder.mkdir(parents=True, exist_ok=True)

        try:
            # Download
            gz_path = temp_folder / file_name
            self._http_client.download_file(url, gz_path)

            # Decompress
            csv_path = temp_folder / file_name[:-3]
            gunzip_file(gz_path, csv_path, delete_source=True)

            # Load and match to GSMs
            data = pd.read_csv(csv_path, index_col=0)
            self._match_columns_to_gsms(data)

        finally:
            if temp_folder.exists():
                shutil.rmtree(temp_folder)

    def _download_txt_gz(self, file_name: str, url: str) -> None:
        """Download and parse a TXT.gz supplementary file."""
        cache_folder = self._config.ensure_cache_dir()
        temp_folder = cache_folder / str(uuid4())
        temp_folder.mkdir(parents=True, exist_ok=True)

        try:
            # Download
            gz_path = temp_folder / file_name
            self._http_client.download_file(url, gz_path)

            # Decompress
            txt_path = temp_folder / file_name[:-3]
            gunzip_file(gz_path, txt_path, delete_source=True)

            # Load and match to GSMs
            data = pd.read_table(txt_path, index_col=0, sep="\t")
            self._match_columns_to_gsms(data)

        finally:
            if temp_folder.exists():
                shutil.rmtree(temp_folder)

    def _download_tar(self, file_name: str, url: str) -> None:
        """Download and parse a TAR supplementary file (IDAT files)."""
        from PyNCBI.FileUtilities import parse_idat_files

        cache_folder = self._config.ensure_cache_dir()
        temp_folder = cache_folder / str(uuid4())
        temp_folder.mkdir(parents=True, exist_ok=True)

        try:
            # Download
            tar_path = cache_folder / file_name
            self._http_client.download_file(url, tar_path)

            # Extract
            extract_tarfile(tar_path, temp_folder)
            tar_path.unlink()

            # Remove non-IDAT files
            remove_non_idat_files(temp_folder)

            if not any(temp_folder.iterdir()):
                raise NoDataAvailableError(self._gse_id)

            # Parse IDAT files
            platform = str(self._info.get("platform_id", ""))
            array_map = self._config.ARRAY_TYPE_MAPPING
            array_type = array_map.get(platform)
            array_str = array_type.value if array_type else "450k"

            parse_idat_files(str(temp_folder) + "/", array_str)

            # Load results
            result_file = temp_folder / "parsed_beta_values.parquet"
            if result_file.exists():
                data = pd.read_parquet(result_file)
                for col in data.columns:
                    if col in self._gsms:
                        self._gsms[col].data = data[col]

        finally:
            if temp_folder.exists():
                shutil.rmtree(temp_folder)

    def _match_columns_to_gsms(self, data: pd.DataFrame) -> None:
        """Match DataFrame columns to GSM objects."""
        for column in data.columns:
            for gsm_id in self._gsms:
                gsm = self._gsms[gsm_id]
                # Match by title or exact GSM ID
                if column == gsm_id or (
                    gsm.info is not None and column in str(gsm.info.get("title", ""))
                ):
                    gsm.data = data[column]
                    gsm.data.name = gsm_id
                    break

    def _save_to_cache(self) -> None:
        """Save current state to cache."""
        cache_data = {
            "gse_id": self._gse_id,
            "GSMS": self._gsms,
            "info": self._info,
        }
        self._cache.set(self._gse_id, cache_data)

    def _load_from_cache(self) -> None:
        """Load state from cache."""
        cache_data = self._cache.get(self._gse_id)
        if cache_data:
            self._gse_id = cache_data.get("gse_id", self._gse_id)
            self._gsms = cache_data.get("GSMS", {})
            self._info = cache_data.get("info")

    def _remove_gsm_caches(self) -> None:
        """Remove individual GSM cache files."""
        for gsm_id in self._gsms:
            try:
                self._cache.delete(gsm_id)
            except Exception:
                pass

    # --- Magic Methods ---

    def __repr__(self) -> str:
        """Rich string representation."""
        try:
            from termcolor import colored

            lines = [
                f"GSE: {colored(self._gse_id, 'green', attrs=['bold'])}",
                f"Array Type: {colored(self.platform_id, 'green', attrs=['bold'])} "
                f"({colored(self.array_type, 'green', attrs=['bold'])})",
                f"Samples: {colored(str(len(self._gsms)), 'green', attrs=['bold'])}",
                f"Title: {colored(self.title, 'green', attrs=['bold'])}",
            ]
            return "\n".join(lines)
        except ImportError:
            return str(self)

    def __str__(self) -> str:
        """String representation."""
        return (
            f"GSE: {self._gse_id}\n"
            f"Array Type: {self.platform_id} ({self.array_type})\n"
            f"Samples: {len(self._gsms)}\n"
            f"Title: {self.title}"
        )

    def __len__(self) -> int:
        """Return number of GSM samples."""
        return len(self._gsms)

    def __getitem__(self, gsm_id: str) -> GSMType:
        """Get a GSM by ID.

        Args:
            gsm_id: GSM identifier

        Returns:
            GSM object

        Raises:
            KeyError: If GSM not found
        """
        if gsm_id in self._gsms:
            return self._gsms[gsm_id]
        raise KeyError(f"{gsm_id} not found in {self._gse_id}")

    def __contains__(self, gsm_id: str) -> bool:
        """Check if GSM ID is in this series."""
        return gsm_id in self._gsms

    def __iter__(self) -> Iterator[str]:
        """Iterate over GSM IDs."""
        return iter(self._gsms)

    def __eq__(self, other: object) -> bool:
        """Check equality by GSE ID."""
        if isinstance(other, GSE):
            return self._gse_id == other._gse_id
        return False

    def __hash__(self) -> int:
        """Hash by GSE ID."""
        return hash(self._gse_id)
