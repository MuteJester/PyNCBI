"""Analysis-ready expression matrix resolver for PyNCBI.

`load_expression` answers the question agents actually ask first ("get me the matrix and the
sample sheet for this study") by trying every place GEO keeps measurements, in priority order,
and reporting which source it used and why:

1. ``ncbi_rnaseq_counts``      NCBI-generated count table (sequencing series)
2. ``series_matrix``           GEO Series Matrix (arrays; often empty for sequencing)
3. ``supplementary_table``     a tabular series-level supplementary file
4. ``per_sample_supplementary`` one tabular file per GSM, joined on their feature index
5. ``on_page_tables``          the GSM on-page ``ID_REF``/``VALUE`` tables, joined
6. ``tar_members``             per-sample tables inside a series ``*_RAW.tar``

Columns from supplementary files are mapped back to GSM IDs (by accession, sample title, or
another identifying sample field) so the matrix always lines up with the sample sheet.
Measurements are never transformed.
"""

from __future__ import annotations

import gzip
import io
import tarfile
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import DataError, HTTPError, NetworkError, NoDataAvailableError
from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.types import TABULAR_EXTENSIONS, table_read_options, validate_accession
from PyNCBI.domains.geo.matrices import (
    _GSM_RE,
    _value_series,
)
from PyNCBI.domains.geo.matrices import VALUE_COLUMN_HINTS as VALUE_COLUMN_HINTS
from PyNCBI.domains.geo.matrices import (
    _normalize_label as _normalize_label,
)
from PyNCBI.domains.geo.matrices import (
    map_columns_to_gsms as map_columns_to_gsms,
)
from PyNCBI.io.http_client import format_size_human, get_default_client
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor as ThreadPoolExecutor
from PyNCBI.io.transfer_limits import bounded_transfer
from PyNCBI.io.urls import supplementary_urls
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient
    from PyNCBI.domains.geo.records import SeriesState

MATRIX_NAME_HINTS: tuple[str, ...] = (
    "count",
    "matrix",
    "expression",
    "expr",
    "tpm",
    "fpkm",
    "rpkm",
    "cpm",
    "norm",
    "beta",
    "rma",
    "fpkm_matrix",
    "readcount",
)
SOURCE_ORDER: tuple[str, ...] = (
    "ncbi_rnaseq_counts",
    "series_matrix",
    "supplementary_table",
    "per_sample_supplementary",
    "on_page_tables",
    "tar_members",
)
_PREFER_ALIASES: dict[str, str] = {
    "auto": "auto",
    "rnaseq": "ncbi_rnaseq_counts",
    "counts": "ncbi_rnaseq_counts",
    "rnaseq_counts": "ncbi_rnaseq_counts",
    "matrix": "series_matrix",
    "series_matrix": "series_matrix",
    "supp": "supplementary_table",
    "supplementary": "supplementary_table",
    "supplementary_table": "supplementary_table",
    "per_sample": "per_sample_supplementary",
    "per-sample": "per_sample_supplementary",
    "table": "on_page_tables",
    "on_page": "on_page_tables",
    "on_page_tables": "on_page_tables",
    "tar": "tar_members",
    "tar_members": "tar_members",
}
DEFAULT_MAX_DOWNLOAD_MB = 2048.0


