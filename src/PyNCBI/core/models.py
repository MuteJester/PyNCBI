"""Structured domain models for PyNCBI.

Provides clean, serializable dataclasses for NCBI GEO records, files,
sample characteristics summaries, Entrez search hits, PubMed articles,
Gene records, and agent response envelopes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from PyNCBI.core.serialization import json_dumps


@dataclass
class NCBIFileInfo:
    """Metadata for a downloadable file hosted on NCBI."""

    name: str
    url: str
    size_bytes: int | None = None
    size_human: str = "unknown"
    last_modified: str | None = None
    file_type: str = "file"
    source: str = (
        "supplementary"  # "supplementary", "series_matrix", "ncbi_rnaseq_counts", "on_page_table"
    )
    exists: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CharacteristicSummary:
    """Summary of a parsed sample characteristic column across a GSE cohort."""

    name: str
    dtype: str  # "categorical" | "numeric"
    non_null_count: int
    total_count: int
    unique_count: int
    value_counts: dict[str, int] | None = None
    numeric_stats: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_brief(self) -> str:
        """Concise 1-line summary for terminal and Markdown views."""
        if self.dtype == "numeric" and self.numeric_stats:
            st = self.numeric_stats
            return (
                f"numeric (n={self.non_null_count}/{self.total_count}): "
                f"min={st['min']:.3g}, max={st['max']:.3g}, "
                f"mean={st['mean']:.3g}, median={st['median']:.3g}"
            )
        if self.value_counts:
            top_items = [f"{k} ({v})" for k, v in list(self.value_counts.items())[:6]]
            extra = f", +{len(self.value_counts) - 6} more" if len(self.value_counts) > 6 else ""
            return f"{', '.join(top_items)}{extra}"
        return f"{self.unique_count} unique values (n={self.non_null_count}/{self.total_count})"


@dataclass
class InspectionReport:
    """Comprehensive overview of an NCBI GEO accession (GSE, GSM, or GPL)."""

    accession: str
    record_type: str  # "GSE", "GSM", "GPL"
    title: str = ""
    summary: str = ""
    overall_design: str = ""
    organisms: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    experiment_types: list[str] = field(default_factory=list)
    pubmed_ids: list[str] = field(default_factory=list)
    bioproject_id: str = ""
    sra_url: str = ""
    biosample_url: str = ""
    parent_gse: str = ""
    superseries_id: str = ""
    subseries_ids: list[str] = field(default_factory=list)
    sample_count: int = 0
    gsm_ids: list[str] = field(default_factory=list)
    data_row_count: int = 0
    characteristics: dict[str, str] = field(default_factory=dict)
    characteristics_summary: list[CharacteristicSummary] = field(default_factory=list)
    files: list[NCBIFileInfo] = field(default_factory=list)
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Keep gsm_ids preview manageable in raw dict while preserving full count
        if len(self.gsm_ids) > 100:
            data["gsm_ids_preview"] = self.gsm_ids[:50]
            data["gsm_ids_truncated"] = True
        return data

    def to_markdown(self) -> str:
        """Render a clean, structured Markdown overview."""
        lines = [f"# {self.record_type}: {self.accession} — {self.title}"]
        lines.append("")
        lines.append("## Overview")
        if self.organisms:
            lines.append(f"- **Organism(s):** {', '.join(self.organisms)}")
        if self.platforms:
            lines.append(f"- **Platform(s):** {', '.join(self.platforms)}")
        if self.experiment_types:
            lines.append(f"- **Assay / Type:** {', '.join(self.experiment_types)}")
        if self.parent_gse:
            lines.append(f"- **Parent Series:** {self.parent_gse}")
        if self.superseries_id:
            lines.append(f"- **SuperSeries:** {self.superseries_id}")
        if self.subseries_ids:
            lines.append(
                f"- **SubSeries ({len(self.subseries_ids)}):** {', '.join(self.subseries_ids)}"
            )
        if self.record_type == "GSE":
            lines.append(f"- **Sample Count:** {self.sample_count}")
        if self.data_row_count > 0:
            lines.append(f"- **On-Page Data Table Rows:** {self.data_row_count:,}")
        if self.pubmed_ids:
            lines.append(f"- **PubMed ID(s):** {', '.join(self.pubmed_ids)}")
        if self.bioproject_id:
            lines.append(f"- **BioProject:** {self.bioproject_id}")
        if self.sra_url:
            lines.append(f"- **SRA Link:** {self.sra_url}")
        if self.biosample_url:
            lines.append(f"- **BioSample Link:** {self.biosample_url}")

        if self.summary:
            lines.append("")
            lines.append("## Summary")
            lines.append(self.summary)

        if self.overall_design:
            lines.append("")
            lines.append("## Overall Design")
            lines.append(self.overall_design)

        if self.characteristics:
            lines.append("")
            lines.append("## Sample Characteristics")
            for k, v in self.characteristics.items():
                lines.append(f"- **{k}:** {v}")

        if self.characteristics_summary:
            lines.append("")
            lines.append("## Cohort Characteristics Breakdown")
            lines.append("| Characteristic | Type | Coverage | Distribution / Stats |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for cs in self.characteristics_summary:
                lines.append(
                    f"| `{cs.name}` | {cs.dtype} | {cs.non_null_count}/{cs.total_count} | {cs.format_brief()} |"
                )

        if self.files:
            lines.append("")
            lines.append("## Available Files on NCBI")
            lines.append("| # | Source | File Name | Type | Size | URL |")
            lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for idx, f in enumerate(self.files):
                lines.append(
                    f"| {idx} | {f.source} | `{f.name}` | {f.file_type} | {f.size_human} | {f.url} |"
                )

        return "\n".join(lines)

    def to_text(self) -> str:
        """Render a clean terminal text overview."""
        lines = [
            f"=== {self.record_type}: {self.accession} ===",
            f"Title:          {self.title}",
        ]
        if self.organisms:
            lines.append(f"Organism(s):    {', '.join(self.organisms)}")
        if self.platforms:
            lines.append(f"Platform(s):    {', '.join(self.platforms)}")
        if self.experiment_types:
            lines.append(f"Assay Type(s):  {', '.join(self.experiment_types)}")
        if self.parent_gse:
            lines.append(f"Parent GSE:     {self.parent_gse}")
        if self.superseries_id:
            lines.append(f"SuperSeries:    {self.superseries_id}")
        if self.subseries_ids:
            lines.append(f"SubSeries:      {', '.join(self.subseries_ids)}")
        if self.record_type == "GSE":
            lines.append(f"Samples (GSMs): {self.sample_count}")
        if self.data_row_count > 0:
            lines.append(f"Table Rows:     {self.data_row_count:,}")
        if self.pubmed_ids:
            lines.append(f"PubMed ID(s):   {', '.join(self.pubmed_ids)}")
        if self.bioproject_id:
            lines.append(f"BioProject:     {self.bioproject_id}")

        if self.summary:
            lines.append(f"\nSummary:\n  {self.summary}")
        if self.overall_design:
            lines.append(f"\nOverall Design:\n  {self.overall_design}")

        if self.characteristics:
            lines.append("\nCharacteristics:")
            for k, v in self.characteristics.items():
                lines.append(f"  - {k}: {v}")

        if self.characteristics_summary:
            lines.append("\nSample Characteristics Breakdown:")
            for cs in self.characteristics_summary:
                lines.append(f"  - {cs.name}: {cs.format_brief()}")

        if self.files:
            lines.append("\nDownloadable Files:")
            for idx, f in enumerate(self.files):
                lines.append(f"  [{idx}] {f.name} ({f.size_human}, {f.source}) -> {f.url}")

        return "\n".join(lines)


@dataclass
class GEOSearchHit:
    """Single result from an NCBI GEO (`db=gds`) search."""

    accession: str
    gds_uid: str
    entry_type: str  # "GSE", "GDS", "GPL", "GSM"
    title: str = ""
    summary: str = ""
    organism: str = ""
    dataset_type: str = ""
    platforms: list[str] = field(default_factory=list)
    sample_count: int = 0
    pubmed_ids: list[str] = field(default_factory=list)
    bioproject_id: str = ""
    supp_file_types: str = ""
    ftp_url: str = ""
    release_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PubMedArticle:
    """Structured PubMed article metadata and abstract."""

    pmid: str
    title: str = ""
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    pub_date: str = ""
    doi: str = ""
    pmc_id: str = ""
    keywords: list[str] = field(default_factory=list)
    full_text: PMCFullText | None = None
    author_details: list[dict[str, str]] = field(
        default_factory=list
    )  # last, fore, initials, affiliation

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["full_text"] = self.full_text.to_dict() if self.full_text is not None else None
        return data


@dataclass
class PMCSection:
    """One section (or subsection) of a PubMed Central full-text article."""

    title: str
    kind: str  # introduction | methods | results | discussion | data_availability | ...
    level: int
    text: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PMCFullText:
    """Structured open-access full text of a PubMed Central article."""

    pmcid: str
    pmid: str = ""
    doi: str = ""
    title: str = ""
    journal: str = ""
    pub_year: str = ""
    abstract: str = ""
    is_open_access: bool = False
    sections: list[PMCSection] = field(default_factory=list)
    notes: str = ""
    section_filter: list[str] | None = None  # default selection used by to_dict/to_markdown

    _KIND_ALIASES: ClassVar[dict[str, str]] = {
        "method": "methods",
        "methods": "methods",
        "materials": "methods",
        "data": "data_availability",
        "data_availability": "data_availability",
        "availability": "data_availability",
        "intro": "introduction",
        "introduction": "introduction",
        "background": "introduction",
        "results": "results",
        "result": "results",
        "discussion": "discussion",
        "conclusion": "conclusion",
        "conclusions": "conclusion",
        "supplementary": "supplementary",
        "supplement": "supplementary",
        "acknowledgments": "acknowledgments",
        "other": "other",
        "all": "all",
    }

    def filter_sections(self, sections: list[str] | tuple[str, ...] | None) -> list[PMCSection]:
        """Select sections by kind (e.g. 'methods') or case-insensitive title substring.

        A kind match also returns subsections inheriting that kind. `None`, `[]` or `'all'`
        return every section.
        """
        if not sections:
            return list(self.sections)
        wanted_kinds: set[str] = set()
        substrings: list[str] = []
        for raw in sections:
            key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
            kind = self._KIND_ALIASES.get(key)
            if kind == "all":
                return list(self.sections)
            if kind:
                wanted_kinds.add(kind)
            else:
                substrings.append(str(raw).strip().lower())
        out: list[PMCSection] = []
        for sec in self.sections:
            if sec.kind in wanted_kinds or any(sub in sec.title.lower() for sub in substrings):
                out.append(sec)
        return out

    def selected_sections(self) -> list[PMCSection]:
        """Sections matching the stored `section_filter` (all sections when unset)."""
        return self.filter_sections(self.section_filter)

    def text_for(self, kind: str) -> str:
        """Concatenated text of all sections of a kind (subsection titles kept as headings)."""
        parts: list[str] = []
        for sec in self.filter_sections([kind]):
            if sec.level > 1 and sec.title:
                parts.append(f"{sec.title}: {sec.text}".strip())
            else:
                parts.append(sec.text)
        return "\n\n".join(p for p in parts if p)

    @property
    def methods(self) -> str:
        return self.text_for("methods")

    _AVAILABILITY_RE: ClassVar[Any] = None  # compiled lazily

    @property
    def data_availability(self) -> str:
        """Data-availability text; falls back to scanning paragraphs when no section is titled so.

        Journals such as Science put the statement inside Acknowledgments
        ("Data and materials availability: ... GSE152418"), so a titled section is not
        required for agents to find where the data lives.
        """
        titled = self.text_for("data_availability")
        if titled:
            return titled
        import re

        if PMCFullText._AVAILABILITY_RE is None:
            PMCFullText._AVAILABILITY_RE = re.compile(
                r"(data|code|materials?)\s+(and\s+\w+\s+)?availability|availab\w*\s+(in|at|from|under|through)\b|"
                r"deposited\s+(in|at|to|under)|accession\s+(number|code|no)|\bGSE\d{3,}\b|\bPRJNA\d+\b|\bSRP\d+\b|"
                r"\bE-MTAB-\d+\b|\bEGAS\d+\b|\bphs\d{6}\b",
                re.I,
            )
        found: list[str] = []
        for sec in self.sections:
            for para in sec.text.split("\n\n"):
                if PMCFullText._AVAILABILITY_RE.search(para):
                    snippet = para.strip()
                    # Keep only the sentences that mention availability/accessions (ack sections are long)
                    sentences = re.split(r"(?<=[.!?])\s+", snippet)
                    keep = [s for s in sentences if PMCFullText._AVAILABILITY_RE.search(s)]
                    text = " ".join(keep) if keep else snippet
                    if text and text not in found:
                        found.append(text)
        return "\n\n".join(found)

    @property
    def geo_accessions(self) -> list[str]:
        """GEO Series accessions (GSE...) mentioned anywhere in the full text."""
        import re

        found: list[str] = []
        blob = " ".join(s.text for s in self.sections) + " " + self.abstract
        for m in re.finditer(r"\bGSE\d{3,}\b", blob):
            if m.group(0) not in found:
                found.append(m.group(0))
        return found

    def to_dict(self, sections: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        active = list(sections) if sections is not None else self.section_filter
        selected = self.filter_sections(active)
        return {
            "section_filter": active,
            "available_sections": [s.title or s.kind for s in self.sections]
            if active and not selected
            else [],
            "pmcid": self.pmcid,
            "pmid": self.pmid,
            "doi": self.doi,
            "title": self.title,
            "journal": self.journal,
            "pub_year": self.pub_year,
            "is_open_access": self.is_open_access,
            "section_count": len(self.sections),
            "geo_accessions": self.geo_accessions,
            "notes": self.notes,
            "sections": [s.to_dict() for s in selected],
        }

    def to_markdown(self, sections: list[str] | tuple[str, ...] | None = None) -> str:
        ids = ", ".join(x for x in (self.pmcid, f"PMID:{self.pmid}" if self.pmid else "") if x)
        lines = [f"# {self.title or self.pmcid} ({ids})"]
        meta = " · ".join(x for x in (self.journal, self.pub_year, self.doi) if x)
        if meta:
            lines.append(f"*{meta}*")
        if not self.is_open_access:
            lines.append("")
            lines.append(f"> Full text not available from PMC: {self.notes}")
            return "\n".join(lines)
        active = list(sections) if sections is not None else self.section_filter
        selected = self.filter_sections(active)
        if active and not selected and self.sections:
            titles = ", ".join(f"`{s.title or s.kind}`" for s in self.sections[:25])
            lines.append("")
            lines.append(f"> No sections matched {active}. Available sections: {titles}")
        for sec in selected:
            lines.append("")
            lines.append(f"{'#' * min(6, sec.level + 1)} {sec.title or sec.kind.title()}")
            if sec.text:
                lines.append(sec.text)
        return "\n".join(lines)

    def to_text(self) -> str:
        return self.to_markdown()


@dataclass
class GeneRecord:
    """Structured NCBI Gene record."""

    gene_id: str
    symbol: str = ""
    description: str = ""
    organism: str = ""
    chromosome: str = ""
    map_location: str = ""
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    other_designations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BioSampleRecord:
    """Clean NCBI BioSample record with unpacked attributes."""

    accession: str
    uid: str = ""
    title: str = ""
    organism: str = ""
    taxid: str = ""
    package: str = ""
    publication_date: str = ""
    owner: str = ""
    gsm_id: str = ""
    sra_sample: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    identifiers: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/biosample/{self.accession}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


@dataclass
class BioProjectRecord:
    """Clean NCBI BioProject record."""

    accession: str
    uid: str = ""
    title: str = ""
    name: str = ""
    description: str = ""
    project_type: str = ""
    data_type: str = ""
    target_scope: str = ""
    target_material: str = ""
    method: str = ""
    organism: str = ""
    taxid: str = ""
    registration_date: str = ""
    relevance: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/bioproject/{self.accession}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


@dataclass
class NucleotideRecord:
    """Clean Nucleotide / Protein sequence summary (`esummary db=nuccore|protein`)."""

    accession: str
    uid: str = ""
    title: str = ""
    organism: str = ""
    taxid: str = ""
    length: int = 0
    moltype: str = ""
    biomol: str = ""
    topology: str = ""
    source_db: str = ""
    completeness: str = ""
    create_date: str = ""
    update_date: str = ""
    db: str = "nuccore"

    @property
    def url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/{self.db}/{self.accession}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


@dataclass
class TaxonRecord:
    """Clean NCBI Taxonomy record with lineage."""

    taxid: str
    scientific_name: str = ""
    common_name: str = ""
    rank: str = ""
    division: str = ""
    parent_taxid: str = ""
    genetic_code: str = ""
    mito_genetic_code: str = ""
    lineage: list[str] = field(default_factory=list)
    lineage_ranks: dict[str, str] = field(default_factory=dict)
    synonyms: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id={self.taxid}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


@dataclass
class AssemblyRecord:
    """Clean NCBI Datasets genome assembly report."""

    accession: str
    paired_accession: str = ""
    organism: str = ""
    taxid: str = ""
    strain: str = ""
    assembly_name: str = ""
    assembly_level: str = ""
    assembly_status: str = ""
    assembly_type: str = ""
    refseq_category: str = ""
    source_db: str = ""
    release_date: str = ""
    submitter: str = ""
    bioproject: str = ""
    biosample: str = ""
    total_length: int = 0
    contig_n50: int = 0
    scaffold_n50: int = 0
    chromosome_count: int = 0
    gc_percent: float | None = None
    annotation_name: str = ""
    annotation_date: str = ""
    gene_count: int = 0
    protein_coding_count: int = 0

    @property
    def url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/datasets/genome/{self.accession}/"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


@dataclass
class ClinVarRecord:
    """Clean, flattened ClinVar variation record (from the noisy `esummary db=clinvar` JSON)."""

    variation_id: str
    accession: str = ""
    title: str = ""
    gene_symbol: str = ""
    gene_id: str = ""
    variant_type: str = ""
    cdna_change: str = ""
    protein_change: str = ""
    molecular_consequences: list[str] = field(default_factory=list)
    clinical_significance: str = ""
    review_status: str = ""
    review_stars: int = 0
    last_evaluated: str = ""
    conditions: list[str] = field(default_factory=list)
    condition_xrefs: list[str] = field(default_factory=list)
    rsid: str = ""
    canonical_spdi: str = ""
    grch38: dict[str, Any] = field(default_factory=dict)
    grch37: dict[str, Any] = field(default_factory=dict)
    oncogenicity: str = ""
    clinical_impact: str = ""
    submission_count: int = 0

    @property
    def url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{self.variation_id}/"

    @staticmethod
    def _loc_str(loc: dict[str, Any]) -> str:
        if not loc or not loc.get("chr"):
            return ""
        start, stop = loc.get("start"), loc.get("stop")
        if start is None:
            return f"chr{loc['chr']}"
        return (
            f"chr{loc['chr']}:{start}-{stop}"
            if stop not in (None, start)
            else f"chr{loc['chr']}:{start}"
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["grch38_location"] = self._loc_str(self.grch38)
        data["grch37_location"] = self._loc_str(self.grch37)
        data["url"] = self.url
        return data


@dataclass
class DbSNPRecord:
    """Clean dbSNP reference SNP record (from `esummary db=snp`)."""

    rsid: str
    chromosome: str = ""
    position_grch38: int | None = None
    position_grch37: int | None = None
    spdi: str = ""
    ref_allele: str = ""
    alt_alleles: list[str] = field(default_factory=list)
    gene_symbols: list[str] = field(default_factory=list)
    gene_ids: list[str] = field(default_factory=list)
    functional_class: list[str] = field(default_factory=list)
    clinical_significance: list[str] = field(default_factory=list)
    hgvs: list[str] = field(default_factory=list)
    protein_change: str = ""
    allele_frequencies: dict[str, float] = field(default_factory=dict)
    validated: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/snp/{self.rsid}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


@dataclass
class FilePreview:
    """Instant streaming preview of a remote NCBI GEO file or archive without full download."""

    accession: str
    file_name: str
    url: str
    source: str
    file_type: str
    total_columns: int = 0
    columns: list[str] = field(default_factory=list)
    index_name: str = ""
    sample_ids_preview: list[str] = field(default_factory=list)
    preview_rows: list[dict[str, Any]] = field(default_factory=list)
    archive_members: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            f"# File Preview: `{self.file_name}` ({self.accession})",
            f"- **Source:** `{self.source}` ({self.file_type})",
            f"- **URL:** {self.url}",
        ]
        if self.total_columns > 0:
            col_preview = ", ".join(f"`{c}`" for c in self.columns[:12])
            if self.total_columns > 12:
                col_preview += f", ... (+{self.total_columns - 12} more)"
            lines.append(f"- **Columns ({self.total_columns}):** {col_preview}")
        if self.index_name:
            lines.append(f"- **Index / Feature Column:** `{self.index_name}`")
        if self.notes:
            lines.append(f"- **Note:** {self.notes}")
        if self.archive_members:
            lines.append("\n## Archive Members (First Peeked Entries)")
            lines.append("| Member Name | Size (bytes) |")
            lines.append("| :--- | :--- |")
            for m in self.archive_members:
                lines.append(f"| `{m['name']}` | {m.get('size_bytes', 0):,} |")
        if self.preview_rows:
            lines.append(f"\n## First {len(self.preview_rows)} Rows (Showing First Columns)")
            keys = list(self.preview_rows[0].keys())[:8]
            lines.append("| " + " | ".join(str(k) for k in keys) + " |")
            lines.append("| " + " | ".join(":---" for _ in keys) + " |")
            for r in self.preview_rows:
                lines.append("| " + " | ".join(str(r.get(k, "")) for k in keys) + " |")
        return "\n".join(lines)

    def to_text(self) -> str:
        lines = [
            f"=== File Preview: {self.file_name} ({self.accession}) ===",
            f"Source:        {self.source} ({self.file_type})",
            f"URL:           {self.url}",
        ]
        if self.total_columns > 0:
            col_preview = ", ".join(self.columns[:10])
            if self.total_columns > 10:
                col_preview += f" (+{self.total_columns - 10} more)"
            lines.append(f"Columns ({self.total_columns}):  {col_preview}")
        if self.index_name:
            lines.append(f"Index Column:  {self.index_name}")
        if self.notes:
            lines.append(f"Note:          {self.notes}")
        if self.archive_members:
            lines.append("\nArchive Members (peeked):")
            for m in self.archive_members:
                lines.append(f"  - {m['name']} ({m.get('size_bytes', 0):,} bytes)")
        if self.preview_rows:
            lines.append(f"\nFirst {len(self.preview_rows)} Rows:")
            keys = list(self.preview_rows[0].keys())[:8]
            lines.append("  " + "\t".join(str(k) for k in keys))
            for r in self.preview_rows:
                lines.append("  " + "\t".join(str(r.get(k, "")) for k in keys))
        return "\n".join(lines)


@dataclass
class FASTASequence:
    """Structured NCBI Nucleotide or Protein FASTA sequence record."""

    accession: str
    description: str
    db: str
    length: int
    sequence: str
    fasta_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "accession": self.accession,
            "description": self.description,
            "db": self.db,
            "length": self.length,
            "sequence_preview": self.sequence[:200] + ("..." if len(self.sequence) > 200 else ""),
            "sequence": self.sequence,
        }


@dataclass
class AgentEnvelope:
    """Deterministic, context-window safe JSON envelope for CLI and MCP agent output."""

    ok: bool
    command: str
    target: str
    summary: str
    data: Any
    saved_files: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = "2.0"
    pyncbi_version: str = ""
    elapsed_ms: float | None = None
    request_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json_dumps(self.to_dict(), indent=indent)
