"""Streaming CSV/TSV parsing into native Python values, without dtype inference."""

from __future__ import annotations

import bz2
import contextlib
import csv
import gzip
import io
import lzma
import re
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from PyNCBI.core.exceptions import DataError
from PyNCBI.core.files import file_name
from PyNCBI.core.tables import Table, table

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

_INTEGER = re.compile(r"[+-]?\d+\Z")
_MISSING = frozenset(
    (
        "",
        "NA",
        "N/A",
        "NaN",
        "nan",
        "null",
        "NULL",
        "None",
        "#N/A",
        "#NA",
        "#N/A N/A",
        "n/a",
        "<NA>",
        "-NaN",
        "-nan",
        "1.#IND",
        "-1.#IND",
        "1.#QNAN",
        "-1.#QNAN",
    )
)


def measurement(value: str) -> int | float | str | None:
    """Preserve exact integers and correctly rounded floats; retain other source text."""
    if value in _MISSING:
        return None
    if _INTEGER.fullmatch(value.strip()):
        return int(value)
    try:
        return float(value)
    except ValueError:
        return value


def _delimited_rows(
    source: Iterable[str],
    filename: str,
    *,
    text_columns: Sequence[str],
    id_field: str,
    numeric: bool,
) -> tuple[list[str], Iterator[list[Any]]]:
    lines = iter(source)
    first = next((line for line in lines if line.strip()), "")
    if not first:
        return [], iter(())
    if first.lstrip().lower().startswith(("<html", "<!doctype html")):
        raise DataError(f"HTML response received instead of {filename}")
    name = file_name(filename).lower()
    if name.endswith(".csv") or ".csv." in name:
        delimiter = ","
    else:
        try:
            delimiter = csv.Sniffer().sniff(first, delimiters="\t,").delimiter
        except csv.Error:
            delimiter = "\t"
    reader = csv.reader(chain((first,), lines), delimiter=delimiter, strict=True)
    try:
        original = next(reader)
    except csv.Error as exc:
        raise DataError(f"Invalid delimited table {filename}: {exc}") from exc
    names = [id_field, *original[1:]]
    if len(set(names)) != len(names):
        raise DataError(f"Duplicate columns in {filename}: {names}")

    def rows() -> Iterator[list[Any]]:
        try:
            for number, values in enumerate(reader, start=2):
                if not values:
                    continue
                if len(values) != len(names):
                    raise DataError(
                        f"Ragged row {number} in {filename}: expected {len(names)} fields, got {len(values)}"
                    )
                yield [
                    value
                    if index == 0 or not numeric or original[index] in text_columns
                    else measurement(value)
                    for index, value in enumerate(values)
                ]
        except csv.Error as exc:
            raise DataError(f"Invalid delimited table {filename}: {exc}") from exc

    return names, rows()


def iter_delimited_records(
    source: Iterable[str],
    filename: str = "table.tsv",
    *,
    text_columns: Sequence[str] = (),
    id_field: str = "feature_id",
    numeric: bool = True,
) -> Iterator[dict[str, Any]]:
    """Stream records; identifiers remain text, including literal NA markers."""
    names, rows = _delimited_rows(
        source, filename, text_columns=text_columns, id_field=id_field, numeric=numeric
    )
    for row in rows:
        yield dict(zip(names, row, strict=True))


def parse_delimited_table(
    text: str,
    filename: str,
    *,
    text_columns: Sequence[str] = (),
    id_field: str = "feature_id",
    numeric: bool = True,
) -> Table:
    """Return a plain columns/rows payload. Use iter_delimited_records for large streams."""
    names, rows = _delimited_rows(
        io.StringIO(text), filename, text_columns=text_columns, id_field=id_field, numeric=numeric
    )
    return table(names, rows)


def read_delimited_table(
    path: Path,
    filename: str | None = None,
    *,
    text_columns: Sequence[str] = (),
    id_field: str = "feature_id",
    numeric: bool = True,
) -> Table:
    """Read a local text/gzip/bzip2/xz table using Python's built-in codecs."""
    with open_table_text(path) as source:
        names, rows = _delimited_rows(
            source,
            filename or path.name,
            text_columns=text_columns,
            id_field=id_field,
            numeric=numeric,
        )
        return table(names, rows)


@contextlib.contextmanager
def open_table_text(path: Path) -> Iterator[TextIO]:
    """One codec policy for full and streaming local table readers."""
    name = path.name.lower()
    opener = (
        gzip.open
        if name.endswith(".gz")
        else bz2.open
        if name.endswith(".bz2")
        else lzma.open
        if name.endswith(".xz")
        else Path.open
    )
    with opener(path, "rt", encoding="utf-8") as source:
        yield source
