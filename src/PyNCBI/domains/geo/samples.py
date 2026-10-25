"""Single-request sample metadata and a small, safe native-record cohort filter."""

from __future__ import annotations

import ast
import operator
import re
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import get_config
from PyNCBI.core.formatter import save_data, summarize_characteristics
from PyNCBI.core.reporter import get_reporter
from PyNCBI.core.tables import Table, by_id, from_records, iter_records
from PyNCBI.core.types import validate_accession
from PyNCBI.io.http_client import get_default_client
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser

if TYPE_CHECKING:
    from pathlib import Path

    from PyNCBI.core.models import CharacteristicSummary
    from PyNCBI.core.protocols import HttpClient

_COMPARISONS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _query_value(node: ast.AST, record: dict[str, Any], aliases: dict[str, str]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        name = aliases.get(node.id, node.id)
        if name not in record:
            raise ValueError(f"Unknown field {name!r}")
        return record[name]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_query_value(item, record, aliases) for item in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _query_value(node.operand, record, aliases)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _query_value(node.operand, record, aliases)
        if not isinstance(value, (int, float)):
            raise ValueError("Unary signs require numbers")
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BoolOp):
        values = [_query_value(value, record, aliases) for value in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _query_value(node.left, record, aliases)
        for operation, operand in zip(node.ops, node.comparators, strict=True):
            right = _query_value(operand, record, aliases)
            if isinstance(operation, (ast.In, ast.NotIn)):
                matched = left in right if right is not None else False
                matched = not matched if isinstance(operation, ast.NotIn) else matched
            elif type(operation) in _COMPARISONS:
                if isinstance(right, (int, float)) and isinstance(left, str):
                    try:
                        left = float(left)
                    except ValueError:
                        left = None
                try:
                    matched = _COMPARISONS[type(operation)](left, right)
                except TypeError:
                    matched = False
            else:
                raise ValueError("Unsupported comparison operator")
            if not matched:
                return False
            left = right
        return True
    raise ValueError(
        "Filters allow field comparisons, membership, and/or/not; calls and attributes are not allowed"
    )


def _apply_sample_filter(value: Table, where: str) -> Table:
    """Filter native metadata; never execute supplied expressions as Python code."""
    if not where or not where.strip():
        return value
    expression = where.strip()
    names = value["columns"]
    records = list(iter_records(value))
    if any(
        token in expression for token in ("==", "!=", ">", "<", " in ", " and ", " or ", "`", "__")
    ) or expression.startswith("not "):
        aliases: dict[str, str] = {}

        def replace(match: re.Match[str]) -> str:
            name = f"_field_{len(aliases)}"
            aliases[name] = match.group(1)
            return name

        expression = re.sub(r"`([^`]+)`", replace, expression)
        for field in sorted(names, key=len, reverse=True):
            if (
                not field.isidentifier()
                and expression.startswith(field)
                and expression[len(field) :].lstrip().startswith(("==", "!=", ">", "<"))
            ):
                alias = f"_field_{len(aliases)}"
                aliases[alias] = field
                expression = alias + expression[len(field) :]
                break
        try:
            root = ast.parse(expression, mode="eval").body
            # Validate independently of the number of records, including empty results.
            _query_value(root, dict.fromkeys(names), aliases)
            matched = [record for record in records if _query_value(root, record, aliases)]
        except (SyntaxError, ValueError, TypeError) as exc:
            raise ValueError(
                f"Invalid filter {where!r}: {exc}. Available columns: {', '.join(names[:12])}"
            ) from exc
        return from_records(matched, names)
    for separator in ("=", ":"):
        if separator in expression:
            key, wanted = (part.strip().strip("'\"`") for part in expression.split(separator, 1))
            matched_field = next((name for name in names if name.lower() == key.lower()), None)
            if matched_field is None:
                raise ValueError(
                    f"Unknown filter field {key!r}. Available columns: {', '.join(names[:12])}"
                )
            return from_records(
                (
                    record
                    for record in records
                    if wanted.lower() in str(record.get(matched_field, "")).lower()
                ),
                names,
            )
    needle = expression.lower()
    return from_records(
        (
            record
            for record in records
            if any(needle in str(item).lower() for item in record.values())
        ),
        names,
    )


def fetch_sample_tables(
    gse_id: str, *, http_client: HttpClient | None = None, parser: SOFTParser | None = None
) -> tuple[Table, Table]:
    """Acquire all batch sample metadata once, returning native tables with gsm_id fields."""
    gse_id = validate_accession(gse_id, "GSE")
    client = http_client or get_default_client()
    return (parser or get_parser()).parse_gse_samples_batch(
        client.get_text(get_config().get_ncbi_batch_gsm_url(gse_id))
    )


def combine_sample_tables(info: Table, characteristics: Table) -> Table:
    """Attach unpacked descriptive fields by GSM accession; retain published metadata."""
    lookup = by_id(characteristics, "gsm_id") if characteristics["rows"] else {}
    records = []
    for item in iter_records(info):
        record = {key: value for key, value in item.items() if key != "^SAMPLE"}
        for key, value in lookup.get(str(item["gsm_id"]), {}).items():
            record.setdefault(key, value)
        records.append(record)
    names = list(
        dict.fromkeys(
            [
                "gsm_id",
                "title",
                *characteristics["columns"],
                "source_name_ch1",
                "organism_ch1",
                "platform_id",
                "library_strategy",
                "molecule_ch1",
                *info["columns"],
            ]
        )
    )
    present = {key for record in records for key in record}
    return from_records(records, [name for name in names if name in present])


def extract_gse_samples(
    gse_id: str,
    *,
    where: str | None = None,
    save_to: str | Path | None = None,
    http_client: HttpClient | None = None,
    parser: SOFTParser | None = None,
) -> tuple[Table, Table, list[CharacteristicSummary]]:
    """Return combined metadata, characteristics and summaries using one batch request."""
    gse_id = validate_accession(gse_id, "GSE")
    with get_reporter().task(
        "GEO", "extract_gse_samples", f"Fetching batch sample metadata for {gse_id}", target=gse_id
    ):
        info, characteristics = fetch_sample_tables(gse_id, http_client=http_client, parser=parser)
        combined = combine_sample_tables(info, characteristics)
        if where:
            combined = _apply_sample_filter(combined, where)
            selected = set(by_id(combined, "gsm_id")) if combined["rows"] else set()
            characteristics = from_records(
                (row for row in iter_records(characteristics) if row["gsm_id"] in selected),
                characteristics["columns"],
            )
        if save_to is not None:
            save_data(combined, save_to)
        return combined, characteristics, summarize_characteristics(characteristics)
