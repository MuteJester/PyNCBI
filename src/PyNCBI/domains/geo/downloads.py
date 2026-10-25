"""Raw file downloads: cohort file resolution, parallel fetches, and archive unpacking."""

from __future__ import annotations

from concurrent.futures import as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import ConfigurationError, DownloadError, NoDataAvailableError
from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.types import COMPRESSED_OUTPUT_SUFFIXES, DataStatus, validate_accession
from PyNCBI.domains.geo.planning import _guard_download, _select_supp_urls
from PyNCBI.domains.geo.records import SampleState, fetch_sample_record
from PyNCBI.domains.geo.resolve import _resolve_assembly_for_gse, resolve_cohort_files
from PyNCBI.domains.geo.tables import (
    load_gds,
    load_gsm_table,
    load_rnaseq_counts,
    load_series_matrix,
)
from PyNCBI.domains.geo.transfer import _unpack_downloaded, download_urls_parallel
from PyNCBI.domains.geo.urls import rnaseq_counts_url, subseries_of
from PyNCBI.io.compression import gunzip_file
from PyNCBI.io.http_client import (
    get_default_client,
)
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor as ThreadPoolExecutor
from PyNCBI.io.transfer_limits import bounded_transfer
from PyNCBI.io.urls import download_filename
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient


