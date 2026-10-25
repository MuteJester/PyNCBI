"""Accession inspection: study design, cohort summary, and remote file sizes."""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING

from PyNCBI.core.config import get_config
from PyNCBI.core.models import (
    CharacteristicSummary,
    InspectionReport,
    NCBIFileInfo,
)
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.types import validate_accession
from PyNCBI.domains.geo.samples import extract_gse_samples
from PyNCBI.domains.geo.transfer import _inspect_urls_parallel
from PyNCBI.domains.geo.urls import resolve_rnaseq_assembly, rnaseq_counts_url
from PyNCBI.io.http_client import (
    get_default_client,
)
from PyNCBI.io.urls import supplementary_urls
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient


def inspect_accession(
    accession: str,
    *,
    check_file_sizes: bool = True,
    include_sample_breakdown: bool = True,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> InspectionReport:
    """Inspect any GSE, GSM, or GPL accession and return a structured InspectionReport.

    Performs fast metadata extraction, sample characteristics breakdown, and
    HTTP HEAD file size checks without downloading large data bodies.
    """
    accession = validate_accession(accession)
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    cfg = get_config()
    reporter = get_reporter()

    if accession.startswith("GSE"):
        with reporter.task(
            "GEO", "inspect_accession", f"Inspecting GEO Series {accession}", target=accession
        ) as task:
            soft_text = client.get_text(cfg.get_ncbi_query_url(accession))
            gse_info = soft_parser.parse_gse(soft_text, accession)
            task.step(
                f"Parsed series header ({len(gse_info.gsm_ids)} samples, platforms: {gse_info.platforms})"
            )

            char_summaries: list[CharacteristicSummary] = []
            if include_sample_breakdown and gse_info.gsm_ids:
                with contextlib.suppress(Exception):
                    _, _, char_summaries = extract_gse_samples(
                        accession, http_client=client, parser=soft_parser
                    )

            # Extract SuperSeries, SubSeries, and SRA links from !Series_relation
            superseries_id = ""
            subseries_ids: list[str] = []
            sra_url = ""
            for rel in gse_info.relations:
                rel_lower = rel.lower()
                if "superseries of:" in rel_lower:
                    m = re.search(r"GSE\d+", rel)
                    if m:
                        subseries_ids.append(m.group(0))
                elif "subseries of:" in rel_lower:
                    m = re.search(r"GSE\d+", rel)
                    if m:
                        superseries_id = m.group(0)
                elif "sra:" in rel_lower:
                    sra_url = rel.split(":", 1)[1].strip() if ":" in rel else rel.strip()

            # Collect candidate downloadable files
            urls_to_check: list[tuple[str, str]] = [
                (u, "supplementary") for u in gse_info.supplementary_files
            ]

            # Series Matrix URL(s)
            if len(gse_info.platforms) <= 1:
                urls_to_check.append((cfg.get_series_matrix_url(accession), "series_matrix"))
            else:
                for plat in gse_info.platforms:
                    urls_to_check.append(
                        (cfg.get_series_matrix_url(accession, plat), "series_matrix")
                    )

            # Check for NCBI-generated RNA-seq count matrices if high-throughput sequencing
            is_seq = any("sequencing" in t.lower() for t in gse_info.experiment_types)
            assembly = resolve_rnaseq_assembly(gse_info)
            if is_seq and assembly:
                for ctype in ("raw_counts", "norm_counts_TPM"):
                    urls_to_check.append(
                        (
                            rnaseq_counts_url(accession, assembly=assembly, count_type=ctype),
                            "ncbi_rnaseq_counts",
                        )
                    )

            if check_file_sizes and urls_to_check:
                task.step(
                    f"Checking file sizes for {len(urls_to_check)} remote URL(s) via HTTP HEAD"
                )
            files = _inspect_urls_parallel(client, urls_to_check, check_sizes=check_file_sizes)
            task.complete(
                f"Inspected {accession}: {len(gse_info.gsm_ids)} sample(s), {len(files)} available file(s)"
            )

            return InspectionReport(
                accession=accession,
                record_type="GSE",
                title=gse_info.title,
                summary=gse_info.summary,
                overall_design=gse_info.overall_design,
                organisms=gse_info.organisms,
                platforms=gse_info.platforms,
                experiment_types=gse_info.experiment_types,
                pubmed_ids=gse_info.pubmed_ids,
                bioproject_id=gse_info.bioproject_id,
                sra_url=sra_url,
                superseries_id=superseries_id,
                subseries_ids=subseries_ids,
                sample_count=len(gse_info.gsm_ids),
                gsm_ids=gse_info.gsm_ids,
                characteristics_summary=char_summaries,
                files=files,
            )

    if accession.startswith("GDS"):
        return _inspect_gds(accession, check_file_sizes=check_file_sizes, client=client)

    if accession.startswith("GSM"):
        soft_text = client.get_text(cfg.get_ncbi_query_url(accession))
        gsm_info = soft_parser.parse_gsm(soft_text, accession)

        urls_to_check = [(u, "supplementary") for u in gsm_info.supplementary_files]
        files = _inspect_urls_parallel(client, urls_to_check, check_sizes=check_file_sizes)

        if gsm_info.data_row_count > 0:
            files.insert(
                0,
                NCBIFileInfo(
                    name=f"{accession}_table.tsv",
                    url=cfg.get_ncbi_data_url(accession),
                    size_human=f"{gsm_info.data_row_count:,} rows",
                    file_type="tsv",
                    source="on_page_table",
                    exists=True,
                ),
            )

        return InspectionReport(
            accession=accession,
            record_type="GSM",
            title=gsm_info.title,
            organisms=[gsm_info.organism] if gsm_info.organism else [],
            platforms=[gsm_info.platform_id] if gsm_info.platform_id else [],
            experiment_types=[gsm_info.library_strategy] if gsm_info.library_strategy else [],
            parent_gse=gsm_info.series_id,
            sra_url=gsm_info.sra_url,
            biosample_url=gsm_info.biosample_url,
            data_row_count=gsm_info.data_row_count,
            characteristics=gsm_info.characteristics,
            files=files,
            extra_metadata={
                "source_name": gsm_info.source_name,
                "molecule": gsm_info.molecule,
            },
        )

    # GPL Platform
    soft_text = client.get_text(cfg.get_ncbi_query_url(accession))
    gpl_series = soft_parser.parse_gpl_record(soft_text)
    urls_to_check = [
        (u, "supplementary") for u in supplementary_urls(gpl_series.get("supplementary_file"))
    ]
    files = _inspect_urls_parallel(client, urls_to_check, check_sizes=check_file_sizes)

    row_count_val = gpl_series.get("data_row_count", "0")
    try:
        row_count = int(str(row_count_val).strip())
    except ValueError:
        row_count = 0

    if row_count > 0:
        files.insert(
            0,
            NCBIFileInfo(
                name=f"{accession}_annotations.tsv",
                url=cfg.get_ncbi_data_url(accession),
                size_human=f"{row_count:,} rows",
                file_type="tsv",
                source="on_page_table",
                exists=True,
            ),
        )

    orgs = gpl_series.get("organism", [])
    if isinstance(orgs, str):
        orgs = [orgs]

    return InspectionReport(
        accession=accession,
        record_type="GPL",
        title=str(gpl_series.get("title", "")),
        summary=str(gpl_series.get("description", "")),
        organisms=list(orgs),
        platforms=[accession],
        experiment_types=[str(gpl_series.get("technology", ""))]
        if gpl_series.get("technology")
        else [],
        data_row_count=row_count,
        files=files,
        extra_metadata={"manufacturer": str(gpl_series.get("manufacturer", ""))},
    )


def _inspect_gds(gds_id: str, *, check_file_sizes: bool, client: HttpClient) -> InspectionReport:
    from PyNCBI.domains.entrez import esummary_docs

    cfg = get_config()
    uid = gds_id[3:].lstrip("0") or "0"
    docs = esummary_docs("gds", [uid], http_client=client)
    doc = docs.get(uid)
    if not doc:
        from PyNCBI.core.exceptions import AccessionNotFoundError

        raise AccessionNotFoundError(gds_id)
    gpl = str(doc.get("gpl", "")).strip()
    gse = str(doc.get("gse", "")).strip()
    urls = [
        (cfg.get_gds_soft_url(gds_id), "gds_soft"),
        (cfg.get_gds_soft_url(gds_id, full=True), "gds_full_soft"),
    ]
    files = _inspect_urls_parallel(client, urls, check_sizes=check_file_sizes)
    return InspectionReport(
        accession=gds_id,
        record_type="GDS",
        title=str(doc.get("title", "")).strip(),
        summary=str(doc.get("summary", "")).strip(),
        organisms=[str(doc.get("taxon", ""))] if doc.get("taxon") else [],
        platforms=[f"GPL{gpl}" if gpl and not gpl.startswith("GPL") else gpl] if gpl else [],
        experiment_types=[str(doc.get("gdstype", ""))] if doc.get("gdstype") else [],
        pubmed_ids=[str(p) for p in doc.get("pubmedids", []) or []],
        parent_gse=f"GSE{gse}" if gse and not gse.startswith("GSE") else gse,
        sample_count=int(doc.get("n_samples", 0) or 0),
        files=files,
        extra_metadata={
            "value_type": str(doc.get("valtype", "")),
            "ftp_link": str(doc.get("ftplink", "")),
        },
    )


def gse_of_gsm(
    gsm_id: str,
    *,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> str | list[str]:
    """Return the parent GSE accession(s) for a GSM sample."""
    gsm_id = validate_accession(gsm_id, "GSM")
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    cfg = get_config()
    text = client.get_text(cfg.get_ncbi_query_url(gsm_id))
    info = soft_parser.parse_gsm(text, gsm_id)
    return info.series_id


def platform_of_gsm(
    gsm_id: str,
    *,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> str | list[str]:
    """Return the platform GPL accession(s) for a GSM sample."""
    gsm_id = validate_accession(gsm_id, "GSM")
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    cfg = get_config()
    text = client.get_text(cfg.get_ncbi_query_url(gsm_id))
    info = soft_parser.parse_gsm(text, gsm_id)
    return info.platform_id