@dataclass
class ExpressionResult:
    """An analysis-ready expression matrix plus how it was obtained."""

    gse_id: str
    source: str
    matrix_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    samples_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    source_file: str = ""
    source_url: str = ""
    column_mapping: dict[str, str] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    saved_path: Path | None = None

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.matrix_df.shape[0]), int(self.matrix_df.shape[1]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "gse_id": self.gse_id,
            "source": self.source,
            "source_file": self.source_file,
            "source_url": self.source_url,
            "shape": [self.shape[0], self.shape[1]],
            "feature_index": str(self.matrix_df.index.name or "feature"),
            "features_preview": [str(i) for i in self.matrix_df.index[:5]],
            "samples": [str(c) for c in self.matrix_df.columns],
            "column_mapping": dict(self.column_mapping),
            "candidates": [dict(c) for c in self.candidates],
            "notes": list(self.notes),
            "saved_path": str(self.saved_path) if self.saved_path else None,
        }

    def to_markdown(self, *, max_rows: int = 8) -> str:
        from PyNCBI.core.formatter import format_dataframe_preview

        lines = [
            f"# Expression matrix: {self.gse_id}",
            "",
            f"- **Source:** `{self.source}`"
            + (f" (`{self.source_file}`)" if self.source_file else ""),
            f"- **Shape:** {self.shape[0]:,} features x {self.shape[1]:,} samples "
            f"(index: `{self.matrix_df.index.name or 'feature'}`)",
        ]
        if self.candidates:
            tried = ", ".join(
                f"{c['source']}{'' if c.get('available') else ' (unavailable)'}"
                for c in self.candidates
            )
            lines.append(f"- **Candidates tried:** {tried}")
        for note in self.notes:
            lines.append(f"- {note}")
        lines += [
            "",
            "## Matrix preview",
            format_dataframe_preview(self.matrix_df, mode="markdown", max_rows=max_rows),
        ]
        if not self.samples_df.empty:
            lines += [
                "",
                "## Sample sheet preview",
                format_dataframe_preview(
                    self.samples_df, mode="markdown", max_rows=max_rows, max_cols=8
                ),
            ]
        return "\n".join(lines)

    def to_text(self) -> str:
        return self.to_markdown().replace("`", "").replace("**", "").replace("# ", "")


# ---------------------------------------------------------------------------
# Column -> GSM mapping
# ---------------------------------------------------------------------------


def _decompress(raw: bytes, *, context: str) -> str:
    from PyNCBI.domains.geo import decompress_table_bytes

    return decompress_table_bytes(raw, context=context)


def _read_table_text(text: str, filename: str) -> pd.DataFrame:
    head = text[: text.find("\n")] if "\n" in text else text
    sep = (
        ","
        if (filename.lower().endswith((".csv", ".csv.gz")) or head.count(",") > head.count("\t"))
        else "\t"
    )
    df = pd.read_csv(
        StringIO(text),
        sep=sep,
        index_col=0,
        low_memory=False,
        comment="!",
        **table_read_options(),
    )
    df.index = df.index.astype(str)
    return df


def _tabular_name(url: str) -> bool:
    name = url.split("?")[0].rstrip("/").split("/")[-1].lower()
    return name.endswith(TABULAR_EXTENSIONS)


def _rank_supplementary(urls: list[str]) -> list[str]:
    def score(url: str) -> tuple[int, int]:
        name = url.split("/")[-1].lower()
        hint = 0 if any(h in name for h in MATRIX_NAME_HINTS) else 1
        return (hint, len(name))

    return sorted((u for u in urls if _tabular_name(u)), key=score)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@bounded_transfer
