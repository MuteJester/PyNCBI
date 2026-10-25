"""Download planning and the safety rails that run before any transfer starts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import DataError
from PyNCBI.core.files import matches_file
from PyNCBI.core.types import validate_accession
from PyNCBI.domains.geo.resolve import _resolve_assembly_for_gse, resolve_cohort_files
from PyNCBI.domains.geo.transfer import _free_bytes, _inspect_urls_parallel
from PyNCBI.domains.geo.urls import rnaseq_counts_url
from PyNCBI.io.http_client import (
    format_size_human,
    get_default_client,
)
from PyNCBI.io.transfer_limits import bounded_transfer, response_budget
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient


@dataclass
class DownloadPlan:
    """What a download *would* fetch: the files, their sizes, and whether they fit on disk."""

    accession: str
    destination: Path
    source: str
    files: list[dict[str, Any]] = field(default_factory=list)
    total_bytes: int = 0
    free_bytes: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_human(self) -> str:
        return format_size_human(self.total_bytes)

    @property
    def fits_on_disk(self) -> bool:
        return True if self.free_bytes is None else self.total_bytes <= self.free_bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "accession": self.accession,
            "destination": str(self.destination),
            "source": self.source,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "total_human": self.total_human,
            "free_bytes": self.free_bytes,
            "free_human": format_size_human(self.free_bytes)
            if self.free_bytes is not None
            else "unknown",
            "fits_on_disk": self.fits_on_disk,
            "files": [dict(f) for f in self.files],
            "notes": list(self.notes),
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Download plan: {self.accession}",
            "",
            f"Would download **{self.file_count} file(s)**, **{self.total_human}** total, into `{self.destination}`"
            + (
                f" ({format_size_human(self.free_bytes)} free)"
                if self.free_bytes is not None
                else ""
            ),
            "",
            "| # | File | Size | Source |",
            "| :-- | :-- | :-- | :-- |",
        ]
        for idx, f in enumerate(self.files):
            lines.append(
                f"| {idx} | `{f.get('name')}` | {f.get('size_human', 'unknown')} | {f.get('source', self.source)} |"
            )
        if not self.fits_on_disk:
            lines.append("")
            lines.append(
                "> **Warning:** this exceeds the free space on the destination filesystem."
            )
        for note in self.notes:
            lines.append(f"- {note}")
        return "\n".join(lines)

    def to_text(self) -> str:
        return self.to_markdown().replace("`", "").replace("**", "").replace("# ", "")


def _select_supp_urls(
    supp_urls: list[str],
    accession: str,
    file_index: int | None,
    pattern: str | None,
) -> list[str]:
    """Apply `file_index` / `pattern` selection to a list of supplementary file URLs."""
    if file_index is not None:
        if file_index < 0 or file_index >= len(supp_urls):
            raise ValueError(
                f"file_index {file_index} out of range (0..{len(supp_urls) - 1}) for {accession}"
            )
        return [supp_urls[file_index]]
    if pattern:
        selected = [u for u in supp_urls if matches_file(u, pattern)]
        if not selected:
            raise ValueError(f"No supplementary files matched pattern '{pattern}' in {accession}")
        return selected
    return list(supp_urls)


def build_download_plan(
    client: HttpClient,
    accession: str,
    urls: list[str],
    destination: Path,
    *,
    source: str = "supplementary",
) -> DownloadPlan:
    """HEAD every URL and report the resulting download plan (no bytes are transferred)."""
    infos = _inspect_urls_parallel(client, [(u, source) for u in urls], check_sizes=True)
    files = [i.to_dict() for i in infos]
    total = sum(int(f["size_bytes"]) for f in files if f.get("size_bytes"))
    notes: list[str] = []
    unknown = [f["name"] for f in files if not f.get("size_bytes")]
    if unknown:
        notes.append(f"{len(unknown)} file(s) report no size: {', '.join(unknown[:5])}")
    return DownloadPlan(
        accession=accession,
        destination=Path(destination),
        source=source,
        files=files,
        total_bytes=total,
        free_bytes=_free_bytes(Path(destination)),
        notes=notes,
    )


@bounded_transfer
def _guard_download(
    client: HttpClient,
    accession: str,
    urls: list[str],
    destination: Path,
    *,
    source: str = "supplementary",
    max_download_mb: float | None,
    check_disk_space: bool,
) -> DownloadPlan | None:
    """Refuse a download that blows the byte budget or would not fit on disk."""
    budget = response_budget(getattr(client, "app_config", None) or get_config())
    if not urls or (not budget.check_disk_space and budget.limit is None):
        return None
    plan = build_download_plan(client, accession, urls, destination, source=source)
    budget.check(plan.total_bytes)
    if budget.check_disk_space and not plan.fits_on_disk:
        raise DataError(
            f"Insufficient disk space for {accession}: need {plan.total_human}, "
            f"{format_size_human(plan.free_bytes)} free at {plan.destination}",
            hint="Free space, choose another -o destination, or pass check_disk_space=False",
        )
    return plan


def plan_geo_download(
    accession: str,
    output_dir: str | Path | None = None,
    *,
    source: str = "auto",
    file_index: int | None = None,
    pattern: str | None = None,
    where: str | None = None,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> DownloadPlan:
    """Report exactly what `download_geo_files` would fetch, without downloading anything.

    Args:
        accession: GSE, GSM, GPL, or GDS accession
        output_dir: Destination the plan is sized against (default `./{accession}`)
        source: Same values as `download_geo_files`
        file_index: 0-based supplementary file index
        pattern: Filename glob filter
        where: For a GSE, a cohort filter selecting per-sample files
        http_client: Optional HTTP client
        parser: Optional SOFT parser

    Returns:
        `DownloadPlan` with the file list, byte total, free space, and `fits_on_disk`.
    """
    accession = validate_accession(accession)
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    cfg = get_config()
    destination = Path(output_dir) if output_dir else Path.cwd() / accession
    norm_source = source.lower().strip()

    if where:
        cohort = resolve_cohort_files(
            accession, where=where, pattern=pattern, http_client=client, parser=soft_parser
        )
        return build_download_plan(
            client, accession, [c.url for c in cohort], destination, source="supplementary"
        )

    if norm_source in ("matrix", "series_matrix"):
        return build_download_plan(
            client,
            accession,
            [cfg.get_series_matrix_url(accession)],
            destination,
            source="series_matrix",
        )

    if norm_source in ("rnaseq", "rnaseq_counts", "rnaseq-counts"):
        assembly = _resolve_assembly_for_gse(accession, "auto", client, soft_parser)
        return build_download_plan(
            client,
            accession,
            [rnaseq_counts_url(accession, assembly=assembly)],
            destination,
            source="ncbi_rnaseq_counts",
        )

    if accession.startswith("GDS"):
        return build_download_plan(
            client, accession, [cfg.get_gds_soft_url(accession)], destination, source="gds_soft"
        )

    soft_text = client.get_text(cfg.get_ncbi_query_url(accession))
    if accession.startswith("GSE"):
        supp_urls = soft_parser.parse_gse(soft_text, accession).supplementary_files
    elif accession.startswith("GSM"):
        supp_urls = soft_parser.parse_gsm(soft_text, accession).supplementary_files
    else:
        raw_s = soft_parser.parse_gpl_to_series(soft_text).get("supplementary_file", [])
        supp_urls = [raw_s] if isinstance(raw_s, str) else list(raw_s)
    if not supp_urls:
        return DownloadPlan(
            accession=accession,
            destination=destination,
            source="supplementary",
            free_bytes=_free_bytes(destination),
            notes=["No supplementary files are attached to this record"],
        )
    selected = _select_supp_urls(supp_urls, accession, file_index, pattern)
    return build_download_plan(client, accession, selected, destination, source="supplementary")
