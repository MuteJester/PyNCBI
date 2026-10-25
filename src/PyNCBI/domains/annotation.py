"""Gene & Platform Annotation Engine for PyNCBI.

Maps the row identifiers of NCBI/GEO data tables to gene-level annotation without touching
any measurement values:

- `fetch_gpl_table`: Stream a GPL platform annotation table (probe -> gene columns) with
  full column preservation, optional column subset / row limit, and local caching.
- `load_rnaseq_annotation`: Load NCBI's RNA-seq gene annotation table (Entrez `GeneID` ->
  `Symbol`, `GeneType`, `EnsemblGeneID`, ...) for an assembly, with a resilient source
  cascade: local cache -> GEO annotation file -> NCBI Datasets API -> Entrez Gene esummary.
- `annotate_gene_ids`: Prepend gene annotation columns to a GeneID-indexed count matrix.
- `annotate_probes`: Prepend probe -> gene columns (from the GPL) to a probe-indexed matrix.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import (
    BotChallengeError,
    DataError,
    NoDataAvailableError,
    PyNCBIError,
)
from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.types import validate_accession
from PyNCBI.io.http_client import get_default_client
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor as ThreadPoolExecutor
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from PyNCBI.core.protocols import HttpClient

# NCBI-generated RNA-seq gene annotation files (shared across all GEO series of an organism)
RNASEQ_ANNOTATION_FILES: dict[str, str] = {
    "GRCh38.p13": "Human.GRCh38.p13.annot.tsv.gz",
    "GRCm39": "Mouse.GRCm39.annot.tsv.gz",
}
RNASEQ_ANNOTATION_TAXID: dict[str, str] = {"GRCh38.p13": "9606", "GRCm39": "10090"}

# Canonical annotation columns (NCBI's annot.tsv naming), used by every source
ANNOTATION_TABLE_COLUMNS: tuple[str, ...] = (
    "Symbol",
    "Description",
    "GeneType",
    "EnsemblGeneID",
    "Chromosome",
    "Synonyms",
    "source",
)
DEFAULT_GENE_ANNOTATION_COLUMNS: tuple[str, ...] = ("Symbol", "GeneType", "EnsemblGeneID")

# Column-name candidates, in priority order, for probe -> gene columns on GPL tables
_SYMBOL_COLUMN_CANDIDATES: tuple[str, ...] = (
    "Gene Symbol",
    "GENE_SYMBOL",
    "gene_symbol",
    "GeneSymbol",
    "Symbol",
    "SYMBOL",
    "Gene symbol",
    "UCSC_RefGene_Name",
    "ILMN_Gene",
    "GENE",
    "Gene",
    "gene_assignment",
    "Gene_Symbol",
)
_ENTREZ_COLUMN_CANDIDATES: tuple[str, ...] = (
    "ENTREZ_GENE_ID",
    "Entrez_Gene_ID",
    "Entrez Gene",
    "ENTREZ_ID",
    "Entrez_Gene",
    "GENE_ID",
    "Gene ID",
    "GeneID",
    "Gene_ID",
    "LocusLink",
)

_DATASETS_BATCH_SIZE = 1000
_ESUMMARY_BATCH_SIZE = 400


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _annotation_cache_dir() -> Path:
    folder = Path(get_config().cache_folder) / "annotations"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read_cached_table(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(
            path, sep="\t", index_col=0, dtype=str, keep_default_na=False, compression="gzip"
        )
    except Exception:
        return None
    df.index = df.index.astype(str)
    return df


def _write_cached_table(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_name(path.name + ".part")
    df.to_csv(tmp, sep="\t", compression="gzip")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# GPL platform tables
# ---------------------------------------------------------------------------


def gpl_table_url(gpl_id: str) -> str:
    """URL of the full GPL data table (`acc.cgi ... view=data`), available for every platform."""
    return get_config().get_ncbi_data_url(gpl_id)


def gpl_annot_url(gpl_id: str) -> str:
    """URL of the NCBI-curated `GPLxxx.annot.gz` file (exists only for some platforms)."""
    gpl_id = gpl_id.strip().upper()
    prefix = gpl_id[:-3] + "nnn" if len(gpl_id) > 6 else "GPLnnn"
    return f"{get_config().NCBI_FTP_HTTPS_BASE}/geo/platforms/{prefix}/{gpl_id}/annot/{gpl_id}.annot.gz"


def _platform_profile(gpl_id: str, client: HttpClient) -> tuple[str, int | None]:
    """Read `technology` and the declared table row count from a platform's own record.

    Only called after the table view came back empty. GEO's `view=data` reply for a virtual
    platform is a single `^PLATFORM` line with no explanation, but the record itself says
    plainly that it is a sequencing platform with 0 table rows. The record also lists every
    referencing sample, so the stream is abandoned as soon as the two fields are seen.
    """
    technology = ""
    declared_rows: int | None = None
    lines = client.stream_lines(get_config().get_ncbi_query_url(gpl_id))
    try:
        for index, line in enumerate(lines):
            lowered = line.lower()
            if lowered.startswith("!platform_technology"):
                technology = line.partition("=")[2].strip()
            elif lowered.startswith("!platform_data_row_count"):
                with contextlib.suppress(ValueError):
                    declared_rows = int(line.partition("=")[2].strip())
            if (technology and declared_rows is not None) or index > 200:
                break
    except PyNCBIError:  # pragma: no cover - the original error is the useful one
        return technology, declared_rows
    finally:
        close = getattr(lines, "close", None)
        if callable(close):
            close()
    return technology, declared_rows


def _explain_missing_platform_table(gpl_id: str, client: HttpClient, error: DataError) -> None:
    """Re-raise a bare "no table" parse failure as a precise, actionable error."""
    technology, declared_rows = _platform_profile(gpl_id, client)
    if "sequencing" in technology.lower() or declared_rows == 0:
        raise NoDataAvailableError(
            gpl_id,
            detail=(
                f"{gpl_id} is a {technology or 'virtual'} platform and carries no probe "
                "annotation table; GEO reports 0 table rows for it"
            ),
            hint=(
                "Probe tables exist only for array platforms. For sequencing studies the gene "
                "annotation comes from the counts table instead: use annotate_gene_ids() for "
                "Entrez GeneIDs, or load_rnaseq_annotation() for the full NCBI annotation table."
            ),
        ) from error
    raise error


def fetch_gpl_table(
    gpl_id: str,
    *,
    columns: Sequence[str] | None = None,
    limit: int | None = None,
    save_to: str | Path | None = None,
    source: str = "auto",
    use_cache: bool = True,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> pd.DataFrame:
    """Fetch a GPL platform annotation table as a DataFrame indexed by probe `ID`.

    The table is streamed line-by-line from GEO so a `limit` stops the transfer early and
    an 80 MB platform never has to be held as one string. Full tables are cached locally
    (`<cache>/annotations/GPLxxxx_table.tsv.gz`) so repeated annotation is instant.

    Args:
        gpl_id: Platform accession (e.g., 'GPL570', 'GPL13534')
        columns: Optional subset of columns to return (the probe `ID` index is always kept)
        limit: Optional maximum number of probe rows to read
        save_to: Optional path (.csv, .tsv, .parquet, .json) to save the returned table
        source: 'auto'/'data' (GEO data table, universal) or 'annot' (NCBI-curated
            `GPLxxxx.annot.gz` with `Gene symbol` / `Gene ID` columns; not all platforms)
        use_cache: Read/write the local full-table cache (default True)
        http_client: Optional HTTP client
        parser: Optional SOFT parser

    Returns:
        DataFrame of strings indexed by `ID` with all (or the selected) annotation columns.
    """
    gpl_id = validate_accession(gpl_id, "GPL")
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    src = source.strip().lower()
    if src not in ("auto", "data", "annot"):
        raise ValueError("source must be 'auto', 'data', or 'annot'")

    cache_path = (
        _annotation_cache_dir() / f"{gpl_id}_{'annot' if src == 'annot' else 'table'}.tsv.gz"
    )
    reporter = get_reporter()

    with reporter.task(
        "GEO", "fetch_gpl_table", f"Fetching platform table for {gpl_id}", target=gpl_id
    ) as task:
        df: pd.DataFrame | None = None
        if use_cache:
            df = _read_cached_table(cache_path)
            if df is not None:
                task.step(f"Loaded cached {gpl_id} table ({len(df):,} probes)")

        if df is None:
            url = gpl_annot_url(gpl_id) if src == "annot" else gpl_table_url(gpl_id)
            lines = client.stream_lines(url)
            try:
                df = soft_parser.parse_gpl_table_lines(lines, limit=limit, gpl_id=gpl_id)
            except NoDataAvailableError:
                raise
            except DataError as exc:
                _explain_missing_platform_table(gpl_id, client, exc)
                raise  # pragma: no cover - the helper always raises
            finally:
                close = getattr(lines, "close", None)
                if callable(close):
                    close()
            if use_cache and limit is None:
                _write_cached_table(df, cache_path)
                task.step(f"Cached full {gpl_id} table ({len(df):,} probes)")

        if limit is not None and len(df) > limit:
            df = df.iloc[:limit]
        if columns:
            missing = [c for c in columns if c not in df.columns]
            if missing:
                raise DataError(
                    f"Column(s) {missing} not found in {gpl_id} table",
                    hint=f"Available columns: {list(df.columns)}",
                )
            df = df[list(columns)]

        if save_to is not None:
            save_data(df, save_to)
            task.step(f"Saved {gpl_id} table to {save_to}")
        task.complete(f"{gpl_id}: {len(df):,} probes x {len(df.columns)} annotation column(s)")
        return df


def detect_gene_columns(gpl_columns: Iterable[str]) -> list[str]:
    """Pick the best gene-symbol and Entrez-ID columns from a GPL table's column names."""
    cols = list(gpl_columns)
    lower_map = {c.lower(): c for c in cols}
    chosen: list[str] = []
    for candidates in (_SYMBOL_COLUMN_CANDIDATES, _ENTREZ_COLUMN_CANDIDATES):
        for cand in candidates:
            hit = lower_map.get(cand.lower())
            if hit and hit not in chosen:
                chosen.append(hit)
                break
    return chosen


