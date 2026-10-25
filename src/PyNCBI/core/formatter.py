"""Unified terminal and file output formatter for PyNCBI.

Provides helpers to:
- Summarize sample characteristics tables into human/agent-readable stats
- Render native tables, dictionaries, and domain models neatly to Terminal (`text`),
  `markdown`, or `json` (`AgentEnvelope`)
- Export tabular and structured data to `.csv`, `.tsv`, or `.json`
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from PyNCBI.core.exceptions import ConfigurationError
from PyNCBI.core.invocation import begin_run as begin_run
from PyNCBI.core.invocation import run_snapshot
from PyNCBI.core.models import AgentEnvelope, CharacteristicSummary
from PyNCBI.core.serialization import json_dumps
from PyNCBI.core.tables import Table, from_records, is_table

ENVELOPE_SCHEMA_VERSION = "2.0"

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


def summarize_characteristics(value: Table) -> list[CharacteristicSummary]:
    """Describe metadata fields using native values; never transform source records."""
    result = []
    for index, name in enumerate(value["columns"]):
        if name == "gsm_id":
            continue
        values = [
            row[index]
            for row in value["rows"]
            if row[index] is not None and str(row[index]).strip()
        ]
        if not values:
            continue
        numeric = []
        for item in values:
            try:
                number = float(item)
                if math.isfinite(number):
                    numeric.append(number)
            except (ValueError, TypeError):
                pass
        if len(numeric) >= max(2, int(0.8 * len(values))) and len(set(numeric)) > 2:
            result.append(
                CharacteristicSummary(
                    name,
                    "numeric",
                    len(values),
                    len(value["rows"]),
                    len(set(numeric)),
                    numeric_stats={
                        "min": min(numeric),
                        "max": max(numeric),
                        "mean": statistics.mean(numeric),
                        "median": statistics.median(numeric),
                        "std": statistics.stdev(numeric) if len(numeric) > 1 else 0,
                    },
                )
            )
        else:
            counts = Counter(str(item) for item in values)
            result.append(
                CharacteristicSummary(
                    name,
                    "categorical",
                    len(values),
                    len(value["rows"]),
                    len(counts),
                    value_counts=dict(counts.most_common(20)),
                )
            )
    return result


def _native(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_native(item) for item in value]
    return value


def save_data(data: Any, output_path: str | Path) -> Path:
    """Write native data as JSON, CSV or TSV using only the Python standard library."""
    destination = Path(output_path)
    suffix = destination.suffix.lower()
    if suffix in (".parquet", ".yaml", ".yml", ".xlsx", ".feather"):
        raise ConfigurationError(
            f"{suffix} conversion belongs to the caller; PyNCBI exports JSON, CSV and TSV"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = _native(data)
    if suffix in (".csv", ".tsv", ".txt") and (is_table(data) or isinstance(data, list)):
        value = data if is_table(data) else from_records(data if isinstance(data, list) else [])
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="," if suffix == ".csv" else "\t")
            writer.writerow(value["columns"])
            writer.writerows(value["rows"])
    else:
        destination.write_text(json_dumps(data, indent=2), encoding="utf-8")
    return destination


def format_table_preview(
    value: Table, mode: str = "text", max_rows: int = 15, max_cols: int = 10
) -> str:
    """Render bounded native data without an analysis library."""
    if not value or not value["rows"]:
        return "(Empty table)"
    names = value["columns"][:max_cols]
    rows = [row[:max_cols] for row in value["rows"][:max_rows]]
    total_rows, total_columns = len(value["rows"]), len(value["columns"])
    if mode == "json":
        return json_dumps(
            {
                "columns": names,
                "rows": rows,
                "total_rows": total_rows,
                "total_columns": total_columns,
                "truncated_rows": total_rows > max_rows,
                "truncated_cols": total_columns > max_cols,
            },
            indent=2,
        )

    def cell(item: Any) -> str:
        return "" if item is None else str(item).replace("\n", " ").replace("|", "\\|")[:60]

    if mode == "markdown":
        lines = ["| " + " | ".join(names) + " |", "| " + " | ".join("---" for _ in names) + " |"]
        lines.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    else:
        lines = ["\t".join(names), *["\t".join(cell(item) for item in row) for row in rows]]
    if total_rows > max_rows or total_columns > max_cols:
        lines.append(
            f"Showing {len(rows)}/{total_rows} rows and {len(names)}/{total_columns} columns"
        )
    return f"Table Shape: {total_rows:,} rows x {total_columns:,} columns\n\n" + "\n".join(lines)


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
    """Render native data or a domain record, including the versioned agent envelope."""
    payload = _native(data)
    if mode == "json":
        if is_table(payload):
            payload = {
                **payload,
                "rows": payload["rows"][:max_rows],
                "total_rows": len(payload["rows"]),
                "truncated": len(payload["rows"]) > max_rows,
            }
        elapsed_ms, request_count = run_snapshot()
        return AgentEnvelope(
            ok=True,
            command=command,
            target=target,
            summary=summary,
            data=payload,
            saved_files=saved_files or [],
            next_steps=next_steps or [],
            warnings=warnings or [],
            schema_version=ENVELOPE_SCHEMA_VERSION,
            pyncbi_version=_package_version(),
            elapsed_ms=elapsed_ms,
            request_count=request_count,
        ).to_json()
    if mode == "markdown" and hasattr(data, "to_markdown"):
        body = data.to_markdown()
    elif mode == "text" and hasattr(data, "to_text"):
        body = data.to_text()
    elif is_table(payload):
        body = format_table_preview(payload, mode, max_rows=max_rows)
    elif isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        body = format_table_preview(from_records(payload), mode, max_rows=max_rows)
    else:
        body = json_dumps(payload, indent=2) if isinstance(payload, (dict, list)) else str(payload)
        if mode == "markdown":
            body = f"```json\n{body}\n```"
    parts = ([summary + "\n"] if summary and not hasattr(data, "to_text") else []) + [body]
    if saved_files:
        parts.append("\nSaved to: " + ", ".join(saved_files))
    if mode == "markdown" and next_steps:
        parts.append("\nSuggested next steps:\n" + "\n".join(f"- `{step}`" for step in next_steps))
    return "\n".join(parts)
