"""Strict JSON output shared by files, CLI envelopes and MCP transports."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import PurePath
from typing import Any

import numpy as np
import pandas as pd


def json_value(value: Any) -> Any:
    """Return a JSON-compatible value without rounding finite measurements."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset, np.ndarray)):
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


def json_dumps(value: Any, *, indent: int | None = None) -> str:
    """Encode strict JSON, retaining the full precision of finite Python floats."""
    return json.dumps(json_value(value), indent=indent, allow_nan=False, ensure_ascii=True)


def table_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Export row identifiers as columns, avoiding collisions with data column names."""
    names: list[str] = []
    used = set(frame.columns)
    for i, name in enumerate(frame.index.names):
        candidate = (
            str(name)
            if name is not None
            else ("index" if frame.index.nlevels == 1 else f"level_{i}")
        )
        while candidate in used:
            candidate = f"_{candidate}"
        names.append(candidate)
        used.add(candidate)
    return [
        {str(k): v for k, v in row.items()}
        for row in frame.rename_axis(names).reset_index().to_dict(orient="records")
    ]
