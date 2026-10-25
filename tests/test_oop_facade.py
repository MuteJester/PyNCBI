"""The object layer (GSE/GSM/GEOReader) must be a facade, not a second implementation.

These tests pin the invariants that keep it one: the object and functional APIs agree on
the sample table and spend the same requests, and the shared idioms (tabular file
detection, supplementary URL normalisation) have exactly one definition each.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from PyNCBI.core.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "PyNCBI"

GSE_SOFT = """^SERIES = GSE200
!Series_title = Facade series
!Series_platform_id = GPL13534
!Series_sample_id = GSM2001
!Series_sample_id = GSM2002
!Series_supplementary_file = {base}/suppl/GSE200_matrix.txt.gz
!Series_supplementary_file = NONE
"""

BATCH_SOFT = """^SAMPLE = GSM2001
!Sample_title = Case one
!Sample_characteristics_ch1 = disease state: Fibromyalgia
!Sample_characteristics_ch1 = age: 44
!Sample_organism_ch1 = Homo sapiens
!Sample_platform_id = GPL13534
!Sample_supplementary_file = {base}/suppl/GSM2001_signal.txt.gz
^SAMPLE = GSM2002
!Sample_title = Case two
!Sample_characteristics_ch1 = disease state: Control
!Sample_characteristics_ch1 = age: 51
!Sample_organism_ch1 = Homo sapiens
!Sample_platform_id = GPL13534
!Sample_supplementary_file = NONE
"""


@pytest.fixture
def facade_geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(
        Config, "NCBI_QUERY_URL_TEMPLATE", base + "/geo/query/acc.cgi?acc={accession}"
    )
    monkeypatch.setattr(
        Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/geo/query/batch.cgi?acc={accession}"
    )
    local_http.add("/geo/query/acc.cgi?acc=GSE200", GSE_SOFT.format(base=base))
    local_http.add("/geo/query/batch.cgi?acc=GSE200", BATCH_SOFT.format(base=base))
    return local_http


def test_series_object_and_function_agree_on_the_sample_table(facade_geo):
    """One series, two entry points, one table: same rows, same values, one extra request."""
    from PyNCBI import GSE, extract_gse_samples

    samples_df, chars_df, summaries = extract_gse_samples("GSE200")
    functional_requests = len(facade_geo.hits)

    gse = GSE("GSE200", shell_only=True)
    object_requests = len(facade_geo.hits) - functional_requests

    assert list(gse.characteristics_df.index) == list(chars_df.index)
    assert sorted(gse.characteristics_df.columns) == sorted(chars_df.columns)
    pd.testing.assert_frame_equal(
        gse.characteristics_df.sort_index(axis=1), chars_df.sort_index(axis=1)
    )
    assert sorted(gse.samples_df.columns) == sorted(samples_df.columns)
    assert [s.name for s in gse.characteristics_summary] == [s.name for s in summaries]
    # The object spends exactly one request more than the function: the series header.
    assert object_requests == functional_requests + 1


def test_series_object_drops_placeholder_supplementary_entries(facade_geo):
    from PyNCBI import GSE

    gse = GSE("GSE200", shell_only=True)
    assert gse.supplementary_files == [facade_geo.url("/suppl/GSE200_matrix.txt.gz")]
    assert gse["GSM2002"].supplementary_files == []
    assert gse["GSM2001"].supplementary_files == [facade_geo.url("/suppl/GSM2001_signal.txt.gz")]


def test_tabular_extension_set_is_defined_once() -> None:
    """Every module that needs to know what counts as a tabular file asks the same place."""
    vocabulary = {
        ".csv",
        ".csv.gz",
        ".parquet",
        ".tab",
        ".tab.gz",
        ".tsv",
        ".tsv.gz",
        ".txt",
        ".txt.gz",
    }
    definitions: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Tuple):
                continue
            literals = {
                e.value.lower()
                for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
            if not literals or len(literals) != len(node.elts):
                continue
            # A tuple of nothing but tabular extensions is the shared constant, or a copy
            # of it. A tuple that also names archives or images is a different concept.
            if {".csv.gz", ".tsv.gz", ".txt.gz"} <= literals <= vocabulary:
                definitions.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert len(definitions) == 1, (
        f"the tabular extension set is defined in {len(definitions)} places: {definitions}; "
        "import PyNCBI.core.types.TABULAR_EXTENSIONS instead"
    )


def test_supplementary_url_normalisation_is_defined_once() -> None:
    """The `drop NONE, rewrite ftp://` idiom lives in one helper, not in every caller."""
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.name == "urls.py" and path.parent.name == "io":
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if 'upper() != "NONE"' in line or 'upper() == "NONE"' in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert offenders == [], (
        f"these call sites re-implement supplementary URL normalisation: {offenders}; "
        "call PyNCBI.io.urls.supplementary_urls instead"
    )


def test_remote_parquet_table_is_never_parsed_as_delimited_text(local_http):
    """A remote .parquet must not be run through the CSV reader and returned as garbage."""
    from PyNCBI.domains.geo import read_supplementary_table

    frame = pd.DataFrame({"gene": ["A1BG", "A2M"], "GSM1": [3, 7]}).set_index("gene")
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pyarrow_available = False
        payload = b"PAR1\x00\x00garbage-not-text\xff\xfePAR1"
    else:
        pyarrow_available = True
        import io

        buf = io.BytesIO()
        frame.to_parquet(buf)
        payload = buf.getvalue()

    url = local_http.add("/suppl/GSE300_counts.parquet", payload, "application/octet-stream")

    if pyarrow_available:
        got = read_supplementary_table(url)
        pd.testing.assert_frame_equal(got, frame)
    else:
        with pytest.raises(Exception) as excinfo:  # noqa: B017, PT011
            read_supplementary_table(url)
        assert "parquet" in str(excinfo.value).lower() or "pyarrow" in str(excinfo.value).lower()


def test_series_object_matches_accessions_without_confusing_prefixes(facade_geo):
    """GSM1 must not consume GSM10's measurements because it is a substring."""
    from PyNCBI import GSE

    def ids(text):
        return text.replace("GSM2001", "GSM1").replace("GSM2002", "GSM10")

    facade_geo.add("/geo/query/acc.cgi?acc=GSE200", ids(GSE_SOFT.format(base=facade_geo.base_url)))
    facade_geo.add(
        "/geo/query/batch.cgi?acc=GSE200", ids(BATCH_SOFT.format(base=facade_geo.base_url))
    )
    study = GSE("GSE200", shell_only=True)
    study._match_columns_to_gsms(pd.DataFrame({"GSM10": [0.75]}, index=["probe1"]))
    assert study["GSM1"].data is None
    assert study["GSM10"].data.loc["probe1"] == 0.75


def test_series_object_does_not_guess_between_ambiguous_titles(facade_geo):
    from PyNCBI import GSE

    shared_title = BATCH_SOFT.replace("Case one", "shared").replace("Case two", "shared")
    facade_geo.add("/geo/query/batch.cgi?acc=GSE200", shared_title.format(base=facade_geo.base_url))
    study = GSE("GSE200", shell_only=True)
    study._match_columns_to_gsms(pd.DataFrame({"shared": [0.75]}, index=["probe1"]))
    assert all(sample.data is None for sample in study.values())
