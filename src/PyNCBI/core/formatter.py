"""Unified terminal and file output formatter for PyNCBI.

Provides helpers to:
- Summarize sample characteristics DataFrames into human/agent-readable stats
- Render DataFrames, dictionaries, and domain models neatly to Terminal (`text`),
  `markdown`, or `json` (`AgentEnvelope`)
- Export tabular and structured data to `.csv`, `.tsv`, `.parquet`, or `.json`
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

from PyNCBI.core.exceptions import ConfigurationError
from PyNCBI.core.invocation import begin_run as begin_run
from PyNCBI.core.invocation import run_snapshot
from PyNCBI.core.models import AgentEnvelope, CharacteristicSummary
from PyNCBI.core.serialization import json_dumps, table_records

ENVELOPE_SCHEMA_VERSION = "1.1"

_CONSOLE_FALLBACKS: dict[str, str] = {
    "\u2022": "*",  # bullet
    "\u2014": "-",  # em dash
    "\u2013": "-",  # en dash
    "\u2192": "->",  # arrow
    "\u2190": "<-",
    "\u2605": "*",  # star
    "\u2606": "*",
    "\u00d7": "x",  # multiplication sign
    "\u2026": "...",  # ellipsis
    "\u00b7": "-",  # middle dot
    "\u2713": "OK",
    "\u2714": "OK",
    "\u2717": "X",
    "\u2718": "X",
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
}


def _package_version() -> str:
    try:
        from PyNCBI import __version__

        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


def console_safe_text(text: str, encoding: str | None = None) -> str:
    """Return `text` guaranteed to be encodable in the console's encoding.

    Typographic glyphs (bullets, dashes, stars) are transliterated to ASCII when the
    target encoding cannot represent them (e.g. Windows cp1252 consoles); anything
    else that is still unencodable is replaced rather than crashing the CLI.
    """
    enc = (encoding or getattr(sys.stdout, "encoding", None) or "utf-8").lower()
    if enc.replace("-", "") in ("utf8", "utf16", "utf32"):
        return text
    try:
        text.encode(enc)
        return text
    except (UnicodeEncodeError, LookupError):
        pass
    out = text
    for glyph, repl in _CONSOLE_FALLBACKS.items():
        out = out.replace(glyph, repl)
    return out.encode(enc, errors="replace").decode(enc, errors="replace")


def emit(text: str) -> None:
    """Print to stdout safely on any console encoding."""
    sys.stdout.write(console_safe_text(text) + "\n")
    sys.stdout.flush()


def summarize_characteristics_df(df: pd.DataFrame) -> list[CharacteristicSummary]:
    """Compute column-by-column summaries for a sample characteristics DataFrame."""
    if df is None or df.empty:
        return []

    total_rows = len(df)
    summaries: list[CharacteristicSummary] = []

    for col in df.columns:
        series = df[col].dropna()
        series = series[series.astype(str).str.strip() != ""]  # type: ignore[misc]
        non_null = len(series)
        if non_null == 0:
            continue

        # Attempt numeric conversion if >= 80% of non-null values are numbers
        numeric_series = pd.to_numeric(series, errors="coerce")
        valid_numeric = numeric_series.dropna()

        if len(valid_numeric) >= max(2, int(0.8 * non_null)) and valid_numeric.nunique() > 2:
            stats = {
                "min": float(valid_numeric.min()),
                "max": float(valid_numeric.max()),
                "mean": float(valid_numeric.mean()),
                "median": float(valid_numeric.median()),
                "std": float(valid_numeric.std()) if len(valid_numeric) > 1 else 0.0,
            }
            summaries.append(
                CharacteristicSummary(
                    name=str(col),
                    dtype="numeric",
                    non_null_count=non_null,
                    total_count=total_rows,
                    unique_count=int(valid_numeric.nunique()),
                    value_counts=None,
                    numeric_stats=stats,
                )
            )
        else:
            vc = series.astype(str).value_counts().head(20)
            vc_dict = {str(k): int(v) for k, v in vc.items()}
            summaries.append(
                CharacteristicSummary(
                    name=str(col),
                    dtype="categorical",
                    non_null_count=non_null,
                    total_count=total_rows,
                    unique_count=int(series.nunique()),
                    value_counts=vc_dict,
                    numeric_stats=None,
                )
            )

    return summaries


def _write_parquet(df: pd.DataFrame, dest: Path, *, index: bool = True) -> None:
    """Write Parquet, turning a missing engine into an instruction rather than a traceback.

    Parquet needs `pyarrow`, which PyNCBI deliberately does not depend on: the engine is
    far larger than the rest of the package combined, and every other output format works
    without it. Install it with `pip install "PyNCBI[parquet]"` when it is wanted.
    """
    try:
        df.to_parquet(dest, index=index)
    except ImportError as exc:
        raise ConfigurationError(
            f"Writing {dest.name} needs a Parquet engine, which is not installed",
            hint=(
                'Install it with `pip install "PyNCBI[parquet]"` (or `pip install pyarrow`), '
                "or choose a .csv, .tsv or .json output path instead."
            ),
        ) from exc


def save_data(
    data: pd.DataFrame | pd.Series | dict[str, Any] | list[Any],
    output_path: str | Path,
) -> Path:
    """Save tabular or structured data to disk based on file extension.

    Supported extensions:
        - `.csv` (comma-separated)
        - `.tsv` or `.txt` (tab-separated)
        - `.parquet` (Apache Parquet)
        - `.json` (JSON)
    """
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = dest.suffix.lower()

    if isinstance(data, pd.Series):
        df = data.to_frame()
    elif isinstance(data, pd.DataFrame):
        df = data
    else:
        df = None

    if df is not None:
        if suffix == ".parquet":
            _write_parquet(df, dest)
        elif suffix in (".tsv", ".txt"):
            df.to_csv(dest, sep="\t")
        elif suffix == ".json":
            dest.write_text(json_dumps(table_records(df), indent=2), encoding="utf-8")
        else:
            df.to_csv(dest)
        return dest

    # Dict or list payload
    if suffix in (".csv", ".tsv") and isinstance(data, list):
        pd.DataFrame(data).to_csv(dest, sep="\t" if suffix == ".tsv" else ",", index=False)
    elif suffix == ".parquet" and isinstance(data, list):
        _write_parquet(pd.DataFrame(data), dest, index=False)
    else:
        dest.write_text(json_dumps(data, indent=2), encoding="utf-8")

    return dest


def format_dataframe_preview(
    df: pd.DataFrame,
    mode: str = "text",
    max_rows: int = 15,
    max_cols: int = 10,
) -> str:
    """Format a DataFrame into a concise terminal, Markdown, or JSON preview."""
    if df is None or df.empty:
        return "(Empty table)"

    n_rows, n_cols = df.shape
    preview_df = df.iloc[:max_rows, :max_cols]

    if mode == "json":
        payload = {
            "shape": [n_rows, n_cols],
            "index_name": df.index.name or "index",
            "columns": [str(c) for c in df.columns[:100]],
            "preview_rows": preview_df.reset_index().to_dict(orient="records"),
            "truncated_rows": n_rows > max_rows,
            "truncated_cols": n_cols > max_cols,
        }
        return json_dumps(payload, indent=2)

    if mode == "markdown":
        header = f"**Table Shape:** `{n_rows:,} rows × {n_cols:,} columns`\n\n"
        cols = list(preview_df.columns)
        idx_name = preview_df.index.name or "Index"
        md_lines = [
            "| " + " | ".join([str(idx_name)] + [str(c) for c in cols]) + " |",
            "| " + " | ".join(["---"] * (len(cols) + 1)) + " |",
        ]
        for idx, row in preview_df.iterrows():
            vals = [str(idx)] + [
                str(row[c]).replace("\n", " ")[:60] if pd.notna(row[c]) else "" for c in cols
            ]
            md_lines.append("| " + " | ".join(vals) + " |")
        footer = ""
        if n_rows > max_rows or n_cols > max_cols:
            footer = f"\n\n*Showing first {min(n_rows, max_rows)} of {n_rows:,} rows and {min(n_cols, max_cols)} of {n_cols:,} columns.*"
        return header + "\n".join(md_lines) + footer

    # Default text mode
    header = f"Table Shape: {n_rows:,} rows × {n_cols:,} columns\n"
    table_str = preview_df.to_string(max_rows=max_rows, max_cols=max_cols)
    footer = ""
    if n_rows > max_rows or n_cols > max_cols:
        footer = f"\n... (Showing {min(n_rows, max_rows)}/{n_rows:,} rows, {min(n_cols, max_cols)}/{n_cols:,} columns)"
    return header + table_str + footer


def render_output(
    data: Any,
    *,
    mode: str = "text",
    command: str = "pyncbi",
    target: str = "",
    summary: str = "",
    saved_files: list[str] | None = None,
    next_steps: list[str] | None = None,
    warnings: list[str] | None = None,
    max_rows: int = 15,
) -> str:
    """Render any PyNCBI result into `text`, `markdown`, or `json` (`AgentEnvelope`)."""
    saved_files = saved_files or []
    next_steps = next_steps or []
    warnings = warnings or []

    if mode == "json":
        if isinstance(data, pd.DataFrame):
            n_rows, n_cols = data.shape
            serialized_data: Any = {
                "shape": [n_rows, n_cols],
                "index_name": data.index.name or "index",
                "columns": [str(c) for c in data.columns[:100]],
                "preview": data.iloc[:max_rows, :15].reset_index().to_dict(orient="records"),
            }
        elif isinstance(data, pd.Series) or hasattr(data, "to_dict"):
            serialized_data = data.to_dict()
        elif isinstance(data, list):
            serialized_data = [
                item.to_dict() if hasattr(item, "to_dict") else item for item in data
            ]
        else:
            serialized_data = data

        elapsed_ms, request_count = run_snapshot()
        envelope = AgentEnvelope(
            ok=True,
            command=command,
            target=target,
            summary=summary,
            data=serialized_data,
            saved_files=saved_files,
            next_steps=next_steps,
            warnings=warnings,
            schema_version=ENVELOPE_SCHEMA_VERSION,
            pyncbi_version=_package_version(),
            elapsed_ms=elapsed_ms,
            request_count=request_count,
        )
        return envelope.to_json()

    if mode == "markdown":
        if hasattr(data, "to_markdown") and not isinstance(data, (pd.DataFrame, pd.Series)):
            body = data.to_markdown()
        elif isinstance(data, pd.DataFrame):
            body = format_dataframe_preview(data, mode="markdown", max_rows=max_rows)
        elif isinstance(data, pd.Series):
            body = format_dataframe_preview(data.to_frame(), mode="markdown", max_rows=max_rows)
        elif isinstance(data, list) and data and hasattr(data[0], "to_dict"):
            df_list = pd.DataFrame([x.to_dict() for x in data])
            body = format_dataframe_preview(df_list, mode="markdown", max_rows=max_rows)
        else:
            body = f"```json\n{json_dumps(data, indent=2)}\n```"

        parts = []
        if summary and not hasattr(data, "to_markdown"):
            parts.append(f"### {summary}\n")
        parts.append(body)
        if saved_files:
            parts.append("\n**Saved Files:**\n" + "\n".join(f"- `{p}`" for p in saved_files))
        if next_steps:
            parts.append(
                "\n**Suggested Next Steps:**\n" + "\n".join(f"- `{s}`" for s in next_steps)
            )
        return "\n".join(parts)

    # Default: text mode
    if hasattr(data, "to_text"):
        body = data.to_text()
    elif isinstance(data, pd.DataFrame):
        body = format_dataframe_preview(data, mode="text", max_rows=max_rows)
    elif isinstance(data, pd.Series):
        body = data.to_string()
    elif isinstance(data, list) and data and hasattr(data[0], "to_dict"):
        df_list = pd.DataFrame([x.to_dict() for x in data])
        body = format_dataframe_preview(df_list, mode="text", max_rows=max_rows)
    elif isinstance(data, (dict, list)):
        body = json_dumps(data, indent=2)
    else:
        body = str(data)

    parts = []
    if summary and not hasattr(data, "to_text"):
        parts.append(summary + "\n")
    parts.append(body)
    if saved_files:
        parts.append("\nSaved to: " + ", ".join(saved_files))
    return "\n".join(parts)
