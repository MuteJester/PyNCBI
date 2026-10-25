"""Shared study loading policy: explicit sources stay explicit; AUTO uses the resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PyNCBI.core.exceptions import InvalidModeError, NoDataAvailableError
from PyNCBI.core.tables import Table, table
from PyNCBI.core.types import (
    FetchMode,
    IndexFileSelector,
    PatternFileSelector,
    SmartFileSelector,
    is_tabular_file,
)
from PyNCBI.domains.geo.matrices import join_sample_values, map_columns_to_gsms, sample_values
from PyNCBI.domains.geo.records import (
    SAMPLE_FAILURES,
    SampleFailure,
    SeriesState,
    fetch_series_record,
)
from PyNCBI.domains.geo.tables import (
    load_rnaseq_counts,
    load_sample_record,
    load_series_matrix,
    read_supplementary_table,
)
from PyNCBI.domains.geo.urls import resolve_rnaseq_assembly
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor, bounded_transfer

if TYPE_CHECKING:
    from PyNCBI.core.protocols import FileSelector, HttpClient
    from PyNCBI.parsing.soft_parser import SOFTParser


@dataclass(frozen=True)
class SeriesRequest:
    mode: FetchMode = FetchMode.AUTO
    file_index: int | None = None
    file_pattern: str | None = None
    file_selector: FileSelector | None = None

    @classmethod
    def create(
        cls,
        mode: str | FetchMode,
        file_index: int | None,
        file_pattern: str | None,
        file_selector: FileSelector | None,
    ) -> SeriesRequest:
        try:
            normalized = mode if isinstance(mode, FetchMode) else FetchMode.from_string(mode)
        except ValueError as exc:
            raise InvalidModeError(
                str(mode), ["auto", "per_gsm", "supp", "matrix", "rnaseq"]
            ) from exc
        return cls(normalized, file_index, file_pattern, file_selector)

    @property
    def selected_file(self) -> bool:
        return (
            self.file_index is not None
            or self.file_pattern is not None
            or self.file_selector is not None
        )

    def identity(self) -> dict[str, Any] | None:
        if self.file_selector is not None and self.file_index is None and self.file_pattern is None:
            # Arbitrary selectors can depend on mutable or external state. Do not guess
            # a cache identity from repr(), object address or arbitrary __dict__ values.
            return None
        return {
            "mode": self.mode.name,
            "file_index": self.file_index,
            "file_pattern": self.file_pattern,
        }


def assign_matrix(
    state: SeriesState, matrix: Table, source: str, source_url: str = ""
) -> list[str]:
    mapping, notes = map_columns_to_gsms(matrix["columns"][1:], state.sample_table)
    selected = [name for name in matrix["columns"][1:] if name in mapping]
    if mapping:
        names = ["feature_id", *[mapping[name] for name in selected]]
        if names != matrix["columns"]:
            positions = [0, *[matrix["columns"].index(name) for name in selected]]
            matrix = table(names, ([row[index] for index in positions] for row in matrix["rows"]))
        for gsm in names[1:]:
            if gsm not in state.samples:
                continue
            sample = state.samples[gsm]
            sample.data = None
            sample._matrix, sample.matrix_column = matrix, gsm
            sample.source, sample.source_url = source, source_url
    state.matrix, state.source = matrix, source
    return notes


def sample_matrix(state: SeriesState) -> Table:
    if state.matrix is not None and all(sample.data is None for sample in state.samples.values()):
        return state.matrix
    from PyNCBI.domains.geo.records import sample_data

    values = []
    for gsm, sample in state.samples.items():
        if sample.source == "files":
            continue
        data = sample_data(sample)
        if data is None:
            continue
        selected = sample_values(data, gsm)
        if selected is not None:
            values.append(selected)
    return join_sample_values(values) if values else state.matrix or table()


@bounded_transfer
def load_series_record(
    gse_id: str,
    *,
    request: SeriesRequest,
    shell_only: bool = False,
    max_workers: int = 1,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> SeriesState:
    state = fetch_series_record(
        gse_id, max_workers=max_workers, http_client=http_client, parser=parser
    )
    if shell_only:
        return state
    if request.mode == FetchMode.AUTO and not request.selected_file:
        from PyNCBI.domains.expression import _resolve_expression

        result = _resolve_expression(
            gse_id, metadata=state, max_workers=max_workers, http_client=http_client, parser=parser
        )
        assign_matrix(state, result.matrix, result.source, result.source_url)
        return state
    if request.mode == FetchMode.PER_GSM:

        def one(gsm: str) -> tuple[str, SampleFailure | None]:
            try:
                load_sample_record(state.samples[gsm], http_client=http_client, parser=parser)
                return gsm, None
            except SAMPLE_FAILURES as exc:
                return gsm, SampleFailure.from_error(gsm, exc, "data")

        if state.samples:
            with ContextThreadPoolExecutor(
                max_workers=max(1, min(max_workers, len(state.samples)))
            ) as pool:
                for _gsm, failure in pool.map(one, state.samples):
                    if failure is not None:
                        state.failures.append(failure)
        state.source = "per_gsm"
        return state
    if request.mode == FetchMode.SERIES_MATRIX:
        matrix_result = load_series_matrix(gse_id, http_client=http_client)
        matrix, source = matrix_result.matrix, "series_matrix"
    elif request.mode == FetchMode.RNASEQ_COUNTS:
        assembly = resolve_rnaseq_assembly(state.details)
        if assembly is None:
            raise NoDataAvailableError(
                gse_id, detail="no NCBI RNA-seq assembly for the study organism"
            )
        matrix = load_rnaseq_counts(
            gse_id, assembly=assembly, http_client=http_client, parser=parser
        )
        source = "ncbi_rnaseq_counts"
    else:
        urls = state.details.supplementary_files
        selector = (
            IndexFileSelector(request.file_index)
            if request.file_index is not None
            else PatternFileSelector(request.file_pattern)
            if request.file_pattern is not None
            else request.file_selector or SmartFileSelector()
        )
        if not urls:
            raise NoDataAvailableError(gse_id)
        url = urls[selector.select(urls, gse_id)]
        if not is_tabular_file(url):
            raise NoDataAvailableError(
                gse_id,
                detail="selected supplementary file is not a readable table; use download_supplementary() for raw files",
            )
        matrix = read_supplementary_table(url, http_client=http_client)
        assign_matrix(state, matrix, "supplementary_table", url)
        return state
    if not matrix["rows"]:
        raise NoDataAvailableError(gse_id, detail=f"{source} has no measurements")
    assign_matrix(state, matrix, source)
    return state