def annotate_probes(
    df: pd.DataFrame,
    gpl_id: str,
    *,
    columns: Sequence[str] | None = None,
    http_client: HttpClient | None = None,
) -> pd.DataFrame:
    """Prepend probe -> gene annotation columns from a GPL table to a probe-indexed matrix.

    Args:
        df: Matrix indexed by probe IDs (e.g., `load_series_matrix(...).matrix_df`)
        gpl_id: Platform accession the probes belong to
        columns: GPL columns to join (default: auto-detected gene symbol + Entrez ID columns)
        http_client: Optional HTTP client

    Returns:
        A new DataFrame with the annotation columns first; measurement columns are unchanged.
    """
    table = fetch_gpl_table(gpl_id, http_client=http_client)
    join_cols = list(columns) if columns else detect_gene_columns(table.columns)
    if not join_cols:
        raise DataError(
            f"Could not detect a gene symbol / Entrez ID column on {gpl_id}",
            hint=f"Pass columns=[...] explicitly. Available: {list(table.columns)[:40]}",
        )
    missing = [c for c in join_cols if c not in table.columns]
    if missing:
        raise DataError(
            f"Column(s) {missing} not found in {gpl_id} table",
            hint=f"Available: {list(table.columns)[:40]}",
        )

    annot = table[join_cols].replace("", pd.NA)
    annot = annot[~annot.index.duplicated(keep="first")]
    aligned = annot.reindex(df.index.astype(str))
    aligned.index = df.index
    out = df.drop(columns=[c for c in join_cols if c in df.columns])
    return pd.concat([aligned, out], axis=1)


