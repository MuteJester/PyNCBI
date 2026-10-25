"""Single-request GSE sample metadata extraction and cohort filtering."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pandas as pd

from PyNCBI.core.config import get_config
from PyNCBI.core.formatter import save_data, summarize_characteristics_df
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.types import validate_accession
from PyNCBI.io.http_client import (
    get_default_client,
)
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from pathlib import Path

    from PyNCBI.core.models import CharacteristicSummary
    from PyNCBI.core.protocols import HttpClient

if TYPE_CHECKING:
    from PyNCBI.core.protocols import HttpClient


def _apply_sample_filter(df: pd.DataFrame, where: str) -> pd.DataFrame:
    """Filter a sample DataFrame by pandas query, key=value, or case-insensitive substring."""
    if df.empty or not where or not where.strip():
        return df

    expr = where.strip()
    # 1. Try pandas DataFrame.query() first (converting numeric-looking columns temporarily)
    query_error: str = ""
    try:
        work_df = df.copy()
        for col in work_df.columns:
            num_s = pd.to_numeric(work_df[col], errors="coerce")
            if num_s.notna().sum() >= max(1, int(0.6 * work_df[col].notna().sum())):
                work_df[col] = num_s
        matched_idx = work_df.query(expr, engine="python").index
        return df.loc[matched_idx]
    except Exception as exc:  # noqa: BLE001 - the fallbacks below handle every shape
        query_error = f"{type(exc).__name__}: {exc}"

    # 2. Try simple key=value or key:value matching
    for sep in ("==", "=", ":"):
        if sep in expr and not any(op in expr for op in (">", "<", "!=")):
            key_part, val_part = expr.split(sep, 1)
            key_clean = key_part.strip().strip("'\"`").lower()
            val_clean = val_part.strip().strip("'\"`").lower()
            matching_cols = [c for c in df.columns if str(c).lower() == key_clean]
            if not matching_cols:
                matching_cols = [c for c in df.columns if key_clean in str(c).lower()]
            if matching_cols:
                col = matching_cols[0]
                mask = df[col].astype(str).str.lower().str.contains(re.escape(val_clean), na=False)  # type: ignore[misc]
                keyed: pd.DataFrame = df[mask]
                if not keyed.empty:
                    return keyed
                # No match on that column: try the whole-row search below before
                # concluding that the filter found nothing.
                break

    # 3. Fallback: case-insensitive substring match across all columns
    pat = re.escape(expr.lower())
    mask = df.astype(str).apply(lambda s: s.str.lower().str.contains(pat, na=False)).any(axis=1)
    result: pd.DataFrame = df[mask]

    # Nothing matched, and the expression never parsed as a column expression either. A
    # typo like `severity === 'Severe'` would otherwise look exactly like "no such samples",
    # and an agent would abandon a perfectly good study.
    if result.empty and query_error and any(op in expr for op in ("==", "!=", ">", "<", "=")):
        columns = ", ".join(str(c) for c in list(df.columns)[:12])
        more = ", ..." if len(df.columns) > 12 else ""
        raise ValueError(
            f"filter {expr!r} matched no samples, and it is not a valid column "
            f"expression either ({query_error}). Check the column name and quoting. "
            f"Available columns: {columns}{more}"
        )
    return result


def fetch_sample_tables(
    gse_id: str,
    *,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch the raw sample metadata and characteristics tables for a GSE in ONE request.

    This is the single HTTP + parse step behind every sample-level feature (the sample
    table, cohort file resolution, and the `GSE` object), so they can never drift apart.

    Returns:
        Tuple of `(info_df, characteristics_df)`, both indexed by GSM accession and both
        possibly empty. Nothing is reordered, filtered, or summarised here.
    """
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    batch_text = client.get_text(get_config().get_ncbi_batch_gsm_url(gse_id))
    return soft_parser.parse_gse_samples_batch(batch_text)


def combine_sample_tables(info_df: pd.DataFrame, char_df: pd.DataFrame) -> pd.DataFrame:
    """Join sample metadata and unpacked characteristics into one agent-friendly table.

    Characteristics columns that do not already exist in the metadata are appended, the
    columns most people filter on are moved to the front, and GEO's internal `^SAMPLE`
    marker column is dropped.
    """
    if info_df.empty or char_df.empty:
        return info_df
    non_overlapping = [c for c in char_df.columns if c not in info_df.columns]
    combined_df = pd.concat([info_df, char_df[non_overlapping]], axis=1)
    front_cols = [
        c
        for c in [
            "title",
            *list(char_df.columns),
            "source_name_ch1",
            "organism_ch1",
            "platform_id",
            "library_strategy",
            "molecule_ch1",
        ]
        if c in combined_df.columns
    ]
    seen: set[str] = set()
    ordered_front = [c for c in front_cols if not (c in seen or seen.add(c))]  # type: ignore[func-returns-value]
    rest_cols = [c for c in combined_df.columns if c not in seen and c != "^SAMPLE"]
    return combined_df[ordered_front + rest_cols]


def extract_gse_samples(
    gse_id: str,
    *,
    where: str | None = None,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[CharacteristicSummary]]:
    """Fetch and organize all sample metadata & characteristics for a GSE in 1 HTTP request.

    Args:
        gse_id: GSE accession (e.g., 'GSE85506')
        where: Optional filter expression (e.g., "disease == 'COVID-19'", "age > 50", or "PBMC")
        save_to: Optional file path (.csv, .tsv, .parquet, .json) to save the table
        http_client: Optional HTTP client
        parser: Optional SOFT parser

    Returns:
        Tuple of `(combined_samples_df, characteristics_df, characteristics_summaries)`
    """
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    soft_parser = parser or get_parser()
    reporter = get_reporter()

    with reporter.task(
        "GEO",
        "extract_gse_samples",
        f"Fetching batch sample metadata for {gse_id}",
        target=gse_id,
    ) as task:
        info_df, char_df = fetch_sample_tables(gse_id, http_client=client, parser=soft_parser)
        total_raw = len(info_df)
        combined_df = combine_sample_tables(info_df, char_df)

        if where:
            combined_df = _apply_sample_filter(combined_df, where)
            if not char_df.empty:
                char_df = char_df.loc[char_df.index.intersection(combined_df.index)]
            task.step(
                f"Applied filter '{where}': matched {len(combined_df)} of {total_raw} samples",
                current=len(combined_df),
                total=total_raw,
            )

        summaries = summarize_characteristics_df(char_df)

        if save_to is not None and not combined_df.empty:
            save_data(combined_df, save_to)
            task.step(f"Saved sample table to {save_to}")

        task.complete(
            f"Extracted {len(combined_df)} sample(s) and {len(char_df.columns)} characteristic column(s) for {gse_id}"
        )
        return combined_df, char_df, summaries
