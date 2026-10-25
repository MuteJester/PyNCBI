"""Measurements must survive the round trip exactly.

PyNCBI's first promise is that it never alters scientific data. The values below are the
ones that silently change when a parser is careless: long decimals that lose precision,
identifiers whose leading zeros disappear, counts beyond float precision, numbers in
scientific notation, and the several spellings of "missing".
"""

from __future__ import annotations

import gzip

import pandas as pd
import pytest

from PyNCBI.core.config import Config
from PyNCBI.core.types import table_read_options

# A beta value with 17 significant digits, an ID with leading zeros, a count past 2**53,
# scientific notation, a negative, and three spellings of missing.
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
        gzip.compress(MATRIX_TEXT.encode("utf-8")),
        "application/x-gzip",
    )
    return local_http


def test_series_matrix_values_are_not_rounded_or_retyped(matrix_server):
    from PyNCBI.domains.geo import load_series_matrix

    matrix = load_series_matrix("GSE500").matrix_df

    # Full double precision, not a rounded display value.
    assert matrix.loc["cg00000029", "GSM1"] == pytest.approx(0.12345678901234567, rel=0, abs=1e-17)
    # Scientific notation survives in both spellings.
    assert matrix.loc["probe_sci", "GSM1"] == pytest.approx(1.23e-45)
    assert matrix.loc["probe_sci", "GSM2"] == pytest.approx(6.02e23)
    assert matrix.loc["0001234", "GSM2"] == pytest.approx(-1.5)


def test_identifiers_keep_their_leading_zeros(matrix_server):
    """`0001234` is an identifier, not the number 1234."""
    from PyNCBI.domains.geo import load_series_matrix

    matrix = load_series_matrix("GSE500").matrix_df
    assert "0001234" in matrix.index
    assert all(isinstance(value, str) for value in matrix.index)


def test_missing_values_stay_missing_and_are_never_filled(matrix_server):
    from PyNCBI.domains.geo import load_series_matrix

    matrix = load_series_matrix("GSE500").matrix_df
    assert pd.isna(matrix.loc["probe_na", "GSM1"])
    assert pd.isna(matrix.loc["probe_na", "GSM2"])
    assert pd.isna(matrix.loc["probe_nan", "GSM1"])
    assert (matrix.fillna(-999) == -999).to_numpy().sum() >= 4, "missing cells must not be filled"


def test_saving_and_reloading_a_table_does_not_change_a_single_value(matrix_server, tmp_path):
    """`-o table.csv` must be a faithful copy, not a reformatted one."""
    from PyNCBI.core.formatter import save_data
    from PyNCBI.domains.geo import load_series_matrix

    matrix = load_series_matrix("GSE500").matrix_df
    destination = tmp_path / "matrix.csv"
    save_data(matrix, destination)

    # The file itself must carry every digit, so nothing is lost on the way out.
    assert "0.12345678901234566" in destination.read_text(encoding="utf-8")

    reloaded = pd.read_csv(destination, index_col=0, **table_read_options())
    reloaded.index = reloaded.index.astype(str)
    assert list(reloaded.index) == list(matrix.index)
    pd.testing.assert_frame_equal(
        reloaded.astype(float), matrix.astype(float), check_exact=True, check_names=False
    )


def test_cohort_filtering_selects_rows_without_touching_values():
    """`--where` is a filter; it must never rewrite the cells it keeps."""
    from PyNCBI.domains.geo.samples import _apply_sample_filter

    frame = pd.DataFrame(
        {
            "title": ["S1", "S2", "S3"],
            "age": ["62", "34", "71"],
            "beta": [0.12345678901234567, 0.5, 0.9],
            "severity": ["Severe", "Moderate", "Severe"],
        },
        index=["GSM1", "GSM2", "GSM3"],
    )
    filtered = _apply_sample_filter(frame, "severity == 'Severe'")

    assert list(filtered.index) == ["GSM1", "GSM3"]
    pd.testing.assert_frame_equal(filtered, frame.loc[["GSM1", "GSM3"]], check_exact=True)