# ---------------------------------------------------------------------------
# NCBI RNA-seq gene annotation (Entrez GeneID -> Symbol / GeneType / Ensembl)
# ---------------------------------------------------------------------------


def rnaseq_annotation_url(assembly: str) -> str:
    """GEO download URL of the NCBI RNA-seq annotation table for an assembly."""
    fname = RNASEQ_ANNOTATION_FILES.get(assembly, assembly if assembly.endswith(".gz") else "")
    if not fname:
        raise ValueError(
            f"Unknown assembly '{assembly}'. Known: {sorted(RNASEQ_ANNOTATION_FILES)} "
            "(or pass an explicit '<Organism>.<assembly>.annot.tsv.gz' filename)"
        )
    return f"{get_config().NCBI_GEO_DOWNLOAD_URL}?type=rnaseq_counts&format=file&file={fname}"


def _normalize_annotation_frame(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Coerce any source's frame into the canonical annotation table layout."""
    out = pd.DataFrame(index=df.index.astype(str))
    out.index.name = "GeneID"
    for col in ANNOTATION_TABLE_COLUMNS:
        if col == "source":
            out[col] = source
        elif col in df.columns:
            out[col] = df[col].astype(str).fillna("")
        else:
            out[col] = ""
    extra = [c for c in df.columns if c not in out.columns and c not in ("GeneID",)]
    for col in extra:
        out[col] = df[col].astype(str).fillna("")
    return out


def _fetch_geo_annotation_file(assembly: str, client: HttpClient) -> pd.DataFrame | None:
    """Download the full NCBI annotation table from GEO; None if browser-gated/unavailable."""
    from PyNCBI.domains.geo import decompress_table_bytes

    url = rnaseq_annotation_url(assembly)
    try:
        text = decompress_table_bytes(client.get_bytes(url), context=f"{assembly} annotation table")
    except (BotChallengeError, DataError, PyNCBIError) as exc:
        get_reporter().warn(
            "GEO",
            f"GEO annotation file unavailable ({type(exc).__name__}); falling back to NCBI Datasets/Gene",
            operation="load_rnaseq_annotation",
            target=assembly,
        )
        return None
    from io import StringIO

    df = pd.read_csv(StringIO(text), sep="\t", dtype=str, keep_default_na=False, low_memory=False)
    id_col = "GeneID" if "GeneID" in df.columns else df.columns[0]
    df = df.set_index(id_col)
    if "Chromosome" not in df.columns and "ChrAcc" in df.columns:
        df["Chromosome"] = df["ChrAcc"]
    return _normalize_annotation_frame(df, "geo_annot_file")


def _fetch_datasets_gene_reports(gene_ids: list[str], client: HttpClient) -> pd.DataFrame:
    """Annotate GeneIDs via the NCBI Datasets v2 gene API (<=1000 IDs per request)."""
    base = get_config().NCBI_DATASETS_BASE_URL
    batches = [
        gene_ids[i : i + _DATASETS_BATCH_SIZE]
        for i in range(0, len(gene_ids), _DATASETS_BATCH_SIZE)
    ]

    def _one(batch: list[str]) -> list[dict[str, Any]]:
        payload = client.get_json(
            f"{base}/gene/id/{','.join(batch)}",
            params={"page_size": _DATASETS_BATCH_SIZE, "returned_content": "COMPLETE"},
        )
        rows: list[dict[str, Any]] = []
        for rep in payload.get("reports", []) or []:
            gene = rep.get("gene") if isinstance(rep, dict) else None
            if not isinstance(gene, dict) or not gene.get("gene_id"):
                continue
            rows.append(
                {
                    "GeneID": str(gene.get("gene_id")),
                    "Symbol": str(gene.get("symbol", "")),
                    "Description": str(gene.get("description", "")),
                    "GeneType": str(gene.get("type", "")).lower().replace("_", "-"),
                    "EnsemblGeneID": ",".join(gene.get("ensembl_gene_ids", []) or []),
                    "Chromosome": ",".join(gene.get("chromosomes", []) or []),
                    "Synonyms": "|".join(gene.get("synonyms", []) or []),
                }
            )
        return rows

    all_rows: list[dict[str, Any]] = []
    workers = max(1, min(3, len(batches)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows in pool.map(_one, batches):
            all_rows.extend(rows)
    if not all_rows:
        return pd.DataFrame(columns=list(ANNOTATION_TABLE_COLUMNS)).rename_axis("GeneID")
    df = pd.DataFrame(all_rows).set_index("GeneID")
    return _normalize_annotation_frame(df, "ncbi_datasets")


def _fetch_esummary_gene(gene_ids: list[str], client: HttpClient) -> pd.DataFrame:
    """Annotate GeneIDs via E-Utilities `esummary db=gene` (symbol/description/chromosome)."""
    cfg = get_config()
    rows: list[dict[str, Any]] = []
    for i in range(0, len(gene_ids), _ESUMMARY_BATCH_SIZE):
        batch = gene_ids[i : i + _ESUMMARY_BATCH_SIZE]
        payload = client.get_json(
            f"{cfg.NCBI_EUTILS_BASE_URL}/esummary.fcgi",
            params={"db": "gene", "id": ",".join(batch), "retmode": "json"},
        )
        result = payload.get("result", {})
        for gid in batch:
            doc = result.get(gid)
            if not isinstance(doc, dict):
                continue
            rows.append(
                {
                    "GeneID": gid,
                    "Symbol": str(doc.get("name", "")),
                    "Description": str(doc.get("description", "")),
                    "GeneType": "",
                    "EnsemblGeneID": "",
                    "Chromosome": str(doc.get("chromosome", "")),
                    "Synonyms": str(doc.get("otheraliases", "")).replace(", ", "|"),
                }
            )
    if not rows:
        return pd.DataFrame(columns=list(ANNOTATION_TABLE_COLUMNS)).rename_axis("GeneID")
    return _normalize_annotation_frame(
        pd.DataFrame(rows).set_index("GeneID"), "entrez_gene_esummary"
    )


def load_rnaseq_annotation(
    assembly: str = "GRCh38.p13",
    *,
    gene_ids: Iterable[str | int] | None = None,
    refresh: bool = False,
    http_client: HttpClient | None = None,
) -> pd.DataFrame:
    """Load the NCBI gene annotation table used by GEO's RNA-seq count matrices.

    Source cascade (first that works wins; results are merged into a local cache):
        1. Local cache `<cache>/annotations/<Organism>.<assembly>.annot.tsv.gz`
        2. GEO's own annotation file (full table, ~40k genes; browser-gated on some networks)
        3. NCBI Datasets v2 gene API for the requested `gene_ids` (1000 IDs/request)
        4. Entrez Gene `esummary` for the requested `gene_ids` (no GeneType/Ensembl)

    Args:
        assembly: 'GRCh38.p13' (human) or 'GRCm39' (mouse)
        gene_ids: Optional GeneIDs to restrict/ensure (required when GEO's file is unavailable)
        refresh: Ignore the local cache and re-download
        http_client: Optional HTTP client

    Returns:
        DataFrame indexed by `GeneID` (str) with columns `Symbol`, `Description`, `GeneType`,
        `EnsemblGeneID`, `Chromosome`, `Synonyms`, `source` (restricted to `gene_ids` if given).
    """
    client = http_client or get_default_client()
    wanted = [str(g).strip() for g in gene_ids] if gene_ids is not None else None
    if wanted is not None:
        wanted = list(dict.fromkeys(g for g in wanted if g))
    cache_name = RNASEQ_ANNOTATION_FILES.get(assembly, f"{assembly}.annot.tsv.gz")
    cache_path = _annotation_cache_dir() / cache_name
    reporter = get_reporter()

    with reporter.task(
        "GENE", "load_rnaseq_annotation", f"Loading gene annotation ({assembly})", target=assembly
    ) as task:
        table = None if refresh else _read_cached_table(cache_path)
        complete_in_cache = False
        if table is not None:
            if wanted is None:
                complete_in_cache = bool(table.attrs.get("complete")) or len(table) > 10000
            else:
                complete_in_cache = all(g in table.index for g in wanted)
            if complete_in_cache:
                task.step(f"Cache hit ({len(table):,} genes)")

        if not complete_in_cache:
            fetched = _fetch_geo_annotation_file(assembly, client)
            if fetched is not None:
                table = fetched
                task.step(f"Downloaded GEO annotation file ({len(table):,} genes)")
            else:
                if wanted is None:
                    raise DataError(
                        f"GEO's {assembly} annotation file is unavailable from this network and no "
                        "gene_ids were given to annotate via NCBI Datasets",
                        hint="Pass gene_ids=[...] (or annotate a GeneID-indexed DataFrame) so the "
                        "needed genes can be fetched from the NCBI Datasets API instead",
                    )
                missing = [g for g in wanted if table is None or g not in table.index]
                task.step(f"Annotating {len(missing):,} GeneID(s) via NCBI Datasets API")
                try:
                    fetched = _fetch_datasets_gene_reports(missing, client)
                except PyNCBIError as exc:
                    reporter.warn(
                        "GENE",
                        f"NCBI Datasets unavailable ({type(exc).__name__}); using Entrez Gene esummary",
                        operation="load_rnaseq_annotation",
                        target=assembly,
                    )
                    fetched = _fetch_esummary_gene(missing, client)
                table = (
                    fetched
                    if table is None
                    else pd.concat([table, fetched[~fetched.index.isin(table.index)]])
                )
            _write_cached_table(table, cache_path)

        assert table is not None
        for col in ANNOTATION_TABLE_COLUMNS:
            if col not in table.columns:
                table[col] = ""
        if wanted is not None:
            table = table.reindex([g for g in wanted if g in table.index])
        task.complete(f"Gene annotation ready: {len(table):,} genes ({assembly})")
        return table


def annotate_gene_ids(
    data: pd.DataFrame | Iterable[str | int],
    *,
    assembly: str = "GRCh38.p13",
    columns: Sequence[str] | None = None,
    http_client: HttpClient | None = None,
) -> pd.DataFrame:
    """Map numeric Entrez GeneIDs to gene annotation and prepend it to a count matrix.

    Args:
        data: A GeneID-indexed DataFrame (e.g., from `load_rnaseq_counts`) or an iterable of
            GeneIDs
        assembly: 'GRCh38.p13' (human) or 'GRCm39' (mouse)
        columns: Annotation columns to attach (default `Symbol`, `GeneType`, `EnsemblGeneID`)
        http_client: Optional HTTP client

    Returns:
        For a DataFrame: a copy with the annotation columns inserted first (measurement
        columns unchanged; unknown IDs get NaN). For an iterable: an annotation DataFrame
        indexed by GeneID in input order.
    """
    cols = tuple(columns) if columns else DEFAULT_GENE_ANNOTATION_COLUMNS
    ids = [str(i) for i in (data.index if isinstance(data, pd.DataFrame) else data)]
    table = load_rnaseq_annotation(assembly, gene_ids=ids, http_client=http_client)
    for col in cols:
        if col not in table.columns:
            raise DataError(
                f"Annotation column '{col}' not available", hint=f"Available: {list(table.columns)}"
            )
    annot = table[list(cols)].replace("", pd.NA)

    if isinstance(data, pd.DataFrame):
        aligned = annot.reindex(ids)
        aligned.index = data.index
        base = data.drop(columns=[c for c in cols if c in data.columns])
        return pd.concat([aligned, base], axis=1)

    out = annot.reindex(ids)
    out.index.name = "GeneID"
    return out


_GENE_ID_RE = re.compile(r"^\d+$")


def looks_like_gene_id_index(index: Iterable[Any], sample: int = 50) -> bool:
    """Heuristic: True if the first `sample` index labels are all numeric Entrez GeneIDs."""
    seen = 0
    for val in index:
        if not _GENE_ID_RE.match(str(val)):
            return False
        seen += 1
        if seen >= sample:
            break
    return seen > 0
