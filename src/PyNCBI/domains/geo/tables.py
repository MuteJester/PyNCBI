"""Loading GEO data tables (Series Matrix, NCBI RNA-seq counts, GDS, supplementary)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import DataError, NoDataAvailableError, SOFTParseError
from PyNCBI.core.files import file_name
from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.tables import Table, table
from PyNCBI.core.types import (
    DataStatus,
    is_tabular_file,
    validate_accession,
)
from PyNCBI.domains.geo.resolve import _resolve_assembly_for_gse
from PyNCBI.domains.geo.transfer import decompress_table_bytes
from PyNCBI.domains.geo.urls import rnaseq_counts_url
from PyNCBI.io.http_client import (
    get_default_client,
)
from PyNCBI.io.transfer_limits import bounded_transfer
from PyNCBI.parsing.gds_parser import GDSResult, parse_gds_soft
from PyNCBI.parsing.matrix_parser import SeriesMatrixParser, SeriesMatrixResult
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser
from PyNCBI.parsing.tabular import parse_delimited_table, read_delimited_table

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PyNCBI.core.protocols import HttpClient
    from PyNCBI.domains.geo.records import SampleState


@bounded_transfer
def load_gds(
    gds_id: str,
    *,
    full: bool = False,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
) -> GDSResult:
    """Download and parse a GEO DataSet (GDS) value table with its experimental subsets.

    Args:
        gds_id: GDS accession (e.g., 'GDS1234')
        full: Fetch the `_full.soft.gz` variant (includes extra platform annotation columns)
        save_to: Optional path to save the value table (.csv, .tsv, .json)
        http_client: Optional HTTP client

    Returns:
        `GDSResult` with `.table` (ID_REF x [IDENTIFIER, GSM...]), `.subsets`,
        `.sample_groups`, and `.info`.
    """
    gds_id = validate_accession(gds_id, "GDS")
    client = http_client or get_default_client()
    cfg = get_config()
    with get_reporter().task(
        "GEO", "load_gds", f"Loading GEO DataSet {gds_id}", target=gds_id
    ) as task:
        lines = client.stream_lines(cfg.get_gds_soft_url(gds_id, full=full))
        try:
            result = parse_gds_soft(lines, gds_id=gds_id)
        finally:
            close = getattr(lines, "close", None)
            if callable(close):
                close()
        if save_to is not None and result.table["rows"]:
            save_data(result.table, save_to)
            task.step(f"Saved GDS table to {save_to}")
        rows, cols = len(result.table["rows"]), len(result.table["columns"])
        task.complete(
            f"{gds_id}: {rows:,} features x {cols - 1 if cols else 0} samples, {len(result.subsets)} subset(s)"
        )
        return result


@bounded_transfer
def load_series_matrix(
    gse_id: str,
    *,
    platform_id: str | None = None,
    annotate: bool = False,
    annotation_columns: tuple[str, ...] | list[str] | None = None,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
) -> SeriesMatrixResult:
    """Download and parse a GEO Series Matrix (`GSE*_series_matrix.txt.gz`) file.

    Args:
        gse_id: GSE accession
        platform_id: Platform (GPL) for multi-platform series
        annotate: If True, prepend probe→gene annotation columns (e.g. `Gene Symbol`,
            `ENTREZ_GENE_ID`) from the platform's GPL table to `matrix`
        annotation_columns: Override which GPL columns to join
        save_to: Optional path to save `matrix`
        http_client: Optional HTTP client

    Returns:
        SeriesMatrixResult containing `.matrix`, `.sample_table`, `.characteristics_table`, and `.series_info`.
    """
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    cfg = get_config()

    with get_reporter().task(
        "GEO", "load_series_matrix", f"Loading Series Matrix for {gse_id}", target=gse_id
    ) as task:
        url = cfg.get_series_matrix_url(gse_id, platform_id=platform_id)
        text = decompress_table_bytes(client.get_bytes(url), context=f"{gse_id} Series Matrix")
        result = SeriesMatrixParser().parse(text, gse_id=gse_id)
        if not result.matrix["rows"] and not result.sample_table["rows"] and not result.series_info:
            # Nothing at all came back. A real Series Matrix always carries the series
            # header, even when the study has no processed table, so an agent must be able
            # to tell "no data in this study" from "the body was empty or truncated".
            raise NoDataAvailableError(
                gse_id,
                detail=(
                    f"the Series Matrix at {url.rsplit('/', 1)[-1]} contained no series "
                    "header, no samples and no table"
                ),
                hint=(
                    "The file may be empty or the transfer truncated; retry, or run "
                    f"`pyncbi inspect {gse_id}` to see which files the study actually has."
                ),
            )

        if annotate and result.matrix["rows"]:
            from PyNCBI.domains.annotation import annotate_probes

            plat = platform_id
            if not plat:
                raw_plat = result.series_info.get("platform_id", "")
                plat = raw_plat[0] if isinstance(raw_plat, list) else str(raw_plat)
            if plat:
                task.step(f"Annotating {len(result.matrix['rows']):,} probes via {plat}")
                result.matrix = annotate_probes(
                    result.matrix,
                    plat,
                    columns=tuple(annotation_columns) if annotation_columns else None,
                    http_client=client,
                )

        if save_to is not None and result.matrix["rows"]:
            save_data(result.matrix, save_to)
            task.step(f"Saved Series Matrix table to {save_to}")

        rows, cols = len(result.matrix["rows"]), len(result.matrix["columns"])
        task.complete(f"Loaded Series Matrix for {gse_id}: {rows:,} features x {cols:,} samples")
        return result


@bounded_transfer
def load_rnaseq_counts(
    gse_id: str,
    *,
    count_type: str = "raw_counts",
    assembly: str | None = "auto",
    annotate: bool = False,
    annotation_columns: tuple[str, ...] | list[str] | None = None,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> Table:
    """Download and parse NCBI-generated RNA-seq count matrices for a GEO Series.

    Args:
        gse_id: GSE accession (e.g., 'GSE152418')
        count_type: 'raw_counts', 'norm_counts_TPM', or 'norm_counts_FPKM'
        assembly: Genome assembly ('GRCh38.p13' for human, 'GRCm39' for mouse) or 'auto'
            (default) to resolve from the series organism
        annotate: If True, prepend gene annotation columns (`Symbol`, `GeneType`,
            `EnsemblGeneID`) resolved from the numeric Entrez `GeneID` index. Measurement
            columns are never altered.
        annotation_columns: Override which annotation columns to prepend
        save_to: Optional destination path to save the table (.csv, .tsv)
        http_client: Optional HTTP client
        parser: Optional SOFT parser (used only to resolve the assembly)

    Returns:
        native table with GeneID as index and GSM sample IDs as columns
    """
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()

    with get_reporter().task(
        "GEO",
        "load_rnaseq_counts",
        f"Loading NCBI RNA-seq {count_type} for {gse_id}",
        target=gse_id,
    ) as task:
        resolved_assembly = _resolve_assembly_for_gse(gse_id, assembly, client, soft_parser)
        url = rnaseq_counts_url(gse_id, assembly=resolved_assembly, count_type=count_type)
        text = decompress_table_bytes(client.get_bytes(url), context=f"{gse_id} RNA-seq counts")
        df = parse_delimited_table(text, file_name(url))

        if annotate:
            from PyNCBI.domains.annotation import annotate_gene_ids

            task.step(
                f"Annotating {len(df['rows']):,} GeneIDs with gene symbols ({resolved_assembly})"
            )
            df = annotate_gene_ids(
                df,
                assembly=resolved_assembly,
                columns=tuple(annotation_columns) if annotation_columns else None,
                http_client=client,
            )

        if save_to is not None:
            save_data(df, save_to)
            task.step(f"Saved RNA-seq count matrix to {save_to}")

        rows, cols = len(df["rows"]), len(df["columns"])
        task.complete(
            f"Loaded RNA-seq count matrix for {gse_id}: {rows:,} genes x {cols:,} samples"
        )
        return df


@bounded_transfer
def read_supplementary_table(
    url_or_path: str | Path,
    *,
    http_client: HttpClient | None = None,
) -> Table:
    """Read a tabular supplementary file (.csv, .tsv, .txt, .tab, or .gz).

    Delimited text is read straight from the response; binary formats are left to the caller.
    """
    client = http_client or get_default_client()
    path_str = str(url_or_path)

    if path_str.startswith(("http://", "https://", "ftp://")):
        raw = client.get_bytes(path_str)
        fname = file_name(path_str).lower()
        if fname.endswith(".parquet"):
            raise DataError(
                "Parquet decoding belongs to the caller; download the original file instead"
            )
        text = decompress_table_bytes(raw, context=fname or "supplementary table")
        return parse_delimited_table(text, fname)

    local_path = Path(url_or_path)
    fname = local_path.name.lower()
    if fname.endswith(".parquet"):
        raise DataError(
            "Parquet decoding belongs to the caller; download the original file instead"
        )
    return read_delimited_table(local_path, fname)


@bounded_transfer
def load_gsm_table(
    gsm_id: str,
    *,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> Table:
    """Load a GSM's on-page table, shared by objects, downloads and expression resolution."""
    gsm_id = validate_accession(gsm_id, "GSM")
    client = http_client if http_client is not None else get_default_client()
    try:
        result = (parser or get_parser()).parse_gsm_data_table(
            client.get_text(get_config().get_ncbi_data_url(gsm_id)), gsm_id
        )
    except ValueError as exc:
        raise SOFTParseError(gsm_id, str(exc)) from exc
    if not result["rows"]:
        raise NoDataAvailableError(gsm_id, detail="on-page table has no measurements")
    return result


