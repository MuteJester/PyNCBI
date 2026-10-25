"""Cross-study screening for PyNCBI.

`screen_studies` turns a GEO search (or an explicit list of accessions) into ONE comparison
table an agent can filter on before downloading anything: sample count, which data sources
exist (NCBI counts / Series Matrix / per-sample files), the total download size, which
harmonized metadata fields are present and how well they are populated, and a quality score.
Per-study failures are recorded instead of aborting the screen.
"""

from __future__ import annotations

import re
from concurrent.futures import as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PyNCBI.core.exceptions import error_payload
from PyNCBI.core.formatter import save_data
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.tables import Table, from_records
from PyNCBI.io.http_client import format_size_human, get_default_client
from PyNCBI.io.transfer_limits import ContextThreadPoolExecutor as ThreadPoolExecutor

if TYPE_CHECKING:
    from pathlib import Path

    from PyNCBI.core.protocols import HttpClient
    from PyNCBI.metadata.schema import StudyRecord

COVERAGE_FIELDS: tuple[str, ...] = ("sex", "age", "disease", "tissue", "treatment", "time_point")
TABLE_COLUMNS: tuple[str, ...] = (
    "title",
    "organism",
    "platform",
    "experiment_type",
    "sample_count",
    "has_ncbi_counts",
    "has_series_matrix",
    "has_per_sample_files",
    "file_count",
    "download_bytes",
    "download_human",
    "harmonized_fields",
    "characteristics",
    *[f"{f}_coverage" for f in COVERAGE_FIELDS],
    "quality_score",
    "quality_issues",
    "pubmed",
    "bioproject",
)
_GSE_RE = re.compile(r"^GSE\d+$", re.I)


