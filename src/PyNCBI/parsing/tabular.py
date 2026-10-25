"""Delimited measurement tables: one delimiter, identifier and precision policy."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

import pandas as pd

from PyNCBI.core.files import file_name
from PyNCBI.core.types import table_read_options

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def parse_delimited_table(
    text: str, filename: str, *, text_columns: Sequence[str] = (), keep_id_column: bool = False
) -> pd.DataFrame:
    """Parse a feature-indexed table without rounding measurements or inferring IDs."""
    return read_delimited_table(
        StringIO(text), filename, text_columns=text_columns, keep_id_column=keep_id_column
    )


def read_delimited_table(
    source: StringIO | Path,
    filename: str,
    *,
    text_columns: Sequence[str] = (),
    keep_id_column: bool = False,
) -> pd.DataFrame:
    """Apply the same cell policy to downloaded text and pandas-supported local files."""
    header = ""
    if isinstance(source, StringIO):
        header = next((line for line in source if line.strip() and not line.startswith("!")), "")
        source.seek(0)
    name = file_name(filename).lower()
    sep = (
        ","
        if name.endswith(".csv") or ".csv." in name or header.count(",") > header.count("\t")
        else "\t"
    )
    frame = pd.read_csv(
        source,
        sep=sep,
        low_memory=False,
        comment="!",
        **table_read_options(text_columns=text_columns),
    )
    # Setting the index during read_csv re-applies NA inference to its values,
    # even with a string converter (a legitimate feature ID "NA" becomes NaN).
    return frame if keep_id_column else frame.set_index(frame.columns[0])
