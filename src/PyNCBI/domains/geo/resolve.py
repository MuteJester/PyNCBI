"""Resolving what to fetch for a study: cohort file sets and genome assemblies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import NoDataAvailableError
from PyNCBI.core.files import file_name, matches_file
from PyNCBI.core.tables import by_id, iter_records
from PyNCBI.core.types import validate_accession
from PyNCBI.domains.geo.samples import (
    _apply_sample_filter,
    combine_sample_tables,
    fetch_sample_tables,
)
from PyNCBI.domains.geo.urls import resolve_rnaseq_assembly
from PyNCBI.io.http_client import (
    get_default_client,
)
from PyNCBI.io.urls import supplementary_urls
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient


@dataclass
class CohortFile:
    """A per-sample supplementary file resolved from a cohort filter."""

    gsm_id: str
    url: str
    file_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_cohort_files(
    gse_id: str,
    *,
    where: str | None = None,
    pattern: str | None = None,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> list[CohortFile]:
    """Resolve per-sample supplementary files for a (filtered) GSE cohort in ONE request.

    The GEO batch sample view already lists every `!Sample_supplementary_file`, so the
    cohort filter (`where`) and filename `pattern` are applied without any per-GSM requests.

    Returns:
        List of `CohortFile(gsm_id, url, file_name)` in GSM order.
    """
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()

    info_df, char_df = fetch_sample_tables(gse_id, http_client=client, parser=soft_parser)
    if not info_df["rows"]:
        raise NoDataAvailableError(gse_id)

    if where:
        matched = _apply_sample_filter(combine_sample_tables(info_df, char_df), where)
        gsm_ids = [str(record["gsm_id"]) for record in iter_records(matched)]
        if not gsm_ids:
            raise NoDataAvailableError(
                gse_id,
                detail=f"no samples matched filter '{where}' (0 of {len(info_df['rows'])})",
            )
    else:
        gsm_ids = [str(record["gsm_id"]) for record in iter_records(info_df)]

    resolved: list[CohortFile] = []
    lookup = by_id(info_df, "gsm_id")
    supp_cols = [c for c in info_df["columns"] if str(c).startswith("supplementary_file")]
    for gsm in gsm_ids:
        for col in supp_cols:
            for url in supplementary_urls(lookup[gsm].get(col)):
                fname = file_name(url)
                if matches_file(url, pattern):
                    resolved.append(CohortFile(gsm_id=gsm, url=url, file_name=fname))
    return resolved


def _resolve_assembly_for_gse(
    gse_id: str, assembly: str | None, client: HttpClient, soft_parser: SOFTParser
) -> str:
    """Return an explicit assembly or resolve it from the series organism (1 request)."""
    if assembly and assembly.lower() != "auto":
        return assembly
    cfg = get_config()
    gse_info = soft_parser.parse_gse(client.get_text(cfg.get_ncbi_query_url(gse_id)), gse_id)
    resolved = resolve_rnaseq_assembly(gse_info)
    if resolved is None:
        raise NoDataAvailableError(
            gse_id,
        )
    return resolved
