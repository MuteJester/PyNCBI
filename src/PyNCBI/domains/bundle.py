"""One-command Study Bundle for PyNCBI.

`build_study_bundle(gse_id)` turns the usual 4-5 separate preflight commands (inspect,
samples, pubmed, sra, files) into a single self-contained local study directory:

    <output_dir>/
      STUDY_OVERVIEW.md       human/agent-readable overview (design, cohort, files, paper, methods)
      bundle.json             bundle manifest + full InspectionReport (machine-readable)
      study.json              versioned StudyRecord (harmonized samples, provenance, links, quality)
      samples_metadata.csv    aligned GSM metadata + unpacked characteristics
      files_manifest.json     every downloadable file with size, source, and URL
      sra_runs.csv            SRA RunInfo table (when sequencing runs exist)
      pubmed_articles.json    linked PubMed articles (+ PMC Methods sections when open access)
      paper_methods.md        PMC Methods / Data Availability sections as Markdown
      <matrix table>          optional: Series Matrix or annotated NCBI RNA-seq counts
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.serialization import json_dumps
from PyNCBI.core.types import validate_accession
from PyNCBI.domains.entrez import fetch_pubmed, fetch_sra_runs
from PyNCBI.domains.geo import (
    extract_gse_samples,
    inspect_accession,
    load_rnaseq_counts,
    load_series_matrix,
)
from PyNCBI.domains.literature import METHODS_SECTIONS, attach_full_text
from PyNCBI.io.http_client import get_default_client
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor as ThreadPoolExecutor

if TYPE_CHECKING:
    from PyNCBI.core.models import CharacteristicSummary, InspectionReport, PubMedArticle
    from PyNCBI.core.protocols import HttpClient


@dataclass
class StudyBundle:
    """Result of `build_study_bundle`: where everything was written plus key facts."""

    gse_id: str
    output_dir: Path
    files: dict[str, Path] = field(default_factory=dict)
    report: InspectionReport | None = None
    sample_count: int = 0
    sra_run_count: int = 0
    pubmed_ids: list[str] = field(default_factory=list)
    methods_available: bool = False
    warnings: list[str] = field(default_factory=list)
    created_at: str = ""
    subseries_bundles: dict[str, StudyBundle] = field(default_factory=dict)
    quality_score: int | None = None
    harmonization: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gse_id": self.gse_id,
            "output_dir": str(self.output_dir),
            "files": {k: str(v) for k, v in self.files.items()},
            "title": self.report.title if self.report else "",
            "sample_count": self.sample_count,
            "sra_run_count": self.sra_run_count,
            "pubmed_ids": self.pubmed_ids,
            "methods_available": self.methods_available,
            "warnings": self.warnings,
            "created_at": self.created_at,
            "subseries_bundles": {k: str(v.output_dir) for k, v in self.subseries_bundles.items()},
            "quality_score": self.quality_score,
            "harmonization": dict(self.harmonization),
        }

    def to_markdown(self) -> str:
        lines = [f"# Study bundle: {self.gse_id}", ""]
        if self.report:
            lines.append(f"**{self.report.title}** — {self.sample_count} samples")
            lines.append("")
        lines.append(f"Written to `{self.output_dir}`:")
        for key, path in self.files.items():
            lines.append(f"- `{path.name}` ({key})")
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in self.warnings)
        return "\n".join(lines)

    def to_text(self) -> str:
        return self.to_markdown().replace("`", "").replace("**", "")


def _sra_search_term(report: InspectionReport) -> str:
    m = re.search(r"(SRP\d+|SRX\d+|SRS\d+|SRR\d+|ERP\d+|DRP\d+)", report.sra_url or "")
    if m:
        return m.group(1)
    return report.bioproject_id or ""


def _char_table(summaries: list[CharacteristicSummary]) -> list[str]:
    if not summaries:
        return ["_No `!Sample_characteristics` fields on this series._"]
    rows = [
        "| Characteristic | Type | Coverage | Distribution / Stats |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for cs in summaries:
        rows.append(
            f"| `{cs.name}` | {cs.dtype} | {cs.non_null_count}/{cs.total_count} | {cs.format_brief()} |"
        )
    return rows


def _write_overview(
    path: Path,
    *,
    report: InspectionReport,
    summaries: list[CharacteristicSummary],
    samples_df: pd.DataFrame,
    sra_df: pd.DataFrame | None,
    articles: list[PubMedArticle],
    bundle: StudyBundle,
    where: str | None,
) -> None:
    gse = report.accession
    L: list[str] = [f"# {gse}: {report.title}", ""]
    L.append(f"_Bundle generated by PyNCBI on {bundle.created_at}_")
    L.append("")
    L.append("## Overview")
    L.append(f"- **Accession:** [{gse}](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gse})")
    if report.experiment_types:
        L.append(f"- **Assay / Type:** {', '.join(report.experiment_types)}")
    if report.organisms:
        L.append(f"- **Organism(s):** {', '.join(report.organisms)}")
    if report.platforms:
        L.append(f"- **Platform(s):** {', '.join(report.platforms)}")
    L.append(
        f"- **Samples:** {report.sample_count}"
        + (f" (bundle filtered with `{where}`: {len(samples_df)} samples)" if where else "")
    )
    if report.superseries_id:
        L.append(f"- **SuperSeries:** {report.superseries_id}")
    if report.subseries_ids:
        L.append(
            f"- **SubSeries ({len(report.subseries_ids)}):** {', '.join(report.subseries_ids)}"
        )
    if report.bioproject_id:
        L.append(f"- **BioProject:** {report.bioproject_id}")
    if report.sra_url:
        L.append(f"- **SRA:** {report.sra_url}")
    if report.pubmed_ids:
        L.append(
            "- **PubMed:** "
            + ", ".join(
                f"[PMID:{p}](https://pubmed.ncbi.nlm.nih.gov/{p}/)" for p in report.pubmed_ids
            )
        )
    if report.summary:
        L += ["", "## Summary", report.summary]
    if report.overall_design:
        L += ["", "## Overall Design", report.overall_design]

    L += ["", f"## Cohort Characteristics ({len(samples_df)} samples)"]
    L += _char_table(summaries)
    L.append("")
    L.append("Full per-sample table: `samples_metadata.csv`")
    if bundle.quality_score is not None or bundle.harmonization:
        L += ["", "## Harmonized Metadata & Quality"]
        if bundle.quality_score is not None:
            L.append(f"- **Quality score:** {bundle.quality_score}/100 (details: `quality.json`)")
        if bundle.harmonization:
            mapped = ", ".join(bundle.harmonization.get("mapped_fields", [])) or "none"
            L.append(
                f"- **Harmonized fields:** {mapped} ({float(bundle.harmonization.get('coverage', 0)):.0%} of values mapped)"
            )
            if bundle.harmonization.get("unmapped_keys"):
                L.append(
                    f"- **Kept verbatim (raw:<key>):** {', '.join(bundle.harmonization['unmapped_keys'])}"
                )
        L.append(
            "- Tables: `samples_harmonized.csv` (canonical columns with units), `provenance.csv` "
            "(source key, rule, confidence per value), `study.json` (versioned record), `manifest.json` (checksums)"
        )

    L += ["", "## Files Available on NCBI"]
    if report.files:
        L.append("| # | Source | File | Type | Size | URL |")
        L.append("| :-- | :-- | :-- | :-- | :-- | :-- |")
        for i, f in enumerate(report.files):
            L.append(
                f"| {i} | {f.source} | `{f.name}` | {f.file_type} | {f.size_human} | {f.url} |"
            )
    else:
        L.append("_No downloadable files were found on the series record._")

    if sra_df is not None and not sra_df.empty:
        n = len(sra_df)
        layout = (
            ", ".join(f"{k} ({v})" for k, v in sra_df["LibraryLayout"].value_counts().items())
            if "LibraryLayout" in sra_df
            else ""
        )
        strat = (
            ", ".join(f"{k} ({v})" for k, v in sra_df["LibraryStrategy"].value_counts().items())
            if "LibraryStrategy" in sra_df
            else ""
        )
        size_mb = (
            pd.to_numeric(sra_df["size_MB"], errors="coerce").sum() if "size_MB" in sra_df else 0
        )
        L += ["", f"## SRA Sequencing Runs ({n} runs)"]
        if strat:
            L.append(f"- **Library strategy:** {strat}")
        if layout:
            L.append(f"- **Layout:** {layout}")
        if size_mb:
            L.append(f"- **Total size:** {size_mb / 1024:.2f} GB")
        L.append("- Full RunInfo table (SRR, SRX, GSM, instrument, download_path): `sra_runs.csv`")

    if articles:
        L += ["", "## Linked Publication(s)"]
        for a in articles:
            authors = ", ".join(a.authors[:3]) + (" et al." if len(a.authors) > 3 else "")
            L.append(f"### {a.title}")
            meta = " · ".join(
                x
                for x in (
                    authors,
                    a.journal,
                    a.pub_date,
                    f"PMID:{a.pmid}",
                    a.pmc_id,
                    f"doi:{a.doi}" if a.doi else "",
                )
                if x
            )
            L.append(f"_{meta}_")
            if a.abstract:
                L += ["", "**Abstract**", "", a.abstract]
            ft = a.full_text
            if ft is not None and ft.is_open_access and ft.selected_sections():
                L += [
                    "",
                    "**Methods (from PMC open-access full text; complete text in `paper_methods.md`)**",
                    "",
                ]
                for sec in ft.selected_sections()[:12]:
                    excerpt = (
                        sec.text
                        if len(sec.text) <= 1200
                        else sec.text[:1200].rsplit(" ", 1)[0] + " …"
                    )
                    heading = sec.path or sec.title or sec.kind
                    if excerpt:
                        L.append(f"- **{heading}:** {excerpt}")
            elif ft is not None:
                L += ["", f"_Methods: {ft.notes}_"]
            L.append("")

    if bundle.subseries_bundles:
        L += ["", "## SubSeries Bundles"]
        for sub, sb in bundle.subseries_bundles.items():
            title = sb.report.title if sb.report else ""
            L.append(
                f"- **{sub}** ({sb.sample_count} samples): `subseries/{sub}/STUDY_OVERVIEW.md` — {title}"
            )

    L += ["", "## Bundle Contents"]
    for key, p in bundle.files.items():
        L.append(f"- `{p.name}` — {key}")
    if bundle.warnings:
        L += ["", "## Warnings"]
        L += [f"- {w}" for w in bundle.warnings]

    L += ["", "## Suggested Next Commands", "```bash"]
    L.append(f"pyncbi preview {gse} --source supp --markdown")
    L.append(f"pyncbi download {gse} --source supp --file-index 0 -o ./{gse}/raw/")
    is_seq = any("sequencing" in t.lower() for t in report.experiment_types)
    if is_seq:
        L.append(
            f"pyncbi download {gse} --source rnaseq --annotate -o ./{gse}/{gse}_counts_annotated.csv"
        )
    else:
        L.append(
            f"pyncbi download {gse} --source matrix --annotate -o ./{gse}/{gse}_matrix_annotated.csv"
        )
    L.append(
        f'pyncbi download {gse} --where "<characteristic> == \'<value>\'" --pattern "*.gz" -o ./{gse}/cohort/'
    )
    L.append("```")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def build_study_bundle(
    gse_id: str,
    output_dir: str | Path | None = None,
    *,
    include_matrix: bool = False,
    include_sra: bool = True,
    include_pubmed: bool = True,
    include_methods: bool = True,
    where: str | None = None,
    check_file_sizes: bool = True,
    expand_subseries: bool = False,
    http_client: HttpClient | None = None,
) -> StudyBundle:
    """Build a complete, self-contained local study directory for a GEO Series.

    Args:
        gse_id: GSE accession
        output_dir: Destination directory (default `./<GSE>_bundle`)
        include_matrix: Also download the primary data table (annotated NCBI RNA-seq counts
            for sequencing studies, Series Matrix for arrays)
        include_sra: Fetch the SRA RunInfo table when the series has sequencing runs
        include_pubmed: Fetch linked PubMed articles
        include_methods: Also fetch PMC open-access Methods / Data Availability sections
        where: Optional cohort filter applied to `samples_metadata.csv`
        check_file_sizes: HTTP HEAD remote files for byte sizes (default True)
        expand_subseries: For a SuperSeries, also build one bundle per SubSeries under
            `<output_dir>/subseries/<GSE>/`
        http_client: Optional HTTP client

    Returns:
        `StudyBundle` with the written file paths; optional steps that fail are recorded in
        `warnings` instead of aborting the bundle.
    """
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    out = Path(output_dir) if output_dir else Path.cwd() / f"{gse_id}_bundle"
    out.mkdir(parents=True, exist_ok=True)
    bundle = StudyBundle(
        gse_id=gse_id,
        output_dir=out,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    reporter = get_reporter()

    with reporter.task(
        "BUNDLE", "build_study_bundle", f"Building study bundle for {gse_id}", target=gse_id
    ) as task:
        # 1. Core: series record + files (no sample breakdown; we fetch samples ourselves once)
        report = inspect_accession(
            gse_id,
            check_file_sizes=check_file_sizes,
            include_sample_breakdown=False,
            http_client=client,
        )
        samples_df, _char_df, summaries = extract_gse_samples(
            gse_id, where=where, http_client=client
        )
        report.characteristics_summary = summaries
        bundle.report = report
        bundle.sample_count = len(samples_df)
        bundle.pubmed_ids = list(report.pubmed_ids)
        task.step(
            f"Series + {len(samples_df)} samples resolved; fetching literature/SRA in parallel"
        )
        if report.subseries_ids:
            bundle.warnings.append(
                f"{gse_id} is a SuperSeries: its data files belong to {len(report.subseries_ids)} SubSeries "
                f"({', '.join(report.subseries_ids)}). Use expand_subseries=True / `pyncbi bundle --subseries` "
                "or bundle each SubSeries directly."
            )
            if expand_subseries:
                for sub in report.subseries_ids:
                    try:
                        bundle.subseries_bundles[sub] = build_study_bundle(
                            sub,
                            out / "subseries" / sub,
                            include_matrix=include_matrix,
                            include_sra=include_sra,
                            include_pubmed=include_pubmed,
                            include_methods=include_methods,
                            check_file_sizes=check_file_sizes,
                            http_client=client,
                        )
                    except Exception as exc:  # noqa: BLE001
                        bundle.warnings.append(
                            f"SubSeries {sub} bundle failed: {type(exc).__name__}: {str(exc).splitlines()[0]}"
                        )

        # 2. Independent enrichment steps in parallel
        sra_term = _sra_search_term(report) if include_sra else ""
        articles: list[PubMedArticle] = []
        sra_df: pd.DataFrame | None = None

        def _pubmed() -> list[PubMedArticle]:
            arts = fetch_pubmed(report.pubmed_ids, http_client=client) if report.pubmed_ids else []
            if include_methods and arts:
                attach_full_text(arts, sections=METHODS_SECTIONS, http_client=client)
            return arts

        def _sra() -> pd.DataFrame:
            return fetch_sra_runs(sra_term, limit=500, http_client=client)

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_pm = pool.submit(_pubmed) if include_pubmed and report.pubmed_ids else None
            fut_sra = pool.submit(_sra) if sra_term else None
            if fut_pm is not None:
                try:
                    articles = fut_pm.result()
                except Exception as exc:  # noqa: BLE001
                    bundle.warnings.append(
                        f"PubMed/PMC step failed: {type(exc).__name__}: {str(exc).splitlines()[0]}"
                    )
            if fut_sra is not None:
                try:
                    sra_df = fut_sra.result()
                except Exception as exc:  # noqa: BLE001
                    bundle.warnings.append(
                        f"SRA RunInfo step failed: {type(exc).__name__}: {str(exc).splitlines()[0]}"
                    )
        if (
            include_sra
            and not sra_term
            and any("sequencing" in t.lower() for t in report.experiment_types)
        ):
            bundle.warnings.append("SRA: series has no SRA/BioProject relation on its record")

        # 3. Write tables
        bundle.files["samples"] = (
            save_data(samples_df, out / "samples_metadata.csv")
            if not samples_df.empty
            else out / "samples_metadata.csv"
        )
        if samples_df.empty:
            bundle.files["samples"].write_text("", encoding="utf-8")
        # Versioned study record layout: harmonized samples, provenance, quality, checksummed manifest
        try:
            from PyNCBI.metadata import assess_study, build_study_record, save_study_record

            record = build_study_record(
                gse_id, where=where, check_file_sizes=False, http_client=client
            )
            record.files = [f.to_dict() for f in report.files]
            record.quality = assess_study(record)
            save_study_record(record, out)
            bundle.files["record"] = out / "study.json"
            bundle.files["samples_harmonized"] = out / "samples_harmonized.csv"
            bundle.files["provenance"] = out / "provenance.csv"
            bundle.files["quality"] = out / "quality.json"
            bundle.files["record_manifest"] = out / "manifest.json"
            bundle.quality_score = record.quality.score if record.quality else None
            bundle.harmonization = dict(record.harmonization)
        except Exception as exc:  # noqa: BLE001
            bundle.warnings.append(
                f"Study record step failed: {type(exc).__name__}: {str(exc).splitlines()[0]}"
            )
        bundle.files["manifest"] = save_data(
            [f.to_dict() for f in report.files], out / "files_manifest.json"
        )
        if sra_df is not None and not sra_df.empty:
            bundle.files["sra"] = save_data(sra_df.reset_index(drop=True), out / "sra_runs.csv")
            bundle.sra_run_count = len(sra_df)
        if articles:
            bundle.files["pubmed"] = save_data(
                [a.to_dict() for a in articles], out / "pubmed_articles.json"
            )
            md_parts = [
                a.full_text.to_markdown()
                for a in articles
                if a.full_text is not None and a.full_text.is_open_access
            ]
            if md_parts:
                methods_path = out / "paper_methods.md"
                methods_path.write_text("\n\n---\n\n".join(md_parts) + "\n", encoding="utf-8")
                bundle.files["methods"] = methods_path
                bundle.methods_available = True

        # 4. Optional primary data table
        if include_matrix:
            is_seq = any("sequencing" in t.lower() for t in report.experiment_types)
            has_counts = any(f.source == "ncbi_rnaseq_counts" for f in report.files)
            try:
                if is_seq and (has_counts or not check_file_sizes):
                    target = out / f"{gse_id}_raw_counts_annotated.csv"
                    load_rnaseq_counts(gse_id, annotate=True, save_to=target, http_client=client)
                    bundle.files["matrix"] = target
                else:
                    target = out / f"{gse_id}_series_matrix.csv"
                    res = load_series_matrix(gse_id, save_to=target, http_client=client)
                    if res.matrix_df.empty:
                        bundle.warnings.append(
                            "Series Matrix contains no data rows (sequencing study?); use --source rnaseq/supp"
                        )
                    else:
                        bundle.files["matrix"] = target
            except Exception as exc:  # noqa: BLE001
                bundle.warnings.append(
                    f"Data table step failed: {type(exc).__name__}: {str(exc).splitlines()[0]}"
                )

        # 5. Overview + machine-readable bundle.json (written last so they list every file)
        overview_path = out / "STUDY_OVERVIEW.md"
        bundle.files = {
            "overview": overview_path,
            "bundle_json": out / "bundle.json",
            **bundle.files,
        }
        _write_overview(
            overview_path,
            report=report,
            summaries=summaries,
            samples_df=samples_df,
            sra_df=sra_df,
            articles=articles,
            bundle=bundle,
            where=where,
        )
        study_payload = {
            "bundle": bundle.to_dict(),
            "report": report.to_dict(),
            "pubmed": [a.to_dict() for a in articles],
        }
        (out / "bundle.json").write_text(json_dumps(study_payload, indent=2), encoding="utf-8")
        task.complete(
            f"Study bundle for {gse_id} written to {out} ({len(bundle.files)} files, {len(bundle.warnings)} warning(s))"
        )
        return bundle
