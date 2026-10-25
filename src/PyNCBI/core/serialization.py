"""Strict JSON output shared by files, CLI envelopes and MCP transports."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import PurePath
from typing import Any

from PyNCBI.core.tables import Table, iter_records


def json_value(value: Any) -> Any:
    """Return a JSON-compatible value without rounding finite measurements."""
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if hasattr(value, "to_dict"):
        return json_value(value.to_dict())
    return str(value)


def json_dumps(
    value: Any,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    separators: tuple[str, str] | None = None,
) -> str:
    """Encode strict JSON, retaining the full precision of finite Python floats."""
    return json.dumps(
        json_value(value),
        indent=indent,
        sort_keys=sort_keys,
        separators=separators,
        allow_nan=False,
        ensure_ascii=True,
    )


def table_records(value: Table) -> list[dict[str, Any]]:
    """Materialize row dictionaries only when the caller requests records."""
    return list(iter_records(value))
