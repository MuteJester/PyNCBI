"""Build versioned :class:`StudyRecord` objects from GEO (plus optional enrichment)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.types import validate_accession
from PyNCBI.domains.geo import extract_gse_samples, inspect_accession
from PyNCBI.io.http_client import get_default_client
from PyNCBI.io.urls import supplementary_urls
from PyNCBI.metadata import vocabulary
from PyNCBI.metadata.harmonize import (
    harmonize_characteristics,
    harmonize_sample,
    vocabulary_version,
)
from PyNCBI.metadata.quality import assess_study
from PyNCBI.metadata.schema import (
    SCHEMA_VERSION,
    NormalizedValue,
    SampleRecord,
    StudyRecord,
    now_iso,
)

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient

ENRICHMENTS: tuple[str, ...] = ("biosample", "sra", "pubmed")

_REL_RE = re.compile(r"^\s*([A-Za-z ]+):\s*(\S+)")
_ACC_RE = re.compile(
    r"(SAMN\d+|SAMEA\d+|SAMD\d+|SRX\d+|ERX\d+|DRX\d+|SRS\d+|ERS\d+|SRR\d+|SRP\d+|ERP\d+|PRJ[NED][AB]\d+|GSE\d+)"
)
_LINK_KEYS = {
    "BIOSAMPLE": "biosample",
    "SRA": "sra_experiment",
    "BIOPROJECT": "bioproject",
    "SUBSERIES OF": "superseries",
}


def _first(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).split("\n")[0].strip()


def _sample_links(relation_blob: Any, gse_id: str) -> dict[str, str]:
    links: dict[str, str] = {}
    if isinstance(relation_blob, str):
        for line in relation_blob.split("\n"):
            m = _REL_RE.match(line)
            if not m:
                continue
            key = _LINK_KEYS.get(m.group(1).strip().upper())
            acc = _ACC_RE.search(m.group(2))
            if key and acc:
                target = acc.group(1)
                if key == "sra_experiment" and target.startswith(("SRS", "ERS")):
                    key = "sra_sample"
                links.setdefault(key, target)
    links["series"] = gse_id
    return links


def _sample_supp_files(row: pd.Series) -> list[str]:
    urls: list[str] = []
    for col in row.index:
        if str(col).startswith("supplementary_file"):
            urls.extend(supplementary_urls(row[col]))
    return urls


def build_study_record(
    gse_id: str,
    *,
    harmonize: bool = True,
    assess: bool = True,
    enrich: tuple[str, ...] | list[str] = (),
    where: str | None = None,
    check_file_sizes: bool = False,
    http_client: HttpClient | None = None,
) -> StudyRecord:
    """Assemble a complete, versioned StudyRecord for a GEO series.

    Two GEO requests are needed (series header + batch sample view); HEAD requests are added
    when ``check_file_sizes`` is True; each enrichment adds E-utilities calls.

    Args:
        gse_id: GSE accession
        harmonize: Map characteristics to the controlled vocabulary (default True)
        assess: Attach a quality report (default True)
        enrich: Any of ``'biosample'`` (attributes merged + harmonized), ``'sra'`` (run
            accessions per sample), ``'pubmed'`` (linked articles in ``extra['pubmed']``)
        where: Optional cohort filter (same syntax as ``extract_gse_samples``)
        check_file_sizes: HTTP HEAD file sizes for the file inventory
        http_client: Optional HTTP client
    """
    from PyNCBI import __version__

    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    kinds = [k.strip().lower() for k in enrich if k.strip()]
    unknown = [k for k in kinds if k not in ENRICHMENTS]
    if unknown:
        raise ValueError(f"Unknown enrichment(s) {unknown}; choose from {ENRICHMENTS}")

    with get_reporter().task(
        "METADATA", "build_study_record", f"Building study record for {gse_id}", target=gse_id
    ) as task:
        report = inspect_accession(
            gse_id,
            check_file_sizes=check_file_sizes,
            include_sample_breakdown=False,
            http_client=client,
        )
        combined_df, char_df, _ = extract_gse_samples(gse_id, where=where, http_client=client)
        task.step(f"{len(combined_df)} sample(s) retrieved; harmonizing characteristics")

        samples: list[SampleRecord] = []
        char_rows: dict[str, dict[str, str]] = {}
        if not char_df.empty:
            for char_key, char_row in char_df.iterrows():
                char_rows[str(char_key)] = {
                    str(k): str(v) for k, v in char_row.items() if pd.notna(v) and str(v) != ""
                }
        for gsm_key, row in combined_df.iterrows():
            gsm_id = str(gsm_key)
            chars = char_rows.get(gsm_id, {})
            raw_count = _first(row.get("data_row_count", "0")) or "0"
            samples.append(
                SampleRecord(
                    gsm_id=str(gsm_id),
                    title=_first(row.get("title", "")),
                    organism=_first(row.get("organism_ch1", "")),
                    platform_id=_first(row.get("platform_id", "")),
                    source_name=_first(row.get("source_name_ch1", "")),
                    molecule=_first(row.get("molecule_ch1", "")),
                    library_strategy=_first(row.get("library_strategy", "")),
                    characteristics=chars,
                    harmonized=harmonize_sample(chars) if harmonize else {},
                    supplementary_files=_sample_supp_files(row),
                    links=_sample_links(row.get("relation"), gse_id),
                    data_row_count=int(raw_count) if raw_count.isdigit() else 0,
                )
            )

        links: dict[str, Any] = {}
        if report.bioproject_id:
            links["bioproject"] = report.bioproject_id
        m = re.search(r"(SRP\d+|ERP\d+|DRP\d+)", report.sra_url or "")
        if m:
            links["sra_study"] = m.group(1)
        if report.pubmed_ids:
            links["pubmed"] = list(report.pubmed_ids)
        if report.superseries_id:
            links["superseries"] = report.superseries_id
        if report.subseries_ids:
            links["subseries"] = list(report.subseries_ids)

        record = StudyRecord(
            gse_id=gse_id,
            title=report.title,
            summary=report.summary,
            overall_design=report.overall_design,
            organisms=list(report.organisms),
            platforms=list(report.platforms),
            experiment_types=list(report.experiment_types),
            pubmed_ids=list(report.pubmed_ids),
            bioproject_id=report.bioproject_id,
            superseries_id=report.superseries_id,
            subseries_ids=list(report.subseries_ids),
            declared_sample_count=report.sample_count,
            samples=samples,
            files=[f.to_dict() for f in report.files],
            links=links,
            retrieved_at=now_iso(),
            source_versions={
                "schema": SCHEMA_VERSION,
                "pyncbi": __version__,
                "vocabulary": vocabulary_version(),
            },
        )
        if where:
            record.extra["cohort_filter"] = where
        if harmonize:
            kept = [s.gsm_id for s in samples if s.gsm_id in char_df.index]
            subset = char_df.loc[kept] if (not char_df.empty and kept) else char_df
            table = harmonize_characteristics(pd.DataFrame(subset))
            record.harmonization = table.summary()

        for kind in kinds:
            task.step(f"Enriching with {kind}")
            try:
                if kind == "biosample":
                    _enrich_biosample(record, client)
                elif kind == "sra":
                    _enrich_sra(record, client)
                elif kind == "pubmed":
                    _enrich_pubmed(record, client)
            except Exception as exc:  # noqa: BLE001 - enrichment is best effort, recorded on the record
                record.extra.setdefault("enrichment_errors", []).append(
                    {
                        "enrichment": kind,
                        "error": type(exc).__name__,
                        "message": str(exc).splitlines()[0],
                    }
                )

        if assess:
            record.quality = assess_study(record)
        task.complete(
            f"{gse_id}: {len(samples)} sample(s), {len(record.harmonization.get('mapped_fields', []))} harmonized field(s), quality {record.quality.score if record.quality else 'n/a'}"
        )
        return record


def _enrich_biosample(record: StudyRecord, client: HttpClient) -> None:
    from PyNCBI.domains.entrez import fetch_biosamples

    accessions = [s.links["biosample"] for s in record.samples if s.links.get("biosample")]
    if not accessions:
        return
    by_acc = {b.accession: b for b in fetch_biosamples(accessions, http_client=client)}
    for sample in record.samples:
        bs = by_acc.get(sample.links.get("biosample", ""))
        if bs is None:
            continue
        sample.extra["biosample"] = bs.to_dict()
        if not sample.harmonized and not sample.characteristics:
            continue
        for key, raw in bs.attributes.items():
            canon = vocabulary.canonical_field(key)
            target = canon or f"raw:{key}"
            if target in sample.harmonized and sample.harmonized[target].value is not None:
                continue  # GEO value wins; BioSample only fills gaps
            nv = (
                vocabulary.normalize_value(canon, raw, source_key=key)
                if canon
                else NormalizedValue(
                    value=str(raw).strip() or None,
                    raw=str(raw),
                    confidence="unmapped",
                    rule="unmapped_key",
                    source_key=key,
                )
            )
            nv.source = "biosample:attributes"
            sample.harmonized[target] = nv


def _enrich_sra(record: StudyRecord, client: HttpClient) -> None:
    from PyNCBI.domains.entrez import fetch_sra_runs

    term = record.links.get("sra_study") or record.bioproject_id
    if not term:
        return
    runs = fetch_sra_runs(term, limit=None, http_client=client)
    if runs.empty or "SampleName" not in runs.columns:
        return
    by_gsm: dict[str, list[str]] = {}
    for _, r in runs.iterrows():
        by_gsm.setdefault(str(r.get("SampleName", "")), []).append(str(r.get("Run", "")))
    for sample in record.samples:
        if sample.gsm_id in by_gsm:
            sample.links["sra_runs"] = ",".join(by_gsm[sample.gsm_id])
    record.extra["sra_run_count"] = int(len(runs))


def _enrich_pubmed(record: StudyRecord, client: HttpClient) -> None:
    from PyNCBI.domains.entrez import fetch_pubmed

    if not record.pubmed_ids:
        return
    articles = fetch_pubmed(record.pubmed_ids, http_client=client)
    record.extra["pubmed"] = [a.to_dict() for a in articles]
    pmc = [a.pmc_id for a in articles if a.pmc_id]
    if pmc:
        record.links["pmc"] = pmc