@bounded_transfer
def download_many(
    accessions: list[str],
    output_dir: str | Path | None = None,
    *,
    source: str = "auto",
    file_index: int | None = None,
    pattern: str | None = None,
    unpack: bool = False,
    max_workers: int = 4,
    annotate: bool = False,
    expand_subseries: bool = False,
    max_download_mb: float | None = None,
    check_disk_space: bool = True,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> dict[str, list[Path]]:
    """Download files for several accessions concurrently (one sub-folder per accession).

    Each accession is processed by `download_geo_files` in its own worker; realm rate
    limits are still enforced globally by the shared client. All accessions are attempted
    and failures are aggregated into one `DownloadError` (with `completed_paths`).
    """
    accs = list(dict.fromkeys(validate_accession(a) for a in accessions))
    if not accs:
        return {}
    root = Path(output_dir) if output_dir else Path.cwd()
    client = http_client or get_default_client()
    workers = max(1, min(max_workers, len(accs)))
    results: dict[str, list[Path]] = {}
    failures: list[tuple[str, str]] = []

    def _one(acc: str) -> list[Path]:
        return download_geo_files(
            acc,
            root / acc,
            source=source,
            file_index=file_index,
            pattern=pattern,
            unpack=unpack,
            max_workers=max(1, max_workers // workers),
            annotate=annotate,
            expand_subseries=expand_subseries,
            max_download_mb=max_download_mb,
            check_disk_space=check_disk_space,
            http_client=client,
            parser=parser,
        )

    with (
        get_reporter().task(
            "GEO",
            "download_many",
            f"Downloading {len(accs)} accession(s) with {workers} worker(s)",
            target=",".join(accs[:5]),
        ) as task,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        futs = {pool.submit(_one, acc): acc for acc in accs}
        for done, fut in enumerate(as_completed(futs), start=1):
            acc = futs[fut]
            try:
                results[acc] = fut.result()
            except Exception as exc:  # noqa: BLE001 - aggregated below
                failures.append((acc, str(exc)))
            task.step(f"{done}/{len(accs)} accession(s) finished", current=done, total=len(accs))
        task.complete(f"Downloaded files for {len(results)}/{len(accs)} accession(s)")

    ordered = {acc: results[acc] for acc in accs if acc in results}
    if failures:
        detail = "; ".join(f"{acc}: {err.splitlines()[0]}" for acc, err in failures)
        raise DownloadError(
            failures[0][0],
            f"{len(failures)} of {len(accs)} accession(s) failed: {detail}",
            completed_paths=[p for paths in ordered.values() for p in paths],
        )
    return ordered


@bounded_transfer
def download_geo_files(
    accession: str,
    output_dir: str | Path | None = None,
    *,
    source: str = "auto",
    file_index: int | None = None,
    pattern: str | None = None,
    where: str | None = None,
    unpack: bool = False,
    annotate: bool = False,
    max_workers: int = 4,
    expand_subseries: bool = False,
    max_download_mb: float | None = None,
    check_disk_space: bool = True,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> list[Path]:
    """Download raw files or data tables for a GSE, GSM, GPL, or GDS exactly as hosted on NCBI.

    Args:
        accession: GSE, GSM, or GPL accession
        output_dir: Destination folder or file path (default: `./{accession}`)
        source: 'auto', 'supp' (supplementary), 'matrix' (Series Matrix),
                'rnaseq' (NCBI RNA-seq counts), or 'table' (on-page table)
        file_index: 0-based index of supplementary file to download
        pattern: Optional glob filter for supplementary filenames (e.g., ``*.txt.gz``)
        where: For a GSE, a cohort filter (same syntax as `extract_gse_samples`); only the
            per-sample supplementary files of matching GSMs are downloaded, in parallel,
            instead of the series-level `GSE_RAW.tar`.
        unpack: If True, safely extract `.tar` archives and gunzip `.gz` files
        annotate: For `source='rnaseq'`/`'matrix'` saved as a table file, prepend gene
            annotation columns (symbols) to the exported table
        max_workers: Parallel download workers for multi-file downloads (default 4)
        expand_subseries: For a SuperSeries (which owns no data itself), download each
            SubSeries into `<output_dir>/<SubSeries>/` instead of raising
        max_download_mb: Refuse the download if the selected files exceed this budget
            (default: `Config.max_download_mb` / `PYNCBI_MAX_DOWNLOAD_MB`, 2048 MB)
        check_disk_space: Refuse the download if it would not fit on the destination
            filesystem (default True)
        http_client: Optional HTTP client
        parser: Optional SOFT parser

    Returns:
        List of downloaded (and optionally unpacked) local file Paths
    """
    accession = validate_accession(accession)
    if (
        output_dir
        and Path(output_dir).suffix.lower() in (".parquet", ".yaml", ".yml", ".xlsx", ".feather")
        and not (Path(output_dir).suffix.lower() == ".parquet" and source.lower().strip() == "supp")
    ):
        raise ConfigurationError(
            "Export tables as JSON, CSV or TSV and convert them with your chosen library; "
            "use source='supp' to download original Parquet bytes"
        )
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    cfg = get_config()

    if accession.startswith("GDS"):
        dest_root = Path(output_dir) if output_dir else Path.cwd() / accession
        is_file = dest_root.suffix.lower() in (".csv", ".tsv", ".txt", ".json")
        out_file = dest_root if is_file else dest_root / f"{accession}_table.csv"
        load_gds(accession, save_to=out_file, http_client=client)
        return [out_file]

    if where:
        if not accession.startswith("GSE"):
            raise ValueError("`where` cohort filtering is only supported for GSE accessions")
        cohort = resolve_cohort_files(
            accession, where=where, pattern=pattern, http_client=client, parser=soft_parser
        )
        if not cohort:
            raise NoDataAvailableError(
                accession,
                detail=(
                    f"samples matched filter '{where}' but have no per-sample supplementary files"
                    + (f" matching '{pattern}'" if pattern else "")
                ),
            )
        target = Path(output_dir) if output_dir else Path.cwd() / accession
        cohort_urls = [c.url for c in cohort]
        _guard_download(
            client,
            accession,
            cohort_urls,
            target,
            max_download_mb=max_download_mb,
            check_disk_space=check_disk_space,
        )
        return download_urls_parallel(
            client, cohort_urls, target, max_workers=max_workers, unpack=unpack
        )

    dest_root = Path(output_dir) if output_dir else Path.cwd() / accession
    # A destination may name a folder to fill, a table to write, or a raw file to save as-is.
    # `.tsv.gz` has suffix `.gz`, so compressed names are matched on the whole filename.
    is_table_target = dest_root.suffix.lower() in (".csv", ".tsv", ".txt", ".json")
    is_raw_target = dest_root.name.lower().endswith((*COMPRESSED_OUTPUT_SUFFIXES, ".parquet"))
    is_file_target = is_table_target
    target_folder = dest_root.parent if (is_table_target or is_raw_target) else dest_root
    target_folder.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    norm_source = source.lower().strip()
    sample_metadata: SampleState | None = None

    # 1. On-page table for GSM / GPL
    if norm_source in ("table", "on_page_table") or (
        norm_source == "auto" and accession.startswith(("GSM", "GPL"))
    ):
        if accession.startswith("GSM"):
            sample_metadata = fetch_sample_record(accession, http_client=client, parser=soft_parser)
            if sample_metadata.status == DataStatus.DATA_ON_PAGE:
                df = load_gsm_table(accession, http_client=client, parser=soft_parser)
                out_file = dest_root if is_file_target else target_folder / f"{accession}.csv"
                save_data(df, out_file)
                saved_paths.append(out_file)
                return saved_paths
            if norm_source in ("table", "on_page_table"):
                raise NoDataAvailableError(accession)
            # Fall through to supplementary files if auto and no on-page table
        else:
            df = soft_parser.parse_gpl_table_lines(
                client.stream_lines(cfg.get_ncbi_data_url(accession)), gpl_id=accession
            )
            out_file = dest_root if is_file_target else target_folder / f"{accession}_table.csv"
            save_data(df, out_file)
            saved_paths.append(out_file)
            return saved_paths

    # 2. Series Matrix
    if norm_source in ("matrix", "series_matrix"):
        if is_file_target or annotate:
            out_file = (
                dest_root
                if is_file_target
                else target_folder / f"{accession}_series_matrix_annotated.csv"
            )
            res = load_series_matrix(
                accession, save_to=out_file, annotate=annotate, http_client=client
            )
            if res.matrix["rows"]:
                saved_paths.append(out_file)
                return saved_paths
        matrix_url = cfg.get_series_matrix_url(accession)
        _guard_download(
            client,
            accession,
            [matrix_url],
            target_folder,
            source="series_matrix",
            max_download_mb=max_download_mb,
            check_disk_space=check_disk_space,
        )
        fname = download_filename(matrix_url)
        dest_file = dest_root if is_raw_target else target_folder / fname
        client.download_file(matrix_url, dest_file)
        if unpack and dest_file.suffix == ".gz":
            unpacked = gunzip_file(dest_file, dest_file.with_suffix(""), delete_source=True)
            saved_paths.append(unpacked)
        else:
            saved_paths.append(dest_file)
        return saved_paths

    # 3. NCBI RNA-seq counts
    if norm_source in ("rnaseq", "rnaseq_counts", "rnaseq-counts"):
        if is_file_target or annotate:
            out_file = (
                dest_root
                if is_file_target
                else target_folder / f"{accession}_raw_counts_annotated.csv"
            )
            load_rnaseq_counts(
                accession,
                save_to=out_file,
                annotate=annotate,
                http_client=client,
                parser=soft_parser,
            )
            saved_paths.append(out_file)
            return saved_paths
        assembly = _resolve_assembly_for_gse(accession, "auto", client, soft_parser)
        rnaseq_url = rnaseq_counts_url(accession, assembly=assembly)
        _guard_download(
            client,
            accession,
            [rnaseq_url],
            target_folder,
            source="ncbi_rnaseq_counts",
            max_download_mb=max_download_mb,
            check_disk_space=check_disk_space,
        )
        fname = download_filename(rnaseq_url)
        dest_file = dest_root if is_raw_target else target_folder / fname
        client.download_file(rnaseq_url, dest_file)
        if unpack and dest_file.suffix == ".gz":
            unpacked = gunzip_file(dest_file, dest_file.with_suffix(""), delete_source=True)
            saved_paths.append(unpacked)
        else:
            saved_paths.append(dest_file)
        return saved_paths

    # 4. Supplementary files (default for GSE or GSM without on-page table)
    if accession.startswith("GSM"):
        sample_metadata = sample_metadata or fetch_sample_record(
            accession, http_client=client, parser=soft_parser
        )
        soft_text = ""
    else:
        soft_text = client.get_text(cfg.get_ncbi_query_url(accession))
    if accession.startswith("GSE"):
        gse_info = soft_parser.parse_gse(soft_text, accession)
        supp_urls = gse_info.supplementary_files
        subseries = subseries_of(gse_info)
        if subseries and (expand_subseries or not supp_urls):
            if not expand_subseries:
                raise NoDataAvailableError(
                    accession,
                    detail=(
                        f"{accession} is a SuperSeries with no files of its own; its data lives in "
                        f"{len(subseries)} SubSeries: {', '.join(subseries)}. Re-run on a SubSeries, or pass "
                        "expand_subseries=True (CLI: --subseries) to download all of them"
                    ),
                )
            collected: list[Path] = []
            for sub in subseries:
                collected.extend(
                    download_geo_files(
                        sub,
                        target_folder / sub,
                        source=source,
                        file_index=file_index,
                        pattern=pattern,
                        unpack=unpack,
                        max_workers=max_workers,
                        annotate=annotate,
                        expand_subseries=True,
                        max_download_mb=max_download_mb,
                        check_disk_space=check_disk_space,
                        http_client=client,
                        parser=soft_parser,
                    )
                )
            return collected
    elif accession.startswith("GSM"):
        sample_metadata = sample_metadata or fetch_sample_record(
            accession, http_client=client, parser=soft_parser
        )
        supp_urls = sample_metadata.supplementary_files
    else:
        gpl_s = soft_parser.parse_gpl_record(soft_text)
        raw_s = gpl_s.get("supplementary_file", [])
        supp_urls = [raw_s] if isinstance(raw_s, str) else list(raw_s)

    if not supp_urls:
        raise NoDataAvailableError(accession)

    selected_urls = _select_supp_urls(supp_urls, accession, file_index, pattern)
    _guard_download(
        client,
        accession,
        selected_urls,
        target_folder,
        max_download_mb=max_download_mb,
        check_disk_space=check_disk_space,
    )

    if len(selected_urls) == 1:
        url = selected_urls[0]
        # One file and an explicitly named destination: use the name the caller asked for.
        dest_file = dest_root if is_raw_target else target_folder / download_filename(url)
        client.download_file(url, dest_file)
        saved_paths.append(_unpack_downloaded(dest_file, target_folder) if unpack else dest_file)
        return saved_paths

    return download_urls_parallel(
        client, selected_urls, target_folder, max_workers=max_workers, unpack=unpack
    )