@dataclass
class ScreeningResult:
    """A comparison table over several GEO series, plus per-study failures."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    query: str = ""
    require: tuple[str, ...] = ()
    saved_path: Path | None = None

    @property
    def table(self) -> Table:
        """Native study comparison records, with an explicit gse_id field."""
        return from_records(self.rows, ["gse_id", *TABLE_COLUMNS])

    @property
    def study_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "require": list(self.require),
            "study_count": self.study_count,
            "rows": [dict(r) for r in self.rows],
            "errors": dict(self.errors),
            "notes": list(self.notes),
            "saved_path": str(self.saved_path) if self.saved_path else None,
        }

    def to_markdown(self, *, max_studies: int = 30) -> str:
        head = ["# Study screening", ""]
        if self.query:
            head.append(f"Query: `{self.query}`")
        if self.require:
            head.append(f"Required harmonized field(s): {', '.join(self.require)}")
        head.append(
            f"{self.study_count} study(ies) kept"
            + (f", {len(self.errors)} skipped" if self.errors else "")
        )
        head.append("")
        cols = (
            "sample_count",
            "organism",
            "platform",
            "download_human",
            "harmonized_fields",
            "sex_coverage",
            "age_coverage",
            "quality_score",
        )
        head.append("| gse_id | title | " + " | ".join(cols) + " |")
        head.append("| :-- | :-- | " + " | ".join(":--" for _ in cols) + " |")
        for row in self.rows[:max_studies]:
            title = str(row.get("title", ""))[:48]
            values = []
            for col in cols:
                val = row.get(col)
                if isinstance(val, float) and col.endswith("_coverage"):
                    values.append(f"{val:.0%}")
                else:
                    values.append("" if val is None else str(val))
            head.append(f"| {row['gse_id']} | {title} | " + " | ".join(values) + " |")
        for note in self.notes:
            head.append("")
            head.append(f"- {note}")
        return "\n".join(head)

    def to_text(self) -> str:
        return self.to_markdown().replace("`", "").replace("**", "").replace("# ", "")


def _row_from_record(record: StudyRecord) -> dict[str, Any]:
    files = record.files or []
    sources = {str(f.get("source", "")) for f in files}
    total = sum(int(f["size_bytes"]) for f in files if f.get("size_bytes"))
    harmonized_present: set[str] = set()
    coverage: dict[str, float] = dict.fromkeys(COVERAGE_FIELDS, 0.0)
    n = max(1, len(record.samples))
    for sample in record.samples:
        for key, nv in sample.harmonized.items():
            if key.startswith("raw:") or nv.value is None:
                continue
            harmonized_present.add(key)
            if key in coverage:
                coverage[key] += 1.0 / n
    char_keys: set[str] = set()
    for sample in record.samples:
        char_keys.update(sample.characteristics)
    quality = record.quality
    return {
        "gse_id": record.gse_id,
        "title": record.title,
        "organism": ", ".join(dict.fromkeys(record.organisms)) or "",
        "platform": ", ".join(dict.fromkeys(record.platforms)) or "",
        "experiment_type": ", ".join(record.experiment_types) or "",
        "sample_count": record.sample_count,
        "has_ncbi_counts": "ncbi_rnaseq_counts" in sources,
        "has_series_matrix": "series_matrix" in sources,
        "has_per_sample_files": any(s.supplementary_files for s in record.samples),
        "file_count": len(files),
        "download_bytes": total,
        "download_human": format_size_human(total),
        "harmonized_fields": ", ".join(sorted(harmonized_present)),
        "characteristics": ", ".join(sorted(char_keys)),
        **{f"{f}_coverage": round(min(1.0, coverage[f]), 4) for f in COVERAGE_FIELDS},
        "quality_score": quality.score if quality else None,
        "quality_issues": ", ".join(i.code for i in quality.issues) if quality else "",
        "pubmed": ", ".join(record.pubmed_ids),
        "bioproject": record.bioproject_id,
        "_harmonized_set": harmonized_present,
    }


def screen_studies(
    accessions_or_query: str | list[str],
    *,
    organism: str | None = None,
    dataset_type: str | None = None,
    min_samples: int | None = None,
    max_samples: int | None = None,
    limit: int = 20,
    require: tuple[str, ...] | list[str] = (),
    harmonize: bool = True,
    check_file_sizes: bool = True,
    sort_by: str | None = None,
    max_workers: int = 4,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
) -> ScreeningResult:
    """Screen many GEO series into one comparison table.

    Args:
        accessions_or_query: A list of GSE accessions, a single accession, or a GEO search query
        organism: Search filter (query mode)
        dataset_type: Search filter (query mode): 'rnaseq', 'methylation', 'microarray', ...
        min_samples / max_samples: Search filters (query mode)
        limit: Maximum studies to screen when searching (default 20)
        require: Keep only studies whose harmonized metadata includes all of these canonical
            fields (e.g. ``("sex", "age")``)
        harmonize: Harmonize characteristics so the coverage columns are populated (default True)
        check_file_sizes: HTTP HEAD every file so `download_bytes` is exact (default True)
        sort_by: Column to sort by, descending (e.g. ``'sample_count'``, ``'quality_score'``);
            `None` keeps the input / search order
        max_workers: Studies screened in parallel (default 4)
        save_to: Optional path for the table (.csv, .tsv, .json)
        http_client: Optional HTTP client

    Returns:
        `ScreeningResult` with `.table`, `.rows`, `.errors` and `.notes`.
    """
    from PyNCBI.domains.entrez import search_geo
    from PyNCBI.metadata.builder import build_study_record

    client = http_client or get_default_client()
    required = tuple(str(f).strip() for f in require if str(f).strip())
    reporter = get_reporter()

    query = ""
    if isinstance(accessions_or_query, str):
        text = accessions_or_query.strip()
        if _GSE_RE.match(text):
            accessions = [text.upper()]
        else:
            query = text
            accessions = []
    else:
        accessions = [str(a).strip().upper() for a in accessions_or_query if str(a).strip()]

    with reporter.task(
        "SCREEN",
        "screen_studies",
        f"Screening {query or f'{len(accessions)} accession(s)'}",
        target=query or ",".join(accessions[:4]),
    ) as task:
        if query:
            hits = search_geo(
                query,
                organism=organism,
                dataset_type=dataset_type,
                min_samples=min_samples,
                max_samples=max_samples,
                limit=limit,
                http_client=client,
            )
            accessions = [h.accession for h in hits if h.accession.upper().startswith("GSE")][
                :limit
            ]
            task.step(f"Search returned {len(accessions)} series to screen")

        result = ScreeningResult(query=query, require=required)
        if not accessions:
            result.notes.append("No studies to screen")
            task.complete("0 studies screened")
            return result

        def _one(acc: str) -> dict[str, Any]:
            record = build_study_record(
                acc,
                harmonize=harmonize,
                assess=True,
                check_file_sizes=check_file_sizes,
                http_client=client,
            )
            return _row_from_record(record)

        collected: dict[str, dict[str, Any]] = {}
        workers = max(1, min(max_workers, len(accessions)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_one, acc): acc for acc in accessions}
            for done, future in enumerate(as_completed(futures), start=1):
                acc = futures[future]
                try:
                    collected[acc] = future.result()
                except Exception as exc:  # noqa: BLE001 - one bad study must not end the screen
                    payload = error_payload(exc, command="screen", target=acc)
                    result.errors[acc] = payload
                    result.notes.append(
                        f"{acc} skipped: {payload['error_code']}: {payload['message']}"
                    )
                task.step(f"{done}/{len(accessions)} screened", current=done, total=len(accessions))

        rows = [collected[a] for a in accessions if a in collected]
        if required:
            kept = [r for r in rows if set(required) <= set(r.get("_harmonized_set", set()))]
            result.notes.insert(
                0,
                f"Kept {len(kept)} of {len(rows)} screened studies with harmonized field(s): {', '.join(required)}",
            )
            rows = kept
        if sort_by:
            rows = sorted(
                rows, key=lambda r: (r.get(sort_by) is None, r.get(sort_by)), reverse=True
            )
        for row in rows:
            row.pop("_harmonized_set", None)
        for row in collected.values():
            row.pop("_harmonized_set", None)
        result.rows = rows

        if save_to is not None and rows:
            result.saved_path = save_data(result.table, save_to)
            result.notes.append(f"Saved screening table to {result.saved_path}")

        task.complete(
            f"Screened {len(accessions)} study(ies); {len(rows)} kept, {len(result.errors)} failed"
        )
        return result
