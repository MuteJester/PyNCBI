"""Native values survive retrieval, filtering, caching and export without rounding."""

from __future__ import annotations

import gzip

import pytest

from PyNCBI.core.config import Config
from PyNCBI.core.tables import by_id, from_records

MATRIX_TEXT = """!Series_title\t"Precision"
!Sample_geo_accession\t"GSM1"\t"GSM2"
!series_matrix_table_begin
"ID_REF"\t"GSM1"\t"GSM2"
"cg00000029"\t0.12345678901234567\t0.9
"0001234"\t9007199254740993\t-1.5
"probe_sci"\t1.23e-45\t6.02E23
"probe_na"\tNA\t
"probe_nan"\tNaN\tnull
!series_matrix_table_end
"""


@pytest.fixture
def matrix_server(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    local_http.add("/query?acc=GSE500", "^SERIES = GSE500\n!Series_title = Precision\n")
    local_http.add(
        "/geo/series/GSEnnn/GSE500/matrix/GSE500_series_matrix.txt.gz",
        gzip.compress(MATRIX_TEXT.encode()),
        "application/x-gzip",
    )
    return local_http


def test_series_matrix_values_are_not_rounded_or_retyped(matrix_server):
    from PyNCBI.domains.geo import load_series_matrix

    rows = by_id(load_series_matrix("GSE500").matrix, "feature_id")
    assert rows["cg00000029"]["GSM1"] == float("0.12345678901234567")
    assert rows["0001234"]["GSM1"] == 9007199254740993
    assert isinstance(rows["0001234"]["GSM1"], int)
    assert rows["probe_sci"]["GSM1"] == 1.23e-45
    assert rows["probe_sci"]["GSM2"] == 6.02e23
    assert rows["0001234"]["GSM2"] == -1.5


def test_identifiers_keep_their_leading_zeros(matrix_server):
    from PyNCBI.domains.geo import load_series_matrix

    rows = by_id(load_series_matrix("GSE500").matrix, "feature_id")
    assert "0001234" in rows
    assert all(isinstance(key, str) for key in rows)


def test_missing_values_stay_missing_and_are_never_filled(matrix_server):
    from PyNCBI.domains.geo import load_series_matrix

    rows = by_id(load_series_matrix("GSE500").matrix, "feature_id")
    assert rows["probe_na"] == {"feature_id": "probe_na", "GSM1": None, "GSM2": None}
    assert rows["probe_nan"] == {"feature_id": "probe_nan", "GSM1": None, "GSM2": None}


def test_saving_and_reloading_a_table_does_not_change_a_single_value(matrix_server, tmp_path):
    from PyNCBI.core.formatter import save_data
    from PyNCBI.domains.geo import load_series_matrix, read_supplementary_table

    value = load_series_matrix("GSE500").matrix
    destination = tmp_path / "matrix.csv"
    save_data(value, destination)
    assert "0.12345678901234566" in destination.read_text(encoding="utf-8")
    assert "9007199254740993" in destination.read_text(encoding="utf-8")
    assert read_supplementary_table(destination) == value


def test_cohort_filtering_selects_rows_without_touching_values():
    from PyNCBI.domains.geo.samples import _apply_sample_filter

    value = from_records(
        [
            {"gsm_id": "GSM1", "age": "62", "beta": 0.12345678901234567, "severity": "Severe"},
            {"gsm_id": "GSM2", "age": "34", "beta": 0.5, "severity": "Moderate"},
            {"gsm_id": "GSM3", "age": "71", "beta": 0.9, "severity": "Severe"},
        ]
    )
    filtered = _apply_sample_filter(value, "severity == 'Severe'")
    assert filtered == {"columns": value["columns"], "rows": [value["rows"][0], value["rows"][2]]}


def test_delimited_reader_preserves_symbol_prefixes_and_quoted_multiline_text():
    from PyNCBI.parsing.tabular import parse_delimited_table

    value = parse_delimited_table(
        'id,GSM1,note\n!probe,3,"line one\n\nline three"\n^probe,4,plain\n',
        "data.csv",
        text_columns=["note"],
    )
    assert value == {
        "columns": ["feature_id", "GSM1", "note"],
        "rows": [["!probe", 3, "line one\n\nline three"], ["^probe", 4, "plain"]],
    }
