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

from PyNCBI.core.config import get_config
from PyNCBI.core.exceptions import (
    DataError,
    NoDataAvailableError,
    PyNCBIError,
)
from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.tables import Table, by_id, from_records, is_table, iter_records, project
from PyNCBI.core.tables import table as make_table
from PyNCBI.core.types import validate_accession
from PyNCBI.io.cache import LocalCache
from PyNCBI.io.http_client import get_default_client
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor as ThreadPoolExecutor
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser
from PyNCBI.parsing.tabular import parse_delimited_table

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


def _annotation_cache_dir(client: HttpClient) -> Path:
    config = getattr(client, "app_config", None) or get_config()
    return Path(config.cache_folder) / "annotations"


def _read_cached_table(path: Path) -> tuple[Table, bool] | None:
    payload = LocalCache[dict[str, Any]](path.parent).get(path.name)
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 2
        or not is_table(payload.get("table"))
    ):
        return None
    return payload["table"], payload.get("complete") is True


def _write_cached_table(value: Table, path: Path, *, complete: bool = False) -> None:
    LocalCache[dict[str, Any]](path.parent).set(
        path.name, {"version": 2, "table": value, "complete": complete}
    )


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
) -> Table:
    """Retrieve published platform annotation as a native columns/rows payload."""
    gpl_id = validate_accession(gpl_id, "GPL")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    client = http_client or get_default_client()
    src = source.strip().lower()
    if src not in ("auto", "data", "annot"):
        raise ValueError("source must be auto, data or annot")
    cache_path = (
        _annotation_cache_dir(client) / f"{gpl_id}_{'annot' if src == 'annot' else 'table'}"
    )
    cached = _read_cached_table(cache_path) if use_cache else None
    value = cached[0] if cached else None
    if value is None:
        lines = client.stream_lines(
            gpl_annot_url(gpl_id) if src == "annot" else gpl_table_url(gpl_id)
        )
        try:
            value = (parser or get_parser()).parse_gpl_table_lines(
                lines, limit=limit, gpl_id=gpl_id
            )
        except NoDataAvailableError:
            raise
        except DataError as exc:
            _explain_missing_platform_table(gpl_id, client, exc)
            raise
        finally:
            close = getattr(lines, "close", None)
            if callable(close):
                close()
        if use_cache and limit is None:
            _write_cached_table(value, cache_path, complete=True)
    if limit is not None:
        value = {"columns": value["columns"], "rows": value["rows"][:limit]}
    if columns:
        missing = [name for name in columns if name not in value["columns"]]
        if missing:
            raise DataError(
                f"Column(s) {missing} not found in {gpl_id}",
                hint=f"Available columns: {value['columns']}",
            )
        value = project(value, list(dict.fromkeys(["feature_id", *columns])))
    if save_to is not None:
        save_data(value, save_to)
    return value


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


def _attach_annotations(data: Table, annotations: Table, fields: Sequence[str]) -> Table:
    missing = [name for name in fields if name not in annotations["columns"]]
    if missing:
        raise DataError(
            f"Annotation fields unavailable: {missing}", hint=f"Available: {annotations['columns']}"
        )
    lookup: dict[str, dict[str, Any]] = {}
    for record in iter_records(annotations):
        lookup.setdefault(str(record[annotations["columns"][0]]), record)
    base = [name for name in data["columns"][1:] if name not in fields]
    positions = [data["columns"].index(name) for name in base]
    return make_table(
        [data["columns"][0], *fields, *base],
        (
            [
                row[0],
                *[(lookup.get(str(row[0]), {}).get(name) or None) for name in fields],
                *[row[index] for index in positions],
            ]
            for row in data["rows"]
        ),
    )


def annotate_probes(
    data: Table,
    gpl_id: str,
    *,
    columns: Sequence[str] | None = None,
    http_client: HttpClient | None = None,
) -> Table:
    """Attach retrieved probe annotation by explicit feature ID without changing values."""
    annotations = fetch_gpl_table(gpl_id, http_client=http_client)
    fields = list(columns) if columns else detect_gene_columns(annotations["columns"])
    if not fields:
        raise DataError(f"Could not detect gene annotation fields on {gpl_id}")
    return _attach_annotations(data, annotations, fields)


def rnaseq_annotation_url(assembly: str) -> str:
    """GEO download URL of the NCBI RNA-seq annotation table for an assembly."""
    fname = RNASEQ_ANNOTATION_FILES.get(assembly, assembly if assembly.endswith(".gz") else "")
    if not fname:
        raise ValueError(
            f"Unknown assembly '{assembly}'. Known: {sorted(RNASEQ_ANNOTATION_FILES)} "
            "(or pass an explicit '<Organism>.<assembly>.annot.tsv.gz' filename)"
        )
    return f"{get_config().NCBI_GEO_DOWNLOAD_URL}?type=rnaseq_counts&format=file&file={fname}"


