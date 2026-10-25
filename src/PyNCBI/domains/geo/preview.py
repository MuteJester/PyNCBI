"""Streaming previews of remote GEO files without downloading them in full."""

from __future__ import annotations

import contextlib
import fnmatch
import io
import tarfile
import zlib
from io import StringIO
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import NoDataAvailableError
from PyNCBI.core.models import (
    FilePreview,
)
from PyNCBI.core.types import SmartFileSelector, table_read_options, validate_accession
from PyNCBI.domains.geo.resolve import _resolve_assembly_for_gse
from PyNCBI.domains.geo.urls import rnaseq_counts_url
from PyNCBI.io.http_client import (
    get_default_client,
    infer_file_type,
)
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient


def _parse_tabular_preview_lines(
    lines: list[str],
    *,
    accession: str,
    file_name: str,
    url: str,
    source: str,
    file_type: str,
    max_rows: int = 10,
    notes: str = "",
) -> FilePreview:
    """Parse header and first `max_rows` lines of text into a structured FilePreview."""
    clean_lines = [ln for ln in lines if ln.strip() and not ln.startswith("#")]
    if not clean_lines:
        return FilePreview(
            accession=accession,
            file_name=file_name,
            url=url,
            source=source,
            file_type=file_type,
            notes=notes or "No tabular lines found in preview chunk.",
        )

    sep = (
        ","
        if (".csv" in file_name.lower() or clean_lines[0].count(",") > clean_lines[0].count("\t"))
        else "\t"
    )
    sample_block = "\n".join(clean_lines[: max_rows + 1])
    try:
        df = pd.read_csv(StringIO(sample_block), sep=sep, low_memory=False, **table_read_options())
        cols = [str(c) for c in df.columns]
        index_name = cols[0] if cols else ""
        # Show up to first 8 columns per row to keep preview compact
        preview_cols = cols[:8]
        rows = [
            {str(k): v for k, v in r.items()}
            for r in df.head(max_rows)[preview_cols].to_dict(orient="records")
        ]
        return FilePreview(
            accession=accession,
            file_name=file_name,
            url=url,
            source=source,
            file_type=file_type,
            total_columns=len(cols),
            columns=cols[:50],
            index_name=index_name,
            sample_ids_preview=cols[1:21] if len(cols) > 1 else [],
            preview_rows=rows,
            notes=notes,
        )
    except Exception as exc:
        return FilePreview(
            accession=accession,
            file_name=file_name,
            url=url,
            source=source,
            file_type=file_type,
            notes=f"Raw line preview ({exc}): {clean_lines[0][:120]}",
        )


