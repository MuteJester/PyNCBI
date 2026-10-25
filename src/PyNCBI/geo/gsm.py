"""Public GSM convenience object over the shared GEO loading engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.formatter import render_output, save_data
from PyNCBI.core.tables import Table, is_table
from PyNCBI.core.types import DataStatus, validate_accession
from PyNCBI.domains.geo import download_geo_files, inspect_accession
from PyNCBI.domains.geo.records import (
    SampleState,
    fetch_sample_record,
    sample_data,
    sample_from_parts,
)
from PyNCBI.domains.geo.tables import load_sample_record
from PyNCBI.geo._cache import EntityCache
from PyNCBI.geo._formatting import sample_summary
from PyNCBI.io.cache import LocalCache
from PyNCBI.io.http_client import get_default_client
from PyNCBI.io.transfer_limits import transfer_scope
from PyNCBI.parsing.soft_parser import get_parser

if TYPE_CHECKING:
    from pathlib import Path

    from PyNCBI.core.models import InspectionReport
    from PyNCBI.core.protocols import Cache, HttpClient


class GSM:
    """A sample's metadata and data, loaded through the common retrieval functions."""

    def __init__(
        self,
        gsm_id: str,
        shell_only: bool = False,
        overwrite_cache: bool = False,
        *,
        _http_client: HttpClient | None = None,
        _cache: Cache[dict[str, Any]] | None = None,
    ) -> None:
        gsm_id = validate_accession(gsm_id, "GSM")
        self._http_client = _http_client if _http_client is not None else get_default_client()
        self._cache = _cache if _cache is not None else LocalCache()
        self._parser = get_parser()
        self._storage = EntityCache(self._cache, gsm_id, {"source": "auto"}, shell_only)
        cached = None if overwrite_cache else self._storage.get()
        if isinstance(cached, SampleState):
            self._state = cached
        else:
            config = getattr(self._http_client, "app_config", None) or get_config()
            with transfer_scope(config.max_download_mb):
                self._state = fetch_sample_record(
                    gsm_id, http_client=self._http_client, parser=self._parser
                )
                if not shell_only:
                    load_sample_record(
                        self._state, http_client=self._http_client, parser=self._parser
                    )
            self.store_cache()

    # --- Public Properties ---

    @property
    def gsm_id(self) -> str:
        """The GSM identifier (e.g., 'GSM1518180')."""
        return self._state.gsm_id

    @gsm_id.setter
    def gsm_id(self, value: str) -> None:
        if validate_accession(value, "GSM") != self.gsm_id:
            raise ValueError("An accession identifies its loaded data; construct a new GSM instead")

    @property
    def array_type(self) -> str:
        """The platform GPL ID (e.g., 'GPL13534')."""
        return self._state.platform_id

    @array_type.setter
    def array_type(self, value: str) -> None:
        self._state.platform_id = value

    @property
    def platform_id(self) -> str:
        """Alias for array_type (GPL ID)."""
        return self._state.platform_id

    @property
    def gse(self) -> str:
        """The parent GSE series ID."""
        return self._state.gse_id

    @gse.setter
    def gse(self, value: str) -> None:
        self._state.gse_id = value

    @property
    def info(self) -> dict[str, Any]:
        """All sample metadata fields as a dictionary."""
        return self._state.info if self._state.info is not None else {}

    @info.setter
    def info(self, value: dict[str, Any]) -> None:
        self._state.info = value

    @property
    def data(self) -> Table | None:
        """On-page or tabular sample data as a native table (or None if shell_only=True)."""
        return sample_data(self._state)

    @data.setter
    def data(self, value: Table | None) -> None:
        if value is not None and not is_table(value):
            raise TypeError("data must be a native columns/rows dictionary or None")
        self._state.data = value
        self._state._matrix = None
        self._state.matrix_column = ""
        self._state.source = "provided" if self._state.data is not None else "metadata"
        self._state.source_url = ""

    @property
    def characteristics(self) -> dict[str, Any]:
        """Parsed sample characteristics (``key: value``) as a dictionary."""
        return self._state.characteristics if self._state.characteristics is not None else {}

    @characteristics.setter
    def characteristics(self, value: dict[str, Any]) -> None:
        self._state.characteristics = value

    @property
    def supplementary_files(self) -> list[str]:
        """List of supplementary file HTTPS URLs attached to this GSM."""
        return list(self._state.supplementary_files)

    @property
    def has_data(self) -> bool:
        """True if tabular data was loaded into `.data`."""
        return self._state.data is not None or self._state._matrix is not None

    @property
    def title(self) -> str:
        """Sample title."""
        return str(self._state.info.get("title", "")) if self._state.info is not None else ""

    @property
    def data_status(self) -> DataStatus:
        """Check data availability status from NCBI."""
        return self._state.status

    # --- Inspection, Download & Formatting Methods ---

    def inspect(self, check_file_sizes: bool = True) -> InspectionReport:
        """Return a comprehensive InspectionReport for this GSM (including file sizes)."""
        return inspect_accession(
            self._state.gsm_id,
            check_file_sizes=check_file_sizes,
            http_client=self._http_client,
            parser=self._parser,
        )

    def download_supplementary(
        self,
        output_dir: str | Path | None = None,
        *,
        file_index: int | None = None,
        pattern: str | None = None,
        unpack: bool = False,
    ) -> list[Path]:
        """Download raw supplementary files for this GSM directly from NCBI."""
        return download_geo_files(
            self._state.gsm_id,
            output_dir=output_dir,
            source="supp",
            file_index=file_index,
            pattern=pattern,
            unpack=unpack,
            http_client=self._http_client,
            parser=self._parser,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize GSM metadata and data summary to a clean dictionary."""
        return sample_summary(self._state)

    def to_markdown(self) -> str:
        """Render a clean Markdown summary of this GSM."""
        return render_output(
            self.to_dict(),
            mode="markdown",
            command="gsm",
            target=self._state.gsm_id,
            summary=f"{self._state.gsm_id}: {self.title} (GSE: {self._state.gse_id}, Platform: {self._state.platform_id})",
        )

    def save(self, output_path: str | Path, section: str = "data") -> Path:
        """Save GSM data or metadata to a file (.csv, .tsv, .json)."""
        if section == "info":
            return save_data(self.info, output_path)
        if section == "characteristics":
            return save_data(self.characteristics, output_path)
        data = self.data
        if data is not None:
            return save_data(data, output_path)
        return save_data(self.to_dict(), output_path)

    # Cache operations describe this request, not another source's measurements.
    def is_cached(self) -> bool:
        """Whether this requested sample state has a usable cache entry."""
        return self._storage.exists()

    def store_cache(self) -> None:
        """Persist the current sample state and its source identity."""
        self._storage.save(self._state)

    def load_cache(self) -> None:
        """Restore this request's state when a valid entry exists."""
        cached = self._storage.get()
        if isinstance(cached, SampleState):
            self._state = cached

    def clear_cache(self) -> bool:
        """Remove sample metadata and all discoverable data variants."""
        return self._storage.clear()

    @classmethod
    def _from_state(
        cls, state: SampleState, http_client: HttpClient, cache: Cache[dict[str, Any]]
    ) -> GSM:
        instance = cls.__new__(cls)
        instance._state = state
        instance._http_client, instance._cache = http_client, cache
        instance._parser = get_parser()
        instance._storage = EntityCache(
            cache,
            state.gsm_id,
            {"origin": "series", "source": state.source, "url": state.source_url},
            state.data is None,
        )
        return instance

    @classmethod
    def _from_parsed_parts(
        cls,
        gsm_id: str,
        info: dict[str, Any],
        characteristics: dict[str, Any],
        *,
        data: Table | None = None,
        http_client: HttpClient | None = None,
        cache: Cache[dict[str, Any]] | None = None,
    ) -> GSM:
        return cls._from_state(
            sample_from_parts(gsm_id, info, characteristics, data=data),
            http_client if http_client is not None else get_default_client(),
            cache if cache is not None else LocalCache(),
        )

    def __repr__(self) -> str:
        return f"GSM({self.gsm_id!r}, has_data={self.has_data})"

    def __str__(self) -> str:
        lines = [f"GSM: {self.gsm_id} | GSE: {self.gse}"]
        lines.extend(f"  {key}: {value}" for key, value in self.characteristics.items())
        return "\n".join(lines)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, GSM):
            return self._state.gsm_id == other._state.gsm_id
        return False

    def __hash__(self) -> int:
        return hash(self._state.gsm_id)

    def __getitem__(self, probe_id: str) -> int | float | None:
        """Get an exact native measurement by its explicit feature identifier."""
        data = self._state._matrix or self._state.data
        if data is not None and self.gsm_id in data["columns"]:
            index = data["columns"].index(self.gsm_id)
            for row in data["rows"]:
                if row[0] == probe_id:
                    value = row[index]
                    if value is None or isinstance(value, (int, float)):
                        return value
                    raise TypeError(f"Feature {probe_id!r} has non-numeric data")
        raise KeyError(f"Feature {probe_id!r} not found in {self.gsm_id}")

    def __contains__(self, probe_id: str) -> bool:
        data = self._state._matrix or self._state.data
        return (
            data is not None
            and bool(data["columns"])
            and data["columns"][0] == "feature_id"
            and any(row[0] == probe_id for row in data["rows"])
        )