def _normalize_annotation_records(records: Iterable[dict[str, Any]], source: str) -> Table:
    rows = []
    for record in records:
        identifier = str(record.get("gene_id", record.get("GeneID", record.get("feature_id", ""))))
        row = {"gene_id": identifier}
        for name in ANNOTATION_TABLE_COLUMNS:
            value = record.get(name, record.get("ChrAcc", "") if name == "Chromosome" else "")
            row[name] = source if name == "source" else str(value) if value is not None else ""
        for name, value in record.items():
            if name not in row and name not in ("GeneID", "feature_id"):
                row[name] = str(value) if value is not None else ""
        rows.append(row)
    return from_records(rows) if rows else make_table(["gene_id", *ANNOTATION_TABLE_COLUMNS])


def _fetch_geo_annotation_file(assembly: str, client: HttpClient) -> Table | None:
    from PyNCBI.domains.geo.transfer import decompress_table_bytes

    try:
        text = decompress_table_bytes(
            client.get_bytes(rnaseq_annotation_url(assembly)), context=f"{assembly} annotation"
        )
    except PyNCBIError as exc:
        get_reporter().warn(
            "GEO", f"Annotation file unavailable ({type(exc).__name__}); using NCBI gene records"
        )
        return None
    value = parse_delimited_table(text, "annotation.tsv", id_field="gene_id", numeric=False)
    return _normalize_annotation_records(iter_records(value), "geo_annot_file")


def _fetch_datasets_gene_reports(gene_ids: list[str], client: HttpClient) -> Table:
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
        return make_table(["gene_id", *ANNOTATION_TABLE_COLUMNS])
    return _normalize_annotation_records(all_rows, "ncbi_datasets")


def _fetch_esummary_gene(gene_ids: list[str], client: HttpClient) -> Table:
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
        return make_table(["gene_id", *ANNOTATION_TABLE_COLUMNS])
    return _normalize_annotation_records(rows, "entrez_gene_esummary")


def load_rnaseq_annotation(
    assembly: str = "GRCh38.p13",
    *,
    gene_ids: Iterable[str | int] | None = None,
    refresh: bool = False,
    http_client: HttpClient | None = None,
) -> Table:
    """Retrieve gene annotation as native data; full and partial caches remain distinct."""
    client = http_client or get_default_client()
    wanted = (
        list(dict.fromkeys(str(value).strip() for value in gene_ids if str(value).strip()))
        if gene_ids is not None
        else None
    )
    cache_name = RNASEQ_ANNOTATION_FILES.get(assembly, f"{assembly}.annot.tsv.gz").removesuffix(
        ".tsv.gz"
    )
    if Path(cache_name).name != cache_name:
        raise ValueError("assembly must be a name, not a filesystem path")
    path = _annotation_cache_dir(client) / cache_name
    cached = None if refresh else _read_cached_table(path)
    value, complete = (
        cached if cached else (make_table(["gene_id", *ANNOTATION_TABLE_COLUMNS]), False)
    )
    lookup = by_id(value, "gene_id")
    sufficient = complete if wanted is None else all(identifier in lookup for identifier in wanted)
    if not sufficient:
        fetched = _fetch_geo_annotation_file(assembly, client)
        if fetched is not None:
            value, complete = fetched, True
        else:
            if wanted is None:
                raise DataError(
                    f"GEO's {assembly} annotation is unavailable and no gene_ids were supplied",
                    hint="Pass the gene IDs to retrieve their NCBI records",
                )
            missing = [identifier for identifier in wanted if identifier not in lookup]
            try:
                fetched = _fetch_datasets_gene_reports(missing, client)
            except PyNCBIError:
                fetched = _fetch_esummary_gene(missing, client)
            for record in iter_records(fetched):
                lookup.setdefault(str(record["gene_id"]), record)
            value = (
                from_records(lookup.values())
                if lookup
                else make_table(["gene_id", *ANNOTATION_TABLE_COLUMNS])
            )
        _write_cached_table(value, path, complete=complete)
    if wanted is not None:
        lookup = by_id(value, "gene_id")
        value = from_records(
            (lookup[identifier] for identifier in wanted if identifier in lookup), value["columns"]
        )
    return value


def annotate_gene_ids(
    data: Table | Iterable[str | int],
    *,
    assembly: str = "GRCh38.p13",
    columns: Sequence[str] | None = DEFAULT_GENE_ANNOTATION_COLUMNS,
    http_client: HttpClient | None = None,
) -> Table:
    """Retrieve annotations for gene IDs, optionally attaching them to native measurements."""
    fields = list(columns or DEFAULT_GENE_ANNOTATION_COLUMNS)
    identifiers = (
        [str(row[0]) for row in data["rows"]] if is_table(data) else [str(item) for item in data]
    )
    annotations = load_rnaseq_annotation(assembly, gene_ids=identifiers, http_client=http_client)
    base = (
        data
        if is_table(data)
        else make_table(["gene_id"], ([identifier] for identifier in identifiers))
    )
    return _attach_annotations(base, annotations, fields)


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