def preview_geo_file(
    accession: str,
    *,
    source: str = "auto",
    file_index: int | None = None,
    pattern: str | None = None,
    max_rows: int = 10,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> FilePreview:
    """Preview the columns, row identifiers, or archive members of a remote GEO file in <0.5s.

    Streams only the first 16–64 KB via HTTP Range / streaming without downloading
    the full file body.

    Args:
        accession: GSE, GSM, or GPL accession
        source: 'auto', 'supp', 'matrix', 'rnaseq', or 'table'
        file_index: 0-based supplementary file index
        pattern: Optional glob/substring pattern for supplementary filename
        max_rows: Number of rows to include in preview (default 10)
        http_client: Optional HTTP client
        parser: Optional SOFT parser

    Returns:
        FilePreview object with column names, index identifier type, and sample rows
    """
    accession = validate_accession(accession)
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    cfg = get_config()
    norm_source = source.lower().strip()

    # 1. GSM or GPL on-page table
    if norm_source in ("table", "on_page_table") or (
        norm_source == "auto"
        and accession.startswith(("GSM", "GPL"))
        and file_index is None
        and not pattern
    ):
        soft_text = client.get_text(cfg.get_ncbi_query_url(accession))
        has_table = True
        if accession.startswith("GSM"):
            gsm_info = soft_parser.parse_gsm(soft_text, accession)
            has_table = gsm_info.data_row_count > 0
        if has_table:
            data_url = cfg.get_ncbi_data_url(accession)
            raw_chunk = client.get_range_bytes(data_url, max_bytes=16384)
            text_chunk = raw_chunk.decode("utf-8", errors="replace")
            all_lines = text_chunk.splitlines()
            # Drop last potentially incomplete line
            if len(all_lines) > 2:
                all_lines = all_lines[:-1]
            try:
                start_idx = soft_parser.find_data_table_start("\n".join(all_lines))
                table_lines = [
                    ln
                    for ln in all_lines[start_idx:]
                    if not ln.startswith("!") and not ln.startswith("^")
                ]
            except ValueError:
                table_lines = [
                    ln for ln in all_lines if not ln.startswith("!") and not ln.startswith("^")
                ]
            return _parse_tabular_preview_lines(
                table_lines,
                accession=accession,
                file_name=f"{accession}_table.tsv",
                url=data_url,
                source="on_page_table",
                file_type="tsv",
                max_rows=max_rows,
            )
        if norm_source in ("table", "on_page_table"):
            raise NoDataAvailableError(accession)

    # 2. Series Matrix preview
    if norm_source in ("matrix", "series_matrix"):
        matrix_url = cfg.get_series_matrix_url(accession)
        fname = matrix_url.split("/")[-1]
        raw_chunk = client.get_range_bytes(matrix_url, max_bytes=65536)
        if raw_chunk[:2] == b"\x1f\x8b":
            decompressed = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw_chunk)
        else:
            decompressed = raw_chunk
        text_chunk = decompressed.decode("utf-8", errors="replace")
        all_lines = text_chunk.splitlines()
        if len(all_lines) > 2:
            all_lines = all_lines[:-1]

        matrix_lines: list[str] = []
        in_table = False
        for ln in all_lines:
            if ln.startswith("!series_matrix_table_begin"):
                in_table = True
                continue
            if ln.startswith("!series_matrix_table_end"):
                break
            if in_table:
                matrix_lines.append(ln)

        note = ""
        if len(matrix_lines) <= 1:
            note = (
                "Series Matrix contains metadata headers only (0 expression rows). "
                "For RNA-seq/sequencing studies, preview with source='rnaseq' or source='supp'."
            )
        return _parse_tabular_preview_lines(
            matrix_lines,
            accession=accession,
            file_name=fname,
            url=matrix_url,
            source="series_matrix",
            file_type="series_matrix.txt.gz",
            max_rows=max_rows,
            notes=note,
        )

    # 3. NCBI RNA-seq counts preview
    if norm_source in ("rnaseq", "rnaseq_counts", "rnaseq-counts"):
        assembly = _resolve_assembly_for_gse(accession, "auto", client, soft_parser)
        rnaseq_url = rnaseq_counts_url(accession, assembly=assembly)
        fname = rnaseq_url.split("file=")[-1]
        raw_chunk = client.get_range_bytes(rnaseq_url, max_bytes=16384)
        if raw_chunk[:2] == b"\x1f\x8b":
            decompressed = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw_chunk)
        else:
            decompressed = raw_chunk
        all_lines = decompressed.decode("utf-8", errors="replace").splitlines()
        if len(all_lines) > 2:
            all_lines = all_lines[:-1]
        return _parse_tabular_preview_lines(
            all_lines,
            accession=accession,
            file_name=fname,
            url=rnaseq_url,
            source="ncbi_rnaseq_counts",
            file_type="tsv.gz",
            max_rows=max_rows,
        )

    # 4. Supplementary file preview
    soft_text = client.get_text(cfg.get_ncbi_query_url(accession))
    if accession.startswith("GSE"):
        supp_urls = soft_parser.parse_gse(soft_text, accession).supplementary_files
    elif accession.startswith("GSM"):
        supp_urls = soft_parser.parse_gsm(soft_text, accession).supplementary_files
    else:
        gpl_s = soft_parser.parse_gpl_to_series(soft_text)
        raw_s = gpl_s.get("supplementary_file", [])
        supp_urls = [raw_s] if isinstance(raw_s, str) else list(raw_s)

    if not supp_urls:
        raise NoDataAvailableError(accession)

    if file_index is not None:
        if file_index < 0 or file_index >= len(supp_urls):
            raise ValueError(
                f"file_index {file_index} out of range (0..{len(supp_urls) - 1}) for {accession}"
            )
        target_url = supp_urls[file_index]
    elif pattern:
        pat = pattern.lower()
        matches = [
            u
            for u in supp_urls
            if fnmatch.fnmatch(u.split("/")[-1].lower(), pat) or pat in u.split("/")[-1].lower()
        ]
        if not matches:
            raise ValueError(f"No supplementary files matched pattern '{pattern}' in {accession}")
        target_url = matches[0]
    else:
        idx = SmartFileSelector().select(supp_urls, accession)
        target_url = supp_urls[idx]

    fname = target_url.split("/")[-1]
    ftype = infer_file_type(fname)

    # Handle .tar archive preview
    if fname.lower().endswith(".tar"):
        raw_chunk = client.get_range_bytes(target_url, max_bytes=65536)
        members: list[dict[str, Any]] = []
        with (
            contextlib.suppress(Exception),
            tarfile.open(fileobj=io.BytesIO(raw_chunk), mode="r:") as tf,
        ):
            for m in tf:
                members.append({"name": m.name, "size_bytes": m.size})
        return FilePreview(
            accession=accession,
            file_name=fname,
            url=target_url,
            source="supplementary",
            file_type=ftype,
            archive_members=members,
            notes="TAR archive containing sample-level supplementary files.",
        )

    raw_chunk = client.get_range_bytes(target_url, max_bytes=16384)
    if raw_chunk[:2] == b"\x1f\x8b":
        decompressed = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw_chunk)
    else:
        decompressed = raw_chunk
    all_lines = decompressed.decode("utf-8", errors="replace").splitlines()
    if len(all_lines) > 2:
        all_lines = all_lines[:-1]
    return _parse_tabular_preview_lines(
        all_lines,
        accession=accession,
        file_name=fname,
        url=target_url,
        source="supplementary",
        file_type=ftype,
        max_rows=max_rows,
    )