def load_expression(
    gse_id: str,
    *,
    prefer: str = "auto",
    count_type: str = "raw_counts",
    annotate: bool = False,
    where: str | None = None,
    save_to: str | Path | None = None,
    max_download_mb: float | None = None,
    max_workers: int = 4,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> ExpressionResult:
    """Resolve and load the best analysis-ready expression matrix for a GEO series.

    Args:
        gse_id: GSE accession
        prefer: ``'auto'`` (default cascade) or try a source first: ``'rnaseq'``, ``'matrix'``,
            ``'supp'``, ``'per_sample'``, ``'table'``, ``'tar'``
        count_type: For NCBI counts: ``'raw_counts'``, ``'norm_counts_TPM'``, ...
        annotate: Prepend gene/probe annotation columns to the matrix
        where: Optional cohort filter restricting the sample sheet (and matrix columns)
        save_to: Optional path for the matrix; the sample sheet is written next to it as
            ``<stem>_samples.csv``
        max_download_mb: Refuse candidates larger than this (default 2048 MB)
        max_workers: Parallel workers for per-sample sources
        http_client: Optional HTTP client
        parser: Optional SOFT parser

    Returns:
        `ExpressionResult` with ``matrix_df`` (features x GSM), the aligned ``samples_df``,
        the ``source`` used, every ``candidate`` considered, and ``notes``.

    Raises:
        DataError: A candidate exceeds ``max_download_mb``
        NoDataAvailableError: No source yielded a usable matrix
    """

    return _resolve_expression(
        gse_id,
        prefer=prefer,
        count_type=count_type,
        annotate=annotate,
        where=where,
        save_to=save_to,
        max_download_mb=max_download_mb,
        max_workers=max_workers,
        http_client=http_client,
        parser=parser,
    )


@bounded_transfer
def _resolve_expression(
    gse_id: str,
    *,
    prefer: str = "auto",
    count_type: str = "raw_counts",
    annotate: bool = False,
    where: str | None = None,
    save_to: str | Path | None = None,
    max_download_mb: float | None = None,
    max_workers: int = 4,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
    metadata: SeriesState | None = None,
) -> ExpressionResult:
    """Resolve a matrix, optionally reusing already acquired study metadata."""
    from PyNCBI.domains.geo import extract_gse_samples, load_series_matrix, rnaseq_counts_url
    from PyNCBI.domains.geo.urls import resolve_rnaseq_assembly

    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    cfg = get_config()
    reporter = get_reporter()
    max_download_mb = cfg.max_download_mb if max_download_mb is None else max_download_mb
    max_bytes = int(max_download_mb * 1024 * 1024) if max_download_mb else None

    with reporter.task(
        "GEO", "load_expression", f"Resolving expression matrix for {gse_id}", target=gse_id
    ) as task:
        if metadata is None:
            from PyNCBI.domains.geo.records import fetch_series_metadata

            gse_info = fetch_series_metadata(gse_id, http_client=client, parser=soft_parser).details
            samples_df, _char_df, _ = extract_gse_samples(gse_id, where=where, http_client=client)
        else:
            gse_info = metadata.details
            samples_df = metadata.samples_df
            if where:
                from PyNCBI.domains.geo.samples import _apply_sample_filter

                samples_df = _apply_sample_filter(samples_df, where)
        is_seq = any("sequencing" in t.lower() for t in gse_info.experiment_types)

        order = (
            list(SOURCE_ORDER)
            if is_seq
            else [s for s in SOURCE_ORDER if s != "ncbi_rnaseq_counts"] + ["ncbi_rnaseq_counts"]
        )
        wanted = _PREFER_ALIASES.get(prefer.strip().lower())
        if wanted is None:
            raise ValueError(
                f"Unknown prefer='{prefer}'; choose from {sorted(set(_PREFER_ALIASES))}"
            )
        if wanted != "auto":
            order = [wanted] + [s for s in order if s != wanted]

        candidates: list[dict[str, Any]] = []
        notes: list[str] = []

        def _guard(url: str, source: str, name: str) -> dict[str, Any]:
            info = client.inspect_url(url, source=source)
            entry = {
                "source": source,
                "name": name,
                "url": url,
                "size_bytes": info.get("size_bytes"),
                "size_human": info.get("size_human", "unknown"),
                "available": bool(info.get("exists")),
            }
            size = info.get("size_bytes")
            if (
                entry["available"]
                and isinstance(size, int)
                and max_bytes is not None
                and size > max_bytes
            ):
                entry["note"] = f"exceeds max_download_mb={max_download_mb}"
                candidates.append(entry)
                raise DataError(
                    f"{name} is {format_size_human(size)}, which exceeds max_download_mb={max_download_mb}",
                    hint="Raise max_download_mb=, choose another prefer= source, or stream the file with "
                    "`pyncbi download` / `preview_geo_file`",
                )
            return entry

        for source in order:
            try:
                if source == "ncbi_rnaseq_counts":
                    assembly = resolve_rnaseq_assembly(gse_info)
                    if assembly is None:
                        raise NoDataAvailableError(
                            gse_id, detail="no NCBI RNA-seq assembly for the study organism"
                        )
                    url = rnaseq_counts_url(gse_id, assembly=assembly, count_type=count_type)
                    entry = _guard(url, source, url.split("file=")[-1])
                    candidates.append(entry)
                    if not entry["available"]:
                        continue
                    text = _decompress(client.get_bytes(url), context=f"{gse_id} RNA-seq counts")
                    matrix = pd.read_csv(
                        StringIO(text),
                        sep="\t",
                        index_col=0,
                        low_memory=False,
                        **table_read_options(),
                    )
                    matrix.index = matrix.index.astype(str)
                    matrix.index.name = matrix.index.name or "GeneID"
                    file_name, file_url = str(entry["name"]), url

                elif source == "series_matrix":
                    url = cfg.get_series_matrix_url(gse_id)
                    entry = _guard(url, source, url.split("/")[-1])
                    candidates.append(entry)
                    if not entry["available"]:
                        continue
                    try:
                        result = load_series_matrix(gse_id, http_client=client)
                    except NoDataAvailableError as exc:
                        # An empty or truncated matrix is a dead candidate, not a dead end:
                        # record why and let the cascade try the next source.
                        entry["note"] = str(exc.detail or exc)
                        continue
                    matrix = result.matrix_df
                    if matrix.empty:
                        entry["note"] = "Series Matrix carries metadata headers but no data rows"
                        continue
                    file_name, file_url = str(entry["name"]), url

                elif source == "supplementary_table":
                    ranked = _rank_supplementary(gse_info.supplementary_files)
                    if not ranked:
                        candidates.append(
                            {
                                "source": source,
                                "name": "",
                                "url": "",
                                "available": False,
                                "note": "no tabular series supplementary file",
                            }
                        )
                        continue
                    url = ranked[0]
                    entry = _guard(url, source, url.split("/")[-1])
                    candidates.append(entry)
                    if not entry["available"]:
                        continue
                    text = _decompress(
                        client.get_bytes(url), context=f"{gse_id} supplementary table"
                    )
                    matrix = _read_table_text(text, str(entry["name"]))
                    file_name, file_url = str(entry["name"]), url

                elif source == "per_sample_supplementary":
                    per_sample: list[tuple[str, str]] = []
                    for gsm_id, row in samples_df.iterrows():
                        for col in samples_df.columns:
                            if not str(col).startswith("supplementary_file"):
                                continue
                            tabular = next(
                                (u for u in supplementary_urls(row[col]) if _tabular_name(u)),
                                None,
                            )
                            if tabular is not None:
                                per_sample.append((str(gsm_id), tabular))
                    entry = {
                        "source": source,
                        "name": f"{len(per_sample)} per-sample file(s)",
                        "url": "",
                        "available": bool(per_sample),
                    }
                    candidates.append(entry)
                    if not per_sample:
                        entry["note"] = "no tabular per-sample supplementary files"
                        continue
                    task.step(f"Downloading {len(per_sample)} per-sample table(s)")

                    def _one(item: tuple[str, str]) -> pd.Series | None:
                        gsm, link = item
                        text = _decompress(client.get_bytes(link), context=f"{gsm} table")
                        return _value_series(_read_table_text(text, link.split("/")[-1]), gsm)

                    with ThreadPoolExecutor(
                        max_workers=max(1, min(max_workers, len(per_sample)))
                    ) as pool:
                        series_list = [s for s in pool.map(_one, per_sample) if s is not None]
                    if not series_list:
                        entry["note"] = "per-sample files held no readable measurement column"
                        continue
                    matrix = pd.concat(series_list, axis=1)
                    matrix.index.name = matrix.index.name or "feature"
                    notes.append(
                        f"Joined {len(series_list)} per-sample supplementary table(s) on their feature index"
                    )
                    file_name, file_url = "per-sample supplementary files", ""

                elif source == "on_page_tables":
                    with_tables = [
                        str(g)
                        for g, row in samples_df.iterrows()
                        if str(row.get("data_row_count", "0")).split("\n")[0].strip().isdigit()
                        and int(str(row.get("data_row_count", "0")).split("\n")[0].strip()) > 0
                    ]
                    entry = {
                        "source": source,
                        "name": f"{len(with_tables)} on-page table(s)",
                        "url": "",
                        "available": bool(with_tables),
                    }
                    candidates.append(entry)
                    if not with_tables:
                        entry["note"] = "no samples expose an on-page data table"
                        continue
                    task.step(f"Fetching {len(with_tables)} on-page sample table(s)")

                    def _one_table(gsm: str) -> pd.Series | None:
                        from PyNCBI.domains.geo.tables import load_gsm_table

                        df = load_gsm_table(gsm, http_client=client, parser=soft_parser)
                        if df.empty:
                            return None
                        if gsm in df.columns:
                            series = df[gsm].copy()
                            series.name = gsm
                            return series
                        return _value_series(
                            df.drop(columns=[c for c in ("probe",) if c in df.columns]), gsm
                        )

                    with ThreadPoolExecutor(
                        max_workers=max(1, min(max_workers, len(with_tables)))
                    ) as pool:
                        series_list = [
                            s for s in pool.map(_one_table, with_tables) if s is not None
                        ]
                    if not series_list:
                        entry["note"] = "on-page tables held no readable values"
                        continue
                    matrix = pd.concat(series_list, axis=1)
                    matrix.index.name = matrix.index.name or "probe"
                    notes.append(f"Joined {len(series_list)} on-page sample table(s)")
                    file_name, file_url = "on-page sample tables", ""

                else:  # tar_members
                    tars = [u for u in gse_info.supplementary_files if u.lower().endswith(".tar")]
                    if not tars:
                        candidates.append(
                            {
                                "source": source,
                                "name": "",
                                "url": "",
                                "available": False,
                                "note": "no series .tar archive",
                            }
                        )
                        continue
                    url = tars[0]
                    entry = _guard(url, source, url.split("/")[-1])
                    candidates.append(entry)
                    if not entry["available"]:
                        continue
                    task.step(f"Reading per-sample tables from {entry['name']}")
                    archive = client.get_bytes(url)
                    series_list = []
                    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tf:
                        for member in tf.getmembers():
                            if not member.isfile() or not _tabular_name(member.name):
                                continue
                            found = _GSM_RE.search(member.name)
                            if not found:
                                continue
                            handle = tf.extractfile(member)
                            if handle is None:
                                continue
                            raw = handle.read()
                            if raw[:2] == b"\x1f\x8b":
                                raw = gzip.decompress(raw)
                            series = _value_series(
                                _read_table_text(
                                    raw.decode("utf-8", errors="replace"), member.name
                                ),
                                found.group(0).upper(),
                            )
                            if series is not None:
                                series_list.append(series)
                    if not series_list:
                        entry["note"] = "archive held no per-sample tabular members"
                        continue
                    matrix = pd.concat(series_list, axis=1)
                    matrix.index.name = matrix.index.name or "feature"
                    notes.append(
                        f"Extracted {len(series_list)} per-sample table(s) from {entry['name']}"
                    )
                    file_name, file_url = str(entry["name"]), url

            except NoDataAvailableError as exc:
                candidates.append(
                    {
                        "source": source,
                        "name": "",
                        "url": "",
                        "available": False,
                        "note": str(exc).splitlines()[0],
                    }
                )
                continue
            except DataError:
                raise
            except NetworkError as exc:
                if isinstance(exc, HTTPError) and exc.status_code in (404, 410):
                    candidates.append(
                        {
                            "source": source,
                            "name": "",
                            "url": "",
                            "available": False,
                            "note": str(exc).splitlines()[0],
                        }
                    )
                    continue
                raise
            except Exception as exc:  # noqa: BLE001 - a failing candidate must not end the cascade
                if candidates and candidates[-1].get("source") == source:
                    candidates[-1]["note"] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                else:
                    candidates.append(
                        {
                            "source": source,
                            "name": "",
                            "url": "",
                            "available": False,
                            "note": f"{type(exc).__name__}: {str(exc).splitlines()[0]}",
                        }
                    )
                continue

            if matrix.empty:
                continue

            mapping, map_notes = map_columns_to_gsms([str(c) for c in matrix.columns], samples_df)
            if mapping:
                # Only columns that belong to a sample of this series are kept
                notes.extend(map_notes)
                matrix = matrix[[c for c in matrix.columns if str(c) in mapping]]
                matrix.columns = [mapping[str(c)] for c in matrix.columns]
                ordered = [g for g in samples_df.index if g in set(matrix.columns)]
                if ordered:
                    matrix = matrix[ordered]
            else:
                # Nothing matched: keep the published columns rather than discard the matrix
                notes.extend(n for n in map_notes if "unmapped columns" not in n.lower())
                if not any("kept as published" in n.lower() for n in notes):
                    notes.append(
                        "No matrix column matched a sample of this series; columns kept as published"
                    )
            if matrix.shape[1] == 0:
                if candidates and candidates[-1].get("source") == source:
                    candidates[-1]["note"] = "no column could be matched to a sample of this series"
                continue

            aligned = samples_df.loc[[g for g in matrix.columns if g in samples_df.index]]
            result_obj = ExpressionResult(
                gse_id=gse_id,
                source=source,
                matrix_df=matrix,
                samples_df=aligned if not aligned.empty else samples_df,
                source_file=file_name,
                source_url=file_url,
                column_mapping=mapping,
                candidates=candidates,
                notes=notes,
            )

            if annotate:
                try:
                    from PyNCBI.domains.annotation import (
                        annotate_gene_ids,
                        annotate_probes,
                        looks_like_gene_id_index,
                    )

                    if looks_like_gene_id_index(matrix.index):
                        assembly = resolve_rnaseq_assembly(gse_info)
                        if assembly is None:
                            raise NoDataAvailableError(
                                gse_id, detail="no NCBI RNA-seq assembly for the study organism"
                            )
                        result_obj.matrix_df = annotate_gene_ids(
                            matrix, assembly=assembly, http_client=client
                        )
                        notes.append("Prepended gene annotation columns (Entrez GeneID index)")
                    elif gse_info.platforms:
                        result_obj.matrix_df = annotate_probes(
                            matrix, gse_info.platforms[0], http_client=client
                        )
                        notes.append(
                            f"Prepended probe annotation columns from {gse_info.platforms[0]}"
                        )
                except Exception as exc:  # noqa: BLE001 - annotation must not lose the matrix
                    notes.append(
                        f"Annotation skipped ({type(exc).__name__}: {str(exc).splitlines()[0]})"
                    )

            if save_to is not None:
                dest = Path(save_to)
                result_obj.saved_path = save_data(result_obj.matrix_df, dest)
                sheet = dest.with_name(f"{dest.stem}_samples.csv")
                save_data(result_obj.samples_df, sheet)
                notes.append(f"Saved matrix to {result_obj.saved_path} and sample sheet to {sheet}")

            rows, cols = result_obj.shape
            task.complete(f"{gse_id}: {rows:,} features x {cols:,} samples from {source}")
            return result_obj

        tried = "; ".join(
            f"{c['source']}" + (f" ({c['note']})" if c.get("note") else "") for c in candidates
        )
        raise NoDataAvailableError(
            gse_id,
            detail=f"no analysis-ready expression matrix found. Candidates tried: {tried or 'none'}",
        )
