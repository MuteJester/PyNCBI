"""Sequencing read access for PyNCBI: resolve any GEO/SRA/BioProject accession to FASTQ files.

Uses the ENA Portal file report (mirrors every public SRA run with direct HTTPS FASTQ URLs,
byte sizes, and MD5 checksums) so reads can be fetched without the SRA Toolkit:

- `fetch_fastq_urls`: GSE / GSM / PRJNA / SRP / SRX / SRS / SAMN / SRR -> one row per FASTQ file
- `download_fastq`: parallel, MD5-verified FASTQ downloads
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import DownloadError, NoDataAvailableError
from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.tables import Table, column, from_records, is_table, iter_records
from PyNCBI.core.types import validate_accession
from PyNCBI.io.http_client import format_size_human, get_default_client
from PyNCBI.io.transfer_limits import bounded_transfer

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient

ENA_FIELDS: tuple[str, ...] = (
    "run_accession",
    "sample_accession",
    "secondary_sample_accession",
    "experiment_accession",
    "study_accession",
    "sample_alias",
    "sample_title",
    "scientific_name",
    "library_strategy",
    "library_layout",
    "instrument_model",
    "read_count",
    "base_count",
    "fastq_ftp",
    "fastq_bytes",
    "fastq_md5",
    "sra_ftp",
    "sra_bytes",
)

FASTQ_COLUMNS: tuple[str, ...] = (
    "run_accession",
    "sample_alias",
    "file_name",
    "file_url",
    "size_bytes",
    "md5",
    "read_index",
    "library_layout",
    "library_strategy",
    "instrument_model",
    "read_count",
    "base_count",
    "scientific_name",
    "sample_title",
    "sample_accession",
    "experiment_accession",
    "study_accession",
)

_ENA_DIRECT_RE = re.compile(
    r"^(SRR|ERR|DRR|SRX|ERX|DRX|SRS|ERS|DRS|SAM[NED]A?|PRJ[NED][AB])\w*\d+$", re.I
)


def _to_int(val: Any) -> int | None:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def parse_ena_file_report(rows: list[dict[str, Any]]) -> Table:
    """Expand ENA `read_run` file-report rows into one row per FASTQ file."""
    out: list[dict[str, Any]] = []
    for row in rows:
        urls = [u for u in str(row.get("fastq_ftp", "") or "").split(";") if u]
        sizes = str(row.get("fastq_bytes", "") or "").split(";")
        md5s = str(row.get("fastq_md5", "") or "").split(";")
        if not urls and row.get("sra_ftp"):
            urls = [str(row["sra_ftp"])]
            sizes = [str(row.get("sra_bytes", ""))]
            md5s = [""]
        paired = len(urls) > 1
        for i, raw_url in enumerate(urls):
            url = (
                raw_url
                if raw_url.startswith(("http://", "https://", "ftp://"))
                else f"https://{raw_url}"
            )
            url = url.replace("ftp://", "https://", 1)
            fname = url.rstrip("/").split("/")[-1]
            m = re.search(r"_(\d)\.(fastq|fq)", fname)
            read_index = int(m.group(1)) if (paired and m) else 0
            out.append(
                {
                    "run_accession": row.get("run_accession", ""),
                    "sample_alias": row.get("sample_alias", ""),
                    "file_name": fname,
                    "file_url": url,
                    "size_bytes": _to_int(sizes[i]) if i < len(sizes) else None,
                    "md5": (md5s[i].strip() if i < len(md5s) else ""),
                    "read_index": read_index,
                    "library_layout": row.get("library_layout", ""),
                    "library_strategy": row.get("library_strategy", ""),
                    "instrument_model": row.get("instrument_model", ""),
                    "read_count": _to_int(row.get("read_count")),
                    "base_count": _to_int(row.get("base_count")),
                    "scientific_name": row.get("scientific_name", ""),
                    "sample_title": row.get("sample_title", ""),
                    "sample_accession": row.get("sample_accession", ""),
                    "experiment_accession": row.get("experiment_accession", ""),
                    "study_accession": row.get("study_accession", ""),
                }
            )
    return from_records(out, FASTQ_COLUMNS)


def _ena_file_report(accession: str, client: HttpClient) -> list[dict[str, Any]]:
    url = f"{get_config().ENA_PORTAL_API_URL}/filereport"
    payload = client.get_json(
        url,
        params={
            "accession": accession,
            "result": "read_run",
            "fields": ",".join(ENA_FIELDS),
            "format": "json",
            "limit": 0,
        },
    )
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def _resolve_ena_accessions(accession: str, client: HttpClient) -> list[str]:
    """Map a GEO/SRA accession to identifiers ENA accepts for `read_run` reports."""
    acc = accession.strip().upper()
    if acc.startswith("GSE"):
        from PyNCBI.domains.geo import inspect_accession

        report = inspect_accession(
            acc, check_file_sizes=False, include_sample_breakdown=False, http_client=client
        )
        if report.bioproject_id:
            return [report.bioproject_id]
        m = re.search(r"(SRP\d+|ERP\d+|DRP\d+)", report.sra_url)
        if m:
            from PyNCBI.domains.entrez import fetch_sra_runs

            runs = fetch_sra_runs(m.group(1), limit=None, http_client=client)
            if "BioProject" in runs["columns"] and any(column(runs, "BioProject")):
                return [str(next(value for value in column(runs, "BioProject") if value))]
            if "Run" in runs["columns"]:
                return [str(r) for r in column(runs, "Run")]
        raise NoDataAvailableError(acc, "no BioProject / SRA study linked to this series")
    if acc.startswith("GSM"):
        from PyNCBI.domains.geo import inspect_accession

        report = inspect_accession(acc, check_file_sizes=False, http_client=client)
        m = re.search(
            r"(SRX\d+|ERX\d+|DRX\d+|SRS\d+|SAMN\d+)", report.sra_url + " " + report.biosample_url
        )
        if m:
            return [m.group(1)]
        raise NoDataAvailableError(acc, "no SRA experiment linked to this sample")
    if acc.startswith(("SRP", "ERP", "DRP")):
        from PyNCBI.domains.entrez import fetch_sra_runs

        runs = fetch_sra_runs(acc, limit=None, http_client=client)
        if "BioProject" in runs["columns"] and any(column(runs, "BioProject")):
            return [str(next(value for value in column(runs, "BioProject") if value))]
        return [str(r) for r in (column(runs, "Run") if "Run" in runs["columns"] else [])]
    if _ENA_DIRECT_RE.match(acc):
        return [acc]
    raise ValueError(
        f"Cannot resolve '{accession}' to sequencing runs (use GSE/GSM/PRJNA/SRP/SRX/SRS/SAMN/SRR)"
    )


def fetch_fastq_urls(
    accession: str,
    *,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
) -> Table:
    """Resolve an accession to its FASTQ files (one row per file) via the ENA mirror.

    Args:
        accession: GSE, GSM, PRJNA/PRJEB, SRP/ERP, SRX, SRS, SAMN, or SRR/ERR accession
        save_to: Optional path (.csv/.tsv/.json) to save the manifest
        http_client: Optional HTTP client

    Returns:
        Native table with `run_accession`, `sample_alias` (GSM when submitted via GEO),
        `file_name`, `file_url` (HTTPS), `size_bytes`, `md5`, `read_index`, layout, strategy,
        instrument, read/base counts, and linked sample/experiment/study accessions.
    """
    client = http_client or get_default_client()
    acc = accession.strip()
    if acc.upper().startswith(("GSE", "GSM", "SRP", "SRR", "SRX", "SRS")):
        acc = (
            validate_accession(acc)
            if acc.upper()[:3] in ("GSE", "GSM", "SRP", "SRR", "SRX", "SRS")
            else acc
        )
    with get_reporter().task(
        "ENA", "fetch_fastq_urls", f"Resolving FASTQ files for {acc}", target=acc
    ) as task:
        targets = _resolve_ena_accessions(acc, client)
        frames = []
        for target in targets:
            rows = _ena_file_report(target, client)
            if rows:
                frames.append(parse_ena_file_report(rows))
        seen = set()
        records = []
        for value in frames:
            for record in iter_records(value):
                if record["file_url"] not in seen:
                    records.append(record)
                    seen.add(record["file_url"])
        df = from_records(records, FASTQ_COLUMNS)
        if save_to is not None:
            save_data(df, save_to)
        total = sum(record.get("size_bytes") or 0 for record in records)
        task.complete(f"{len(records)} FASTQ file(s) for {acc} ({format_size_human(total)})")
        return df


def _md5_of(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.md5()  # noqa: S324 - integrity check against ENA's published MD5
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@bounded_transfer
def download_fastq(
    accession_or_manifest: str | Table,
    output_dir: str | Path | None = None,
    *,
    runs: list[str] | None = None,
    max_workers: int = 4,
    verify_md5: bool = True,
    max_download_mb: float | None = None,
    check_disk_space: bool = True,
    http_client: HttpClient | None = None,
) -> list[Path]:
    """Download FASTQ files (parallel, resumable, MD5-verified) for an accession or manifest.

    Args:
        accession_or_manifest: Accession accepted by `fetch_fastq_urls`, or its native table
        output_dir: Destination folder (default `./<accession>_fastq`)
        runs: Optional subset of run accessions (SRR...) to download
        max_workers: Parallel transfers (default 4)
        verify_md5: Verify each file against ENA's MD5 (default True)
        http_client: Optional HTTP client

    Raises:
        DownloadError: On transfer failure or MD5 mismatch (bad files are removed)
    """
    from PyNCBI.domains.geo import download_urls_parallel

    client = http_client or get_default_client()
    if is_table(accession_or_manifest):
        manifest = accession_or_manifest
        label = "fastq"
    else:
        label = str(accession_or_manifest).strip().upper()
        manifest = fetch_fastq_urls(label, http_client=client)
    if runs:
        wanted = {r.strip().upper() for r in runs}
        manifest = from_records(
            (
                record
                for record in iter_records(manifest)
                if str(record["run_accession"]).upper() in wanted
            ),
            manifest["columns"],
        )
    if not manifest["rows"]:
        raise NoDataAvailableError(label, "no FASTQ files to download")

    target = Path(output_dir) if output_dir else Path.cwd() / f"{label}_fastq"
    urls = [str(u) for u in column(manifest, "file_url")]
    paths = download_urls_parallel(client, urls, target, max_workers=max_workers)

    if verify_md5:
        expected = {record["file_name"]: record.get("md5", "") for record in iter_records(manifest)}
        mismatched: list[str] = []
        for path in paths:
            want = str(expected.get(path.name, "") or "").lower()
            if not want:
                continue
            got = _md5_of(path)
            if got != want:
                mismatched.append(f"{path.name} (expected {want}, got {got})")
                path.unlink(missing_ok=True)
        if mismatched:
            raise DownloadError(
                urls[0],
                f"MD5 verification failed for {len(mismatched)} file(s): {'; '.join(mismatched)}",
                completed_paths=[p for p in paths if p.exists()],
            )
    return paths