@bounded_transfer
def load_sample_record(
    sample: SampleState,
    *,
    source: str = "auto",
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> SampleState:
    """Load sample data without hiding transport, budget or parser failures.

    Raw-only samples retain the legacy file-manifest native table, explicitly marked as
    ``source='files'`` so it cannot be assembled as a measurement matrix.
    """
    client = http_client if http_client is not None else get_default_client()
    if source not in ("auto", "table", "supp"):
        raise ValueError("sample source must be auto, table or supp")
    if source == "table" or (source == "auto" and sample.status == DataStatus.DATA_ON_PAGE):
        sample.data = load_gsm_table(sample.gsm_id, http_client=client, parser=parser)
        sample.source = "on_page_table"
        sample.source_url = get_config().get_ncbi_data_url(sample.gsm_id)
        return sample
    for url in sample.supplementary_files:
        if is_tabular_file(url):
            sample.data = read_supplementary_table(url, http_client=client)
            sample.source, sample.source_url = "supplementary_table", url
            return sample
    if sample.supplementary_files and source == "auto":
        sample.data = table(
            ["file", "url"], ([file_name(url), url] for url in sample.supplementary_files)
        )
        sample.source = "files"
        return sample
    raise NoDataAvailableError(sample.gsm_id)


def iter_supplementary_records(
    url_or_path: str | Path,
    *,
    max_download_mb: float | None = None,
    http_client: HttpClient | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream native row dictionaries, closing network/files on exhaustion or close().

    The caller owns iteration and can wrap rows in any analysis library. This
    function never materializes the complete table.
    """
    from PyNCBI.io.transfer_limits import transfer_scope
    from PyNCBI.parsing.tabular import iter_delimited_records, open_table_text

    client = http_client or get_default_client()
    name = file_name(str(url_or_path))
    if name.lower().endswith(".parquet"):
        raise DataError("Download Parquet bytes and decode them with your chosen library")
    if str(url_or_path).startswith(("http://", "https://", "ftp://")):
        config = getattr(client, "app_config", None) or get_config()
        limit = config.max_download_mb if max_download_mb is None else max_download_mb
        with transfer_scope(limit):
            lines = client.stream_lines(str(url_or_path))
            try:
                yield from iter_delimited_records((line + "\n" for line in lines), name)
            finally:
                close = getattr(lines, "close", None)
                if callable(close):
                    close()
    else:
        with open_table_text(Path(url_or_path)) as source:
            yield from iter_delimited_records(source, name)
