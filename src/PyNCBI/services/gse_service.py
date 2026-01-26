"""GSE service for fetching and managing series data.

This service encapsulates the business logic for fetching GSE data,
extracting from the legacy GSE class constructor.
"""

from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI._types import FetchMode, GSEInfo
from PyNCBI.config import get_config
from PyNCBI.exceptions import NoDataAvailableError
from PyNCBI.io.cache import CompressedPickleCache
from PyNCBI.io.http_client import HttpClient, get_default_client
from PyNCBI.logging import get_logger
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser
from PyNCBI.services.gsm_service import GSMService

if TYPE_CHECKING:
    from PyNCBI._types import FileSelector
    from PyNCBI.core.protocols import Cache

logger = get_logger(__name__)


class GSEService:
    """Service for fetching and managing GSE (series) data.

    This service handles:
    - Fetching GSE metadata from NCBI
    - Fetching all GSM samples in a series
    - Multiple fetch modes (per_gsm, supplementary)
    - Parallel fetching with threading
    - Caching for performance

    Example:
        service = GSEService()

        # Fetch using per_gsm mode
        data = service.fetch("GSE12345", mode=FetchMode.PER_GSM)

        # Fetch using supplementary files (first file)
        data = service.fetch("GSE12345", mode=FetchMode.SUPPLEMENTARY, file_index=0)

        # With threading
        data = service.fetch("GSE12345", mode=FetchMode.PER_GSM, n_threads=4)
    """

    def __init__(
        self,
        http_client: HttpClient | None = None,
        cache: Cache[dict[str, Any]] | None = None,
        parser: SOFTParser | None = None,
        gsm_service: GSMService | None = None,
    ) -> None:
        """Initialize the GSE service.

        Args:
            http_client: HTTP client for network requests
            cache: Cache for storing fetched data
            parser: SOFT parser
            gsm_service: GSM service for fetching individual samples
        """
        self.http_client = http_client or get_default_client()
        self.cache = cache or CompressedPickleCache()
        self.parser = parser or get_parser()
        self.gsm_service = gsm_service or GSMService(
            http_client=self.http_client,
            cache=self.cache,
            parser=self.parser,
        )
        self.config = get_config()

    def fetch(
        self,
        gse_id: str,
        mode: FetchMode | str,
        use_cache: bool = True,
        n_threads: int = 1,
        shell_only: bool = False,
        file_index: int | None = None,
        file_selector: FileSelector | None = None,
    ) -> dict[str, Any]:
        """Fetch GSE data with all samples.

        Args:
            gse_id: GSE accession ID (e.g., 'GSE12345')
            mode: Fetch mode (PER_GSM or SUPPLEMENTARY)
            use_cache: If True, use cached data if available
            n_threads: Number of threads for parallel fetching (per_gsm mode)
            shell_only: If True, fetch metadata only
            file_index: Index of supplementary file to use (supp mode)
            file_selector: Custom file selector for supp mode

        Returns:
            Dictionary with GSE data:
                - gse_id: GSE identifier
                - info: pandas Series with metadata
                - gsms: Dict mapping GSM ID to GSM data
                - failed_gsms: List of GSM IDs that failed to fetch

        Raises:
            InvalidModeError: If mode is invalid
        """
        # Convert string mode to enum
        if isinstance(mode, str):
            warnings.warn(
                f"String mode='{mode}' is deprecated. Use FetchMode enum instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            mode = FetchMode.from_string(mode)

        # Check cache first
        if use_cache and self.cache.exists(gse_id):
            cached = self.cache.get(gse_id)
            if cached is not None:
                return cached

        # Fetch fresh data
        data = self._fetch_fresh(
            gse_id=gse_id,
            mode=mode,
            n_threads=n_threads,
            shell_only=shell_only,
            file_index=file_index,
            file_selector=file_selector,
        )

        # Cache the result
        self.cache.set(gse_id, data)

        return data

    def fetch_info(self, gse_id: str) -> GSEInfo:
        """Fetch only GSE metadata (no sample data).

        Args:
            gse_id: GSE accession ID

        Returns:
            GSEInfo with parsed metadata
        """
        soft_text = self._fetch_soft(gse_id)
        return self.parser.parse_gse(soft_text, gse_id)

    def list_gsm_ids(self, gse_id: str) -> list[str]:
        """Get list of GSM IDs in a GSE.

        Args:
            gse_id: GSE accession ID

        Returns:
            List of GSM IDs
        """
        soft_text = self._fetch_soft(gse_id)
        return self.parser.extract_gsm_ids(soft_text)

    def list_supplementary_files(self, gse_id: str) -> list[str]:
        """Get list of supplementary files for a GSE.

        Args:
            gse_id: GSE accession ID

        Returns:
            List of supplementary file URLs
        """
        info = self.fetch_info(gse_id)
        return info.supplementary_files

    def _fetch_soft(self, gse_id: str) -> str:
        """Fetch SOFT format text for a GSE.

        Args:
            gse_id: GSE accession ID

        Returns:
            Raw SOFT format text
        """
        url = self.config.get_ncbi_query_url(gse_id)
        return self.http_client.get_text(url)

    def _fetch_fresh(
        self,
        gse_id: str,
        mode: FetchMode,
        n_threads: int,
        shell_only: bool,
        file_index: int | None,
        file_selector: FileSelector | None,
    ) -> dict[str, Any]:
        """Fetch fresh GSE data from NCBI.

        Args:
            gse_id: GSE accession ID
            mode: Fetch mode
            n_threads: Number of threads
            shell_only: Metadata only flag
            file_index: Supplementary file index
            file_selector: Custom file selector

        Returns:
            Dictionary with GSE data
        """
        # Fetch and parse metadata
        soft_text = self._fetch_soft(gse_id)
        gse_info = self.parser.parse_gse(soft_text, gse_id)
        info_series = self.parser.parse_gse_to_series(soft_text)

        # Build result
        result: dict[str, Any] = {
            "gse_id": gse_id,
            "info": info_series,
            "gsms": {},
            "failed_gsms": [],
        }

        if shell_only:
            # Fetch GSM shells only
            result["gsms"] = self._fetch_gsm_shells(gse_info.gsm_ids, n_threads)
            return result

        if mode == FetchMode.PER_GSM:
            gsms, failed = self._fetch_per_gsm(gse_info.gsm_ids, n_threads)
            result["gsms"] = gsms
            result["failed_gsms"] = failed

        elif mode == FetchMode.SUPPLEMENTARY:
            gsms = self._fetch_from_supplementary(
                gse_id=gse_id,
                gse_info=gse_info,
                file_index=file_index,
                file_selector=file_selector,
            )
            result["gsms"] = gsms

        return result

    def _fetch_gsm_shells(
        self,
        gsm_ids: list[str],
        n_threads: int,
    ) -> dict[str, dict[str, Any]]:
        """Fetch GSM metadata only (shells) for all samples.

        Args:
            gsm_ids: List of GSM IDs
            n_threads: Number of threads

        Returns:
            Dict mapping GSM ID to GSM data
        """
        from tqdm.auto import tqdm

        result: dict[str, dict[str, Any]] = {}

        if n_threads <= 1:
            for gsm_id in tqdm(gsm_ids, desc="Fetching GSM info", leave=False):
                try:
                    data = self.gsm_service.fetch(gsm_id, shell_only=True)
                    result[gsm_id] = data
                except Exception:
                    pass
        else:
            with ThreadPoolExecutor(max_workers=n_threads) as executor:
                futures = {
                    executor.submit(self.gsm_service.fetch, gsm_id, True): gsm_id
                    for gsm_id in gsm_ids
                }
                for future in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc="Fetching GSM info",
                    leave=False,
                ):
                    gsm_id = futures[future]
                    try:
                        data = future.result()
                        result[gsm_id] = data
                    except Exception:
                        pass

        return result

    def _fetch_per_gsm(
        self,
        gsm_ids: list[str],
        n_threads: int,
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Fetch full GSM data for all samples.

        Args:
            gsm_ids: List of GSM IDs
            n_threads: Number of threads

        Returns:
            Tuple of (gsms dict, failed_gsms list)
        """
        from tqdm.auto import tqdm

        result: dict[str, dict[str, Any]] = {}
        failed: list[str] = []

        if n_threads <= 1:
            for gsm_id in tqdm(gsm_ids, desc="Fetching GSM data", leave=False):
                try:
                    data = self.gsm_service.fetch(gsm_id, shell_only=False)
                    result[gsm_id] = data
                except NoDataAvailableError:
                    failed.append(gsm_id)
                except Exception as e:
                    logger.error("Failed to fetch %s: %s", gsm_id, e)
                    failed.append(gsm_id)
        else:
            with ThreadPoolExecutor(max_workers=n_threads) as executor:
                futures = {
                    executor.submit(self.gsm_service.fetch, gsm_id, False): gsm_id
                    for gsm_id in gsm_ids
                }
                for future in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc="Fetching GSM data",
                    leave=False,
                ):
                    gsm_id = futures[future]
                    try:
                        data = future.result()
                        result[gsm_id] = data
                    except NoDataAvailableError:
                        failed.append(gsm_id)
                    except Exception as e:
                        print(f"Error fetching {gsm_id}: {e}")
                        failed.append(gsm_id)

        return result, failed

    def _fetch_from_supplementary(
        self,
        gse_id: str,
        gse_info: GSEInfo,
        file_index: int | None,
        file_selector: FileSelector | None,
    ) -> dict[str, dict[str, Any]]:
        """Fetch GSM data from supplementary files.

        Args:
            gse_id: GSE accession ID
            gse_info: Parsed GSE info
            file_index: File index to use
            file_selector: Custom file selector

        Returns:
            Dict mapping GSM ID to GSM data
        """
        from PyNCBI._types import IndexFileSelector, InteractiveFileSelector

        supp_files = gse_info.supplementary_files
        if not supp_files:
            raise NoDataAvailableError(gse_id)

        # Select file
        if file_index is not None:
            selector = IndexFileSelector(file_index)
        elif file_selector is not None:
            selector = file_selector
        else:
            # Default to interactive if multiple files, first if single
            if len(supp_files) == 1:
                selector = IndexFileSelector(0)
            else:
                selector = InteractiveFileSelector()

        selected_index = selector.select(supp_files, gse_id)
        selected_url = supp_files[selected_index]

        # Download and parse the supplementary file
        return self._parse_supplementary_file(
            gse_id=gse_id,
            file_url=selected_url,
            gsm_ids=gse_info.gsm_ids,
        )

    def _parse_supplementary_file(
        self,
        gse_id: str,
        file_url: str,
        gsm_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Download and parse a supplementary file.

        Args:
            gse_id: GSE accession ID
            file_url: URL of supplementary file
            gsm_ids: List of GSM IDs in the series

        Returns:
            Dict mapping GSM ID to GSM data
        """
        import gzip
        from io import StringIO

        # Download file
        filename = file_url.split("/")[-1].lower()

        if filename.endswith(".tar") or ".tar" in filename:
            # Handle tar files (IDAT)
            return self._parse_tar_supplementary(gse_id, file_url, gsm_ids)

        # Download as text/csv
        content = self.http_client.get_bytes(file_url)

        # Decompress if needed
        if filename.endswith(".gz"):
            content = gzip.decompress(content)

        # Parse as CSV/TSV
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")

        # Detect separator
        if filename.endswith(".csv") or filename.endswith(".csv.gz"):
            df = pd.read_csv(StringIO(text))
        else:
            df = pd.read_table(StringIO(text))

        # Match columns to GSM IDs
        return self._match_data_to_gsms(df, gsm_ids)

    def _parse_tar_supplementary(
        self,
        gse_id: str,
        file_url: str,
        gsm_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Parse a tar supplementary file (typically IDAT files).

        Args:
            gse_id: GSE accession ID
            file_url: URL of tar file
            gsm_ids: List of GSM IDs

        Returns:
            Dict mapping GSM ID to GSM data
        """
        import tempfile

        from PyNCBI.FileUtilities import parse_idat_files
        from PyNCBI.io.compression import extract_tarfile, remove_non_idat_files

        # Create temp directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Download tar file
            tar_path = temp_path / "data.tar"
            self.http_client.download_file(file_url, tar_path)

            # Extract
            extract_folder = temp_path / "extracted"
            extract_tarfile(tar_path, extract_folder)

            # Remove non-IDAT files
            remove_non_idat_files(extract_folder)

            # Parse IDAT files
            # Detect array type from first IDAT file
            array_type = "450k"  # Default
            for file in extract_folder.iterdir():
                if "epic" in file.name.lower():
                    array_type = "epic"
                    break

            parse_idat_files(str(extract_folder) + "/", array_type)

            # Load results
            result_file = extract_folder / "parsed_beta_values.parquet"
            if result_file.exists():
                df = pd.read_parquet(result_file)
                return self._match_data_to_gsms(df, gsm_ids)

        return {}

    def _match_data_to_gsms(
        self,
        df: pd.DataFrame,
        gsm_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Match DataFrame columns to GSM IDs.

        Args:
            df: DataFrame with methylation data
            gsm_ids: List of GSM IDs

        Returns:
            Dict mapping GSM ID to GSM data
        """
        result: dict[str, dict[str, Any]] = {}

        # Find probe column
        probe_col = None
        for col in ["probe", "ID_REF", "Probe_ID", "IlmnID"]:
            if col in df.columns:
                probe_col = col
                break

        if probe_col is None and df.columns[0] not in gsm_ids:
            probe_col = df.columns[0]

        # Match GSM columns
        for gsm_id in gsm_ids:
            # Try exact match first
            if gsm_id in df.columns:
                data = df[[probe_col, gsm_id]] if probe_col else df[[gsm_id]]
                result[gsm_id] = {
                    "gsm_id": gsm_id,
                    "data": data,
                    "info": pd.Series({"gsm_id": gsm_id}),
                    "characteristics": pd.Series(),
                    "array_type": "",
                    "gse": "",
                }
                continue

            # Try substring match
            for col in df.columns:
                if gsm_id in col:
                    data = df[[probe_col, col]] if probe_col else df[[col]]
                    data = data.rename(columns={col: gsm_id})
                    result[gsm_id] = {
                        "gsm_id": gsm_id,
                        "data": data,
                        "info": pd.Series({"gsm_id": gsm_id}),
                        "characteristics": pd.Series(),
                        "array_type": "",
                        "gse": "",
                    }
                    break

        return result
