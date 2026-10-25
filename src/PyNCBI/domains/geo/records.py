"""Typed GEO state and metadata acquisition, independent of public Python objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import (
    AccessionNotFoundError,
    AccessionPrivateError,
    NoDataAvailableError,
    ParseError,
    PyNCBIError,
    error_code_for,
    is_retryable,
)
from PyNCBI.core.tables import Table, by_id, from_records, iter_records, table
from PyNCBI.core.types import DataStatus, GSEInfo, validate_accession
from PyNCBI.domains.geo.samples import combine_sample_tables, fetch_sample_tables
from PyNCBI.io.http_client import get_default_client
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor
from PyNCBI.io.urls import supplementary_urls
from PyNCBI.parsing.soft_parser import get_parser, parse_characteristics

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient
    from PyNCBI.parsing.soft_parser import SOFTParser


@dataclass
class SampleState:
    gsm_id: str
    info: dict[str, Any]
    characteristics: dict[str, Any]
    platform_id: str = ""
    gse_id: str = ""
    supplementary_files: list[str] = field(default_factory=list)
    data: Table | None = None
    source: str = "metadata"
    source_url: str = ""
    matrix_column: str = ""
    _matrix: Table | None = field(default=None, repr=False, compare=False)

    @property
    def status(self) -> DataStatus:
        rows = str(self.info.get("data_row_count", "0")).split("\n")[0].strip()
        if rows.isdigit() and int(rows) > 0:
            return DataStatus.DATA_ON_PAGE
        return DataStatus.SUPPLEMENTARY_FILES if self.supplementary_files else DataStatus.NO_DATA


@dataclass
class SampleFailure:
    gsm_id: str
    error_code: str
    retryable: bool
    message: str
    stage: str = "data"

    @classmethod
    def from_error(cls, gsm_id: str, exc: PyNCBIError, stage: str) -> SampleFailure:
        return cls(gsm_id, error_code_for(exc), is_retryable(exc), str(exc), stage)


@dataclass
class SeriesState:
    gse_id: str
    info: dict[str, Any]
    details: GSEInfo
    sample_table: Table = field(default_factory=table)
    characteristics_table: Table = field(default_factory=table)
    samples: dict[str, SampleState] = field(default_factory=dict)
    failures: list[SampleFailure] = field(default_factory=list)
    matrix: Table | None = None
    source: str = "metadata"

    @property
    def failed_samples(self) -> list[str]:
        return list(dict.fromkeys(f.gsm_id for f in self.failures))


# These are record-local failures. Authentication, rate limiting, transport errors,
# budgets and programming errors must propagate instead of becoming missing samples.
SAMPLE_FAILURES = (AccessionNotFoundError, AccessionPrivateError, NoDataAvailableError, ParseError)


def sample_from_parts(
    gsm_id: str,
    info: dict[str, Any],
    characteristics: dict[str, Any] | None = None,
    *,
    data: Table | None = None,
) -> SampleState:
    """Build sample state without network or cache access (also used when hydrating)."""
    info = info.copy()
    if characteristics is None:
        text = "\n".join(
            str(info.get(k, "")) for k in ("characteristics_ch1", "characteristics_ch2")
        )
        parsed = parse_characteristics([text])
        characteristics = next(iter_records(parsed), {})
        characteristics.pop("gsm_id", None)
    characteristics = {key: value for key, value in characteristics.items() if key != "gsm_id"}
    return SampleState(
        gsm_id=gsm_id,
        info=info,
        characteristics=characteristics,
        platform_id=str(info.get("platform_id", "")).split("\n")[0].strip(),
        gse_id=str(info.get("series_id", "")).split("\n")[0].strip(),
        supplementary_files=supplementary_urls(info.get("supplementary_file")),
        data=data,
    )


def fetch_sample_record(
    gsm_id: str, *, http_client: HttpClient | None = None, parser: SOFTParser | None = None
) -> SampleState:
    gsm_id = validate_accession(gsm_id, "GSM")
    client = http_client if http_client is not None else get_default_client()
    soft_parser = parser or get_parser()
    text = client.get_text(get_config().get_ncbi_query_url(gsm_id))
    return sample_from_parts(gsm_id, soft_parser.parse_gsm_record(text))


def fetch_series_metadata(
    gse_id: str,
    *,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> SeriesState:
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client if http_client is not None else get_default_client()
    soft_parser = parser or get_parser()
    text = client.get_text(get_config().get_ncbi_query_url(gse_id))
    return SeriesState(
        gse_id, soft_parser.parse_gse_record(text), soft_parser.parse_gse(text, gse_id)
    )


def fetch_series_record(
    gse_id: str,
    *,
    max_workers: int = 1,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> SeriesState:
    """Fetch series and batch metadata; fill missing batch rows without retry loops."""
    if max_workers < 1:
        raise ValueError("n_threads/max_workers must be at least 1")
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client if http_client is not None else get_default_client()
    soft_parser = parser or get_parser()
    state = fetch_series_metadata(gse_id, http_client=client, parser=soft_parser)
    try:
        info, chars = fetch_sample_tables(gse_id, http_client=client, parser=soft_parser)
    except (ParseError, NoDataAvailableError):
        info, chars = table(), table()
    characteristics_by_gsm = by_id(chars, "gsm_id") if chars["rows"] else {}
    for record in iter_records(info):
        gsm = str(record["gsm_id"])
        state.samples[gsm] = sample_from_parts(gsm, record, characteristics_by_gsm.get(gsm, {}))
    missing = [gsm for gsm in state.details.gsm_ids if gsm not in state.samples]

    def one(gsm: str) -> tuple[str, SampleState | SampleFailure]:
        try:
            return gsm, fetch_sample_record(gsm, http_client=client, parser=soft_parser)
        except SAMPLE_FAILURES as exc:
            return gsm, SampleFailure.from_error(gsm, exc, "metadata")

    if missing:
        with ContextThreadPoolExecutor(max_workers=min(max_workers, len(missing))) as pool:
            for gsm, result in pool.map(one, missing):
                if isinstance(result, SampleFailure):
                    state.failures.append(result)
                else:
                    state.samples[gsm] = result
    order = list(dict.fromkeys([*state.details.gsm_ids, *state.samples]))
    state.samples = {gsm: state.samples[gsm] for gsm in order if gsm in state.samples}
    if not missing:
        state.sample_table = combine_sample_tables(info, chars)
        state.characteristics_table = chars
    else:
        raw = from_records({"gsm_id": gsm, **sample.info} for gsm, sample in state.samples.items())
        chars = from_records(
            {"gsm_id": gsm, **sample.characteristics} for gsm, sample in state.samples.items()
        )
        state.sample_table = combine_sample_tables(raw, chars)
        state.characteristics_table = chars
    return state


def sample_data(sample: SampleState) -> Table | None:
    """Materialize an individual sample only when requested, from the shared matrix."""
    if sample._matrix is not None and sample.matrix_column:
        index = sample._matrix["columns"].index(sample.matrix_column)
        return table(
            ["feature_id", sample.gsm_id], ([row[0], row[index]] for row in sample._matrix["rows"])
        )
    return sample.data
