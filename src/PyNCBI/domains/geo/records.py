"""Typed GEO state and metadata acquisition, independent of public Python objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

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
    info: pd.Series
    characteristics: pd.Series
    platform_id: str = ""
    gse_id: str = ""
    supplementary_files: list[str] = field(default_factory=list)
    data: pd.DataFrame | pd.Series | None = None
    source: str = "metadata"
    source_url: str = ""

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
    info: pd.Series
    details: GSEInfo
    samples_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    characteristics_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    samples: dict[str, SampleState] = field(default_factory=dict)
    failures: list[SampleFailure] = field(default_factory=list)
    matrix: pd.DataFrame | None = None
    source: str = "metadata"

    @property
    def failed_samples(self) -> list[str]:
        return list(dict.fromkeys(f.gsm_id for f in self.failures))


# These are record-local failures. Authentication, rate limiting, transport errors,
# budgets and programming errors must propagate instead of becoming missing samples.
SAMPLE_FAILURES = (AccessionNotFoundError, AccessionPrivateError, NoDataAvailableError, ParseError)


def sample_from_parts(
    gsm_id: str,
    info: pd.Series,
    characteristics: pd.Series | None = None,
    *,
    data: pd.DataFrame | pd.Series | None = None,
) -> SampleState:
    """Build sample state without network or cache access (also used when hydrating)."""
    info = info.copy()
    info.name = gsm_id
    if characteristics is None:
        text = "\n".join(
            str(info.get(k, "")) for k in ("characteristics_ch1", "characteristics_ch2")
        )
        parsed = parse_characteristics([text])
        characteristics = parsed.iloc[0] if not parsed.empty else pd.Series(dtype=object)
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
    return sample_from_parts(gsm_id, soft_parser.parse_gsm_to_series(text))


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
        gse_id, soft_parser.parse_gse_to_series(text), soft_parser.parse_gse(text, gse_id)
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
        info, chars = pd.DataFrame(), pd.DataFrame()
    for gsm in info.index:
        state.samples[str(gsm)] = sample_from_parts(
            str(gsm),
            info.loc[gsm].dropna(),
            chars.loc[gsm].dropna() if gsm in chars.index else pd.Series(dtype=object),
        )
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
        state.samples_df = combine_sample_tables(info, chars)
        state.characteristics_df = chars
    else:
        raw = pd.DataFrame.from_dict(
            {g: s.info.to_dict() for g, s in state.samples.items()}, orient="index"
        )
        chars = pd.DataFrame.from_dict(
            {g: s.characteristics.to_dict() for g, s in state.samples.items()}, orient="index"
        )
        raw.index.name = chars.index.name = "gsm_id"
        state.samples_df = combine_sample_tables(raw, chars)
        state.characteristics_df = chars
    return state
