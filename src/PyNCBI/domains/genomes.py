"""Genome & assembly access via the NCBI Datasets v2 API (no extra dependencies).

- `search_assemblies(taxon)`: reference (or all) assemblies for an organism / TaxID
- `fetch_assembly(accession)`: one ``GCF_``/``GCA_`` accession report
- `download_genome(accession, include=(...))`: genome FASTA / GFF3 / GTF / proteins package,
  unpacked safely into a flat folder
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import AccessionNotFoundError
from PyNCBI.core.models import AssemblyRecord
from PyNCBI.core.reporter import get_reporter
from PyNCBI.io.compression import extract_zipfile
from PyNCBI.io.http_client import get_default_client
from PyNCBI.io.transfer_limits import bounded_transfer

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient

INCLUDE_TYPES: dict[str, str] = {
    "genome": "GENOME_FASTA",
    "gff3": "GENOME_GFF",
    "gtf": "GENOME_GTF",
    "gbff": "GENOME_GBFF",
    "rna": "RNA_FASTA",
    "protein": "PROT_FASTA",
    "cds": "CDS_FASTA",
    "seq-report": "SEQUENCE_REPORT",
}


@dataclass
class GenomeDownloadResult:
    """Outcome of `download_genome`."""

    accession: str
    output_dir: Path
    files: list[Path] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    estimated_size_mb: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accession": self.accession,
            "output_dir": str(self.output_dir),
            "files": [str(p) for p in self.files],
            "include": self.include,
            "estimated_size_mb": self.estimated_size_mb,
        }


def _datasets(path: str) -> str:
    return f"{get_config().NCBI_DATASETS_BASE_URL}/{path}"


def _to_int(val: Any) -> int:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return 0


def parse_assembly_report(report: dict[str, Any]) -> AssemblyRecord:
    """Flatten one NCBI Datasets genome `dataset_report` entry."""
    org = report.get("organism") or {}
    info = report.get("assembly_info") or {}
    stats = report.get("assembly_stats") or {}
    annot = report.get("annotation_info") or {}
    gene_counts = (
        ((annot.get("stats") or {}).get("gene_counts") or {}) if isinstance(annot, dict) else {}
    )
    source = str(report.get("source_database", "")).replace("SOURCE_DATABASE_", "").lower()
    strain = (
        ((org.get("infraspecific_names") or {}).get("strain") or "")
        if isinstance(org, dict)
        else ""
    )
    gc = stats.get("gc_percent")
    return AssemblyRecord(
        accession=str(report.get("accession") or report.get("current_accession") or ""),
        paired_accession=str(report.get("paired_accession", "")),
        organism=str(org.get("organism_name", "")),
        taxid=str(org.get("tax_id", "")),
        strain=str(strain),
        assembly_name=str(info.get("assembly_name", "")),
        assembly_level=str(info.get("assembly_level", "")),
        assembly_status=str(info.get("assembly_status", "")),
        assembly_type=str(info.get("assembly_type", "")),
        refseq_category=str(info.get("refseq_category", "")),
        source_db=source,
        release_date=str(info.get("release_date", "")),
        submitter=str(info.get("submitter", "")),
        bioproject=str(info.get("bioproject_accession", "")),
        biosample=str((info.get("biosample") or {}).get("accession", ""))
        if isinstance(info.get("biosample"), dict)
        else "",
        total_length=_to_int(stats.get("total_sequence_length")),
        contig_n50=_to_int(stats.get("contig_n50")),
        scaffold_n50=_to_int(stats.get("scaffold_n50")),
        chromosome_count=_to_int(stats.get("total_number_of_chromosomes")),
        gc_percent=float(gc) if gc not in (None, "") else None,
        annotation_name=str(annot.get("name", "")),
        annotation_date=str(annot.get("release_date", "")),
        gene_count=_to_int(gene_counts.get("total")),
        protein_coding_count=_to_int(gene_counts.get("protein_coding")),
    )


def search_assemblies(
    taxon: str,
    *,
    reference_only: bool = True,
    limit: int = 20,
    http_client: HttpClient | None = None,
) -> list[AssemblyRecord]:
    """List genome assemblies for an organism name or TaxID (reference assemblies by default)."""
    client = http_client or get_default_client()
    params: dict[str, Any] = {"page_size": max(1, min(limit, 1000))}
    if reference_only:
        params["filters.reference_only"] = "true"
    payload = client.get_json(
        _datasets(f"genome/taxon/{quote(taxon.strip())}/dataset_report"), params=params
    )
    reports = payload.get("reports", []) or []
    return [parse_assembly_report(r) for r in reports[:limit] if isinstance(r, dict)]


def fetch_assembly(accession: str, *, http_client: HttpClient | None = None) -> AssemblyRecord:
    """Fetch the report for one genome assembly accession (``GCF_...`` or ``GCA_...``)."""
    client = http_client or get_default_client()
    acc = accession.strip()
    payload = client.get_json(_datasets(f"genome/accession/{acc}/dataset_report"))
    reports = payload.get("reports", []) or []
    if not reports:
        raise AccessionNotFoundError(acc, "no assembly report returned by NCBI Datasets")
    return parse_assembly_report(reports[0])


@bounded_transfer
def download_genome(
    accession: str,
    output_dir: str | Path | None = None,
    *,
    include: tuple[str, ...] | list[str] = ("genome",),
    unpack: bool = True,
    max_download_mb: float | None = None,
    check_disk_space: bool = True,
    http_client: HttpClient | None = None,
) -> GenomeDownloadResult:
    """Download a genome data package (FASTA/GFF3/GTF/protein...) and unpack it safely.

    Args:
        accession: Assembly accession (``GCF_...`` or ``GCA_...``)
        output_dir: Destination folder (default `./<accession>`)
        include: Any of 'genome', 'gff3', 'gtf', 'gbff', 'rna', 'protein', 'cds', 'seq-report'
        unpack: Extract the zip and flatten the data files into `output_dir` (default True)
        http_client: Optional HTTP client
    """
    client = http_client or get_default_client()
    acc = accession.strip()
    kinds = [k.strip().lower() for k in include if k.strip()]
    unknown = [k for k in kinds if k not in INCLUDE_TYPES]
    if unknown:
        raise ValueError(f"Unknown include type(s) {unknown}; choose from {sorted(INCLUDE_TYPES)}")
    annotation_types = ",".join(INCLUDE_TYPES[k] for k in kinds)
    out = Path(output_dir) if output_dir else Path.cwd() / acc
    out.mkdir(parents=True, exist_ok=True)
    reporter = get_reporter()

    with reporter.task(
        "DATASETS",
        "download_genome",
        f"Downloading genome package {acc} ({', '.join(kinds)})",
        target=acc,
    ) as task:
        est: int | None = None
        try:
            summary = client.get_json(
                _datasets(f"genome/accession/{acc}/download_summary"),
                params={"include_annotation_type": annotation_types},
            )
            est = (summary.get("hydrated") or {}).get("estimated_file_size_mb")
            if est is not None:
                task.step(f"Estimated package size: {est} MB")
        except Exception:  # noqa: BLE001 - size estimate is best effort
            est = None

        zip_path = out / f"{acc}.zip"
        url = (
            _datasets(f"genome/accession/{acc}/download")
            + f"?include_annotation_type={quote(annotation_types)}&hydrated=FULLY_HYDRATED"
        )
        client.download_file(url, zip_path)
        result = GenomeDownloadResult(
            accession=acc,
            output_dir=out,
            include=kinds,
            estimated_size_mb=int(est) if est is not None else None,
        )
        if not unpack:
            result.files = [zip_path]
            task.complete(f"Saved {zip_path}")
            return result

        tmp_dir = Path(tempfile.mkdtemp(prefix="pyncbi_genome_", dir=out))
        try:
            extract_zipfile(zip_path, tmp_dir)
            data_root = tmp_dir / "ncbi_dataset" / "data"
            moved: list[Path] = []
            if data_root.exists():
                for src in sorted(data_root.rglob("*")):
                    if src.is_file():
                        dest = out / src.name
                        if dest.exists():
                            dest.unlink()
                        shutil.move(str(src), str(dest))
                        moved.append(dest)
            result.files = moved
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)
        task.complete(f"Unpacked {len(result.files)} file(s) for {acc} into {out}")
        return result
