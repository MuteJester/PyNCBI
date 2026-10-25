"""Small native fixture/assertion helpers for the public columns/rows contract.

These return plain Python data. They do not provide DataFrame-compatible objects.
"""

from __future__ import annotations

import math

from PyNCBI.core.tables import column, from_records, is_table, iter_records, project, table


def labels(values, name=None):
    return name, list(values)


def fixture_table(data=None, *, index=None, columns=None, dtype=None):
    if data is None:
        return table(columns or [])
    if isinstance(data, dict):
        names = list(columns) if columns is not None else list(data)
        count = len(next(iter(data.values()))) if data else 0
        value = table(names, ([data[name][i] for name in names] for i in range(count)))
    elif data and isinstance(data[0], dict):
        value = from_records(data, columns)
    else:
        value = table(columns or [str(i) for i in range(len(data[0]) if data else 0)], data)
    if index is not None:
        name, identifiers = (
            index
            if isinstance(index, tuple) and len(index) == 2 and isinstance(index[1], list)
            else (None, list(index))
        )
        canonical = (
            "gene_id"
            if name == "GeneID"
            else "gsm_id"
            if name == "gsm_id" or (identifiers and str(identifiers[0]).startswith("GSM"))
            else "gse_id"
            if name == "gse_id"
            else "feature_id"
        )
        assert len(identifiers) == len(value["rows"])
        value = table(
            [canonical, *value["columns"]],
            (
                [str(identifier), *row]
                for identifier, row in zip(identifiers, value["rows"], strict=True)
            ),
        )
    return value


def fixture_record(data=None, *, index=None, name=None, dtype=None):
    if data is None:
        return {}
    if isinstance(data, dict):
        return dict(data)
    return dict(zip(index or range(len(data)), data, strict=True))


def data_fields(value):
    if not is_table(value):
        return value.columns
    names = value["columns"]
    return (
        names[1:]
        if names and names[0] in {"feature_id", "gsm_id", "gse_id", "gene_id", "accession"}
        else names
    )


def identifiers(value):
    return [row[0] for row in value["rows"]] if is_table(value) else list(value)


def data_shape(value):
    return (len(value["rows"]), len(data_fields(value))) if is_table(value) else value.shape


def table_empty(value):
    return not value["rows"] if is_table(value) else not value


def record(value, key):
    if not is_table(value):
        return value[key]
    return next(item for item in iter_records(value) if item[value["columns"][0]] == key)


def cell(value, key, name):
    return record(value, key)[name]


def cell_at(value, row, position):
    return value["rows"][row][value["columns"].index(data_fields(value)[position])]


def record_at(value, position):
    return dict(zip(value["columns"], value["rows"][position], strict=True))


def select_ids(value, keys):
    lookup = {row[0]: row for row in value["rows"]}
    return table(value["columns"], (lookup[key] for key in keys))


def identify(value, field, drop=True):
    selected = project(value, [field, *[name for name in value["columns"] if name != field]])
    selected["columns"][0] = "feature_id"
    return selected


def is_missing(value):
    return value is None or isinstance(value, float) and math.isnan(value)


def assert_equal(actual, expected, **options):
    if isinstance(actual, dict) and isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in actual:
            assert_equal(actual[key], expected[key])
    elif isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            assert_equal(left, right)
    elif (
        isinstance(actual, float)
        and isinstance(expected, float)
        and math.isnan(actual)
        and math.isnan(expected)
    ):
        return
    else:
        assert actual == expected


def values(value, name):
    return column(value, name)


def read_csv_fixture(path, **options):
    import csv

    from PyNCBI.parsing.tabular import measurement

    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        names = next(reader)
        return table(
            names, ([row[0], *[measurement(value) for value in row[1:]]] for row in reader)
        )
