"""Public GSE mapping/convenience object over the shared GEO loading engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.formatter import render_output, save_data, summarize_characteristics_df
from PyNCBI.core.types import FetchMode, validate_accession
from PyNCBI.domains.geo import (
    download_geo_files,
    inspect_accession,
    load_rnaseq_counts,
    load_series_matrix,
)
from PyNCBI.domains.geo.records import SeriesState
from PyNCBI.domains.geo.studies import (
    SeriesRequest,
    assign_matrix,
    load_series_record,
    sample_matrix,
)
from PyNCBI.geo._cache import EntityCache
from PyNCBI.geo._formatting import series_summary
from PyNCBI.geo.gsm import GSM
from PyNCBI.io.cache import LocalCache
from PyNCBI.io.http_client import get_default_client
from PyNCBI.io.urls import supplementary_urls
from PyNCBI.parsing.soft_parser import get_parser

if TYPE_CHECKING:
    from collections.abc import ItemsView, Iterator, KeysView, ValuesView
    from pathlib import Path

    from PyNCBI.core.models import CharacteristicSummary, InspectionReport
    from PyNCBI.core.protocols import Cache, FileSelector, HttpClient
    from PyNCBI.geo.gsm import GSM as GSMType


class GSE:
    """A study and its samples. Retrieval policy belongs to the shared domain engine."""

    def __init__(
        self,
        gse_id: str,
        mode: str | FetchMode = FetchMode.AUTO,
        overwrite_cache: bool = False,
        n_threads: int = 1,
        remove_gsm_caches: bool = True,
        shell_only: bool = False,
        *,
        file_index: int | None = None,
        file_pattern: str | None = None,
        file_selector: FileSelector | None = None,
        _http_client: HttpClient | None = None,
        _cache: Cache[dict[str, Any]] | None = None,
    ) -> None:
        gse_id = validate_accession(gse_id, "GSE")
        if n_threads < 1:
            raise ValueError("n_threads must be at least 1")
        self._request = SeriesRequest.create(mode, file_index, file_pattern, file_selector)
        self._file_index, self._file_pattern = file_index, file_pattern
        self._http_client = _http_client if _http_client is not None else get_default_client()
        self._cache = _cache if _cache is not None else LocalCache()
        self._parser = get_parser()
        self._storage = EntityCache(self._cache, gse_id, self._request.identity(), shell_only)
        cached = None if overwrite_cache else self._storage.get()
        if isinstance(cached, SeriesState):
            self._adopt(cached)
        else:
            self._adopt(
                load_series_record(
                    gse_id,
                    request=self._request,
                    shell_only=shell_only,
                    max_workers=n_threads,
                    http_client=self._http_client,
                    parser=self._parser,
                )
            )
            self.store_cache()
            if remove_gsm_caches:
                self.remove_gsm_cache()

    # --- Public Properties ---

    @property
    def gse_id(self) -> str:
        """The GSE identifier (e.g., 'GSE85506')."""
        return self._state.gse_id

    @gse_id.setter
    def gse_id(self, value: str) -> None:
        if validate_accession(value, "GSE") != self.gse_id:
            raise ValueError("An accession identifies its loaded data; construct a new GSE instead")

    @property
    def gsms(self) -> dict[str, Any]:
        """Dictionary mapping GSM IDs to GSM objects."""
        return self._gsms

    @property
    def info(self) -> pd.Series:
        """Series metadata as a pandas Series."""
        return self._state.info if self._state.info is not None else pd.Series(dtype=object)

    @info.setter
    def info(self, value: pd.Series | None) -> None:
        self._state.info = value if value is not None else pd.Series(dtype=object)

    @property
    def samples_df(self) -> pd.DataFrame:
        """DataFrame of all samples in the GSE with metadata and unpacked characteristics."""
        if self._state.samples_df is not None and not self._state.samples_df.empty:
            return self._state.samples_df
        if self._gsms:
            all_info = [self._gsms[g].info for g in self._gsms]
            df = pd.DataFrame(pd.concat(all_info, axis=1).T)
            df.index.name = "gsm_id"
            return df
        return pd.DataFrame()

    @property
    def characteristics_df(self) -> pd.DataFrame:
        """DataFrame of unpacked ``key: value`` sample characteristics (rows = GSMs)."""
        if self._state.characteristics_df is not None and not self._state.characteristics_df.empty:
            return self._state.characteristics_df
        if self._gsms:
            rows = {g: self._gsms[g].characteristics.to_dict() for g in self._gsms}
            df = pd.DataFrame.from_dict(rows, orient="index")
            df.index.name = "gsm_id"
            return df
        return pd.DataFrame()

    @property
    def characteristics_summary(self) -> list[CharacteristicSummary]:
        """Structured breakdown of sample characteristics across the cohort."""
        return summarize_characteristics_df(self.characteristics_df)

    @property
    def supplementary_files(self) -> list[str]:
        """List of supplementary file HTTPS URLs attached to this GSE."""
        if self._state.info is None:
            return []
        return supplementary_urls(self._state.info.get("supplementary_file"))

    @property
    def failed_samples(self) -> list[str]:
        """List of GSM IDs that failed to fetch."""
        return self._state.failed_samples

    @property
    def title(self) -> str:
        """Series title."""
        return str(self._state.info.get("title", "")) if self._state.info is not None else ""

    @property
    def platform_id(self) -> str:
        """Primary platform GPL ID."""
        if self._state.info is None:
            return ""
        plat = self._state.info.get("platform_id", "")
        if isinstance(plat, list):
            return str(plat[0]) if plat else ""
        return str(plat)

    @property
    def sample_count(self) -> int:
        """Number of samples in this series."""
        return len(self._gsms) or len(self._state.details.gsm_ids)

    # --- Dict / Mapping Methods ---

    def keys(self) -> KeysView[str]:
        """Return view of GSM IDs in this series."""
        return self._gsms.keys()

    def values(self) -> ValuesView[Any]:
        """Return view of GSM objects in this series."""
        return self._gsms.values()

    def items(self) -> ItemsView[str, Any]:
        """Return `(gsm_id, gsm)` pairs in this series."""
        return self._gsms.items()

    def list_samples(self) -> list[str]:
        """Get list of all GSM IDs in this series."""
        return list(self._gsms.keys()) if self._gsms else list(self._state.details.gsm_ids)

    # --- Inspection, Download & Export Methods ---

    @classmethod
    def inspect(
        cls,
        gse_id: str,
        *,
        check_file_sizes: bool = True,
    ) -> InspectionReport:
        """Inspect series metadata without downloading large data files."""
        return inspect_accession(gse_id, check_file_sizes=check_file_sizes)

    def download_supplementary(
        self,
        output_dir: str | Path | None = None,
        *,
        file_index: int | None = None,
        pattern: str | None = None,
        unpack: bool = False,
    ) -> list[Path]:
        """Download raw supplementary files for this GSE directly from NCBI."""
        return download_geo_files(
            self._state.gse_id,
            output_dir=output_dir,
            source="supp",
            file_index=file_index if file_index is not None else self._file_index,
            pattern=pattern or self._file_pattern,
            unpack=unpack,
            http_client=self._http_client,
            parser=self._parser,
        )

    def load_series_matrix(self, save_to: str | Path | None = None) -> pd.DataFrame:
        """Download and parse the GEO Series Matrix table for this GSE."""
        res = load_series_matrix(
            self._state.gse_id,
            save_to=save_to,
            http_client=self._http_client,
        )
        return res.matrix_df

    def load_rnaseq_counts(
        self,
        count_type: str = "raw_counts",
        save_to: str | Path | None = None,
    ) -> pd.DataFrame:
        """Download and parse NCBI-generated RNA-seq counts for this GSE."""
        return load_rnaseq_counts(
            self._state.gse_id,
            count_type=count_type,
            save_to=save_to,
            http_client=self._http_client,
        )

    def to_dataframe(self, section: str = "data") -> pd.DataFrame:
        """Export GSE metadata, characteristics, or sample data matrix as a DataFrame."""
        sec = section.lower().strip()
        if sec == "info":
            all_info = [self._gsms[gsm].info for gsm in self._gsms]
            return pd.concat(all_info, axis=1) if all_info else pd.DataFrame()
        if sec == "characteristics":
            return self.characteristics_df
        if sec == "samples":
            return self.samples_df
        if sec == "data":
            self._sync_samples()
            return sample_matrix(self._state)

        raise ValueError("section must be 'info', 'characteristics', 'samples', or 'data'")

    def to_dict(self) -> dict[str, Any]:
        """Serialize GSE summary and sample characteristics breakdown to a dictionary."""
        self._sync_samples()
        return series_summary(self._state)

    def to_markdown(self) -> str:
        """Render a clean Markdown summary of this GSE."""
        return render_output(
            self.to_dict(),
            mode="markdown",
            command="gse",
            target=self._state.gse_id,
            summary=f"{self._state.gse_id}: {self.title} ({self.sample_count} samples, Platform: {self.platform_id})",
        )

    def save(self, output_path: str | Path, section: str = "samples") -> Path:
        """Save GSE table (`samples`, `characteristics`, `info`, or `data`) to disk."""
        df = self.to_dataframe(section=section)
        return save_data(df, output_path)

    def is_cached(self) -> bool:
        """Whether this source/file request has a valid cached snapshot."""
        return self._storage.exists()

    def store_cache(self) -> None:
        """Persist metadata separately from source/file-specific measurements."""
        self._sync_samples()
        self._storage.save(self._state)

    def load_cache(self) -> None:
        """Restore this request's snapshot without fetching."""
        cached = self._storage.get()
        if isinstance(cached, SeriesState):
            self._adopt(cached)

    def clear_cache(self) -> bool:
        """Remove metadata and all discoverable source/file variants for this study."""
        return self._storage.clear()

    def remove_gsm_cache(self) -> None:
        """Remove persistent caches of samples held by this series."""
        for gsm_id in self._gsms:
            EntityCache(self._cache, gsm_id, {}, True).clear()

    def _adopt(self, state: SeriesState) -> None:
        self._state = state
        self._gsms = {
            g: GSM._from_state(sample, self._http_client, self._cache)
            for g, sample in state.samples.items()
        }

    def _sync_samples(self) -> None:
        self._state.samples = {g: sample._state for g, sample in self._gsms.items()}

    def _match_columns_to_gsms(self, data: pd.DataFrame) -> None:
        self._sync_samples()
        assign_matrix(self._state, data, "provided")

    def __repr__(self) -> str:
        return f"GSE({self.gse_id!r}, samples={self.sample_count}, source={self._state.source!r})"

    def __str__(self) -> str:
        return f"GSE: {self.gse_id}\nPlatform: {self.platform_id}\nSamples: {self.sample_count}\nTitle: {self.title}"

    def __len__(self) -> int:
        return len(self._gsms)

    def __getitem__(self, gsm_id: str) -> GSMType:
        if gsm_id in self._gsms:
            gsm: GSMType = self._gsms[gsm_id]
            return gsm
        raise KeyError(f"{gsm_id} not found in {self._state.gse_id}")

    def __contains__(self, gsm_id: str) -> bool:
        return gsm_id in self._gsms

    def __iter__(self) -> Iterator[str]:
        return iter(self._gsms)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, GSE):
            return self._state.gse_id == other._state.gse_id
        return False

    def __hash__(self) -> int:
        return hash(self._state.gse_id)
