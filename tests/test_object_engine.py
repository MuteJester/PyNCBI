"""Public GEO objects use one loading engine and source-aware, data-only snapshots."""

from __future__ import annotations

import gzip
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from PyNCBI import GSE, GSM, Config
from PyNCBI.core.exceptions import PyNCBIError
from PyNCBI.domains.expression import load_expression
from PyNCBI.domains.geo import (
    download_geo_files,
    load_rnaseq_counts,
    load_series_matrix,
    read_supplementary_table,
    rnaseq_counts_url,
)
from PyNCBI.domains.geo.records import fetch_sample_record
from PyNCBI.domains.geo.tables import load_sample_record
from PyNCBI.io.cache import LocalCache
from PyNCBI.io.http_client import NCBIClient

PRECISION = 0.12345678901234567


@pytest.fixture
def study(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_DATA_URL_TEMPLATE", base + "/data?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    local_http.add(
        "/query?acc=GSE1",
        f"""^SERIES = GSE1
!Series_title = Object engine
!Series_type = Expression profiling by array
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL1
!Series_sample_id = GSM1
!Series_sample_id = GSM10
!Series_supplementary_file = {base}/supp/first.tsv.gz
!Series_supplementary_file = {base}/supp/second.tsv.gz
""",
    )
    samples = [
        f"""^SAMPLE = {gsm}
!Sample_title = {title}
!Sample_series_id = GSE1
!Sample_platform_id = GPL1
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = group: case
!Sample_characteristics_ch2 = age: 45
!Sample_data_row_count = 2
"""
        for gsm, title in [("GSM1", "first"), ("GSM10", "second")]
    ]
    local_http.add("/batch?acc=GSE1", "".join(samples))
    for gsm, sample in zip(("GSM1", "GSM10"), samples, strict=True):
        local_http.add("/query?acc=" + gsm, sample)
        local_http.add(
            "/data?acc=" + gsm,
            f"!sample_table_begin\nID_REF\tVALUE\n0001234\t{PRECISION!r}\n0005678\t0.75\n!sample_table_end\n",
        )
    local_http.add_gzip(
        "/supp/first.tsv.gz",
        f"feature\tGSM1\tGSM10\n0001234\t{PRECISION!r}\t0.25\n0005678\t0.75\t0.5\n",
    )
    local_http.add_gzip(
        "/supp/second.tsv.gz", "feature\tGSM1\tGSM10\n0001234\t9.5\t8.5\n0005678\t7.5\t6.5\n"
    )
    local_http.add_gzip(
        "/geo/series/GSEnnn/GSE1/matrix/GSE1_series_matrix.txt.gz",
        f"""!Series_title\t"Object engine"
!Sample_geo_accession\t"GSM1"\t"GSM10"
!series_matrix_table_begin
ID_REF\tGSM1\tGSM10
0001234\t{PRECISION!r}\t0.25
0005678\t0.75\t0.5
!series_matrix_table_end
""",
    )
    local_http.add(
        rnaseq_counts_url("GSE1")[len(base) :],
        gzip.compress(b"GeneID\tGSM1\tGSM10\n001234\t11\t12\n005678\t21\t22\n"),
        "application/gzip",
    )
    return local_http


@pytest.fixture
def client():
    with NCBIClient(app_config=Config(http_cache_ttl=0)) as value:
        yield value


@pytest.mark.parametrize("mode", ["auto", "matrix", "rnaseq", "supp"])
def test_series_modes_match_functional_measurements(study, client, tmp_path, mode):
    if mode == "auto":
        expected = load_expression("GSE1", http_client=client).matrix_df
    elif mode == "matrix":
        expected = load_series_matrix("GSE1", http_client=client).matrix_df
    elif mode == "rnaseq":
        expected = load_rnaseq_counts("GSE1", http_client=client)
    else:
        expected = read_supplementary_table(study.url("/supp/first.tsv.gz"), http_client=client)
    kwargs = {"file_index": 0} if mode == "supp" else {}
    obj = GSE("GSE1", mode=mode, _http_client=client, _cache=LocalCache(tmp_path), **kwargs)
    pd.testing.assert_frame_equal(obj.to_dataframe("data"), expected, check_exact=True)


def test_auto_does_not_download_metadata_twice(study, client, tmp_path):
    load_expression("GSE1", http_client=client)
    functional = len(study.hits)
    GSE("GSE1", _http_client=client, _cache=LocalCache(tmp_path))
    assert len(study.hits) - functional == functional


def test_cache_separates_sources_and_selected_files(study, client, tmp_path):
    cache = LocalCache(tmp_path)
    requests = [
        {"mode": "matrix"},
        {"mode": "rnaseq"},
        {"mode": "supp", "file_index": 0},
        {"mode": "supp", "file_index": 1},
        {"mode": "supp", "file_pattern": "second"},
    ]
    expected = []
    for request in requests:
        obj = GSE("GSE1", _http_client=client, _cache=cache, **request)
        expected.append(obj.to_dataframe("data"))
    assert expected[0].iloc[0, 0] == PRECISION
    assert expected[1].iloc[0, 0] == 11
    assert expected[3].iloc[0, 0] == 9.5
    before = len(study.hits)
    for request, frame in zip(requests, expected, strict=True):
        obj = GSE("GSE1", _http_client=client, _cache=cache, **request)
        pd.testing.assert_frame_equal(obj.to_dataframe("data"), frame, check_exact=True)
    assert len(study.hits) == before
    assert obj.clear_cache()
    assert not cache.list_keys("GSE1*")


def test_shell_cache_contains_no_measurements_even_after_full_fetch(study, client, tmp_path):
    cache = LocalCache(tmp_path)
    GSE("GSE1", mode="matrix", _http_client=client, _cache=cache)
    GSM("GSM1", _http_client=client, _cache=cache)
    before = len(study.hits)
    series = GSE("GSE1", shell_only=True, _http_client=client, _cache=cache)
    sample = GSM("GSM1", shell_only=True, _http_client=client, _cache=cache)
    assert series.to_dataframe("data").empty and sample.data is None
    assert all(s.data is None for s in series.values())
    assert len(study.hits) == before


def test_arbitrary_stateful_selectors_do_not_reuse_full_data(study, client, tmp_path):
    class Selector:
        counter = 0

        def select(self, files, gse):
            selected = self.counter
            self.counter += 1
            return selected

    selector = Selector()
    cache = LocalCache(tmp_path)
    a = GSE("GSE1", mode="supp", file_selector=selector, _http_client=client, _cache=cache)
    b = GSE("GSE1", mode="supp", file_selector=selector, _http_client=client, _cache=cache)
    assert a.to_dataframe("data").iloc[0, 0] == PRECISION
    assert b.to_dataframe("data").iloc[0, 0] == 9.5
    assert not a.is_cached() and not b.is_cached()


def test_gsm_gse_and_download_share_exact_on_page_values(study, client, tmp_path):
    cache = LocalCache(tmp_path / "cache")
    gsm = GSM("GSM1", _http_client=client, _cache=cache)
    series = GSE("GSE1", mode="per_gsm", n_threads=2, _http_client=client, _cache=cache)
    loaded = load_sample_record(fetch_sample_record("GSM1", http_client=client), http_client=client)
    path = download_geo_files("GSM1", tmp_path / "sample.csv", source="table", http_client=client)[
        0
    ]
    for frame in (gsm.data, loaded.data):
        assert frame.loc["0001234", "GSM1"] == PRECISION
        pd.testing.assert_frame_equal(frame, gsm.data, check_exact=True)
    assert series.to_dataframe("data").loc["0001234", "GSM1"] == PRECISION
    assert "age" in gsm.characteristics and gsm.characteristics["age"] == "45"
    text = path.read_text(encoding="utf-8")
    assert "0001234" in text and repr(PRECISION) in text


def test_data_status_and_hydration_do_not_make_requests(study, client, tmp_path):
    cache = LocalCache(tmp_path)
    obj = GSM("GSM1", _http_client=client, _cache=cache)
    before = len(study.hits)
    assert obj.data_status.name == "DATA_ON_PAGE"
    again = GSM("GSM1", _http_client=client, _cache=cache)
    pd.testing.assert_frame_equal(obj.data, again.data, check_exact=True)
    assert len(study.hits) == before


def test_systemic_errors_propagate_instead_of_becoming_partial_success(study, client, tmp_path):
    study.add("/data?acc=GSM10", "blocked", "text/plain", 429)
    with pytest.raises(PyNCBIError) as error:
        GSE("GSE1", mode="per_gsm", n_threads=2, _http_client=client, _cache=LocalCache(tmp_path))
    from PyNCBI import error_code_for

    assert error_code_for(error.value) == "rate_limited"


def test_missing_sample_data_is_reported_and_partial_data_is_not_cached(study, client, tmp_path):
    cache = LocalCache(tmp_path)
    complete = GSE("GSE1", mode="per_gsm", _http_client=client, _cache=cache)
    assert complete.is_cached()
    study.add("/query?acc=GSM10", "^SAMPLE = GSM10\n!Sample_title = unavailable\n")
    # The batch still advertises a table; an empty result is a record-local absence.
    study.add("/data?acc=GSM10", "!sample_table_begin\nID_REF\tVALUE\n!sample_table_end\n")
    obj = GSE(
        "GSE1", mode="per_gsm", n_threads=2, overwrite_cache=True, _http_client=client, _cache=cache
    )
    assert obj.failed_samples == ["GSM10"]
    assert obj.to_dict()["failures"][0]["error_code"] == "no_data"
    assert list(obj.to_dataframe("data").columns) == ["GSM1"]
    assert not obj.is_cached()


def test_raw_file_manifest_is_not_a_measurement_matrix(study, client, tmp_path):
    sample = f"^SAMPLE = GSM1\n!Sample_title = raw\n!Sample_supplementary_file = {study.base_url}/raw/GSM1.idat.gz\n"
    study.add("/query?acc=GSM1", sample)
    study.add("/batch?acc=GSE1", sample)
    study.add("/query?acc=GSM10", "^SAMPLE = GSM10\n")
    obj = GSE("GSE1", mode="per_gsm", _http_client=client, _cache=LocalCache(tmp_path))
    assert list(obj["GSM1"].data.columns) == ["file", "url"]
    assert obj.to_dataframe("data").empty


def test_old_accession_only_object_cache_is_invalidated(study, client, tmp_path):
    cache = LocalCache(tmp_path)
    cache.set(
        "GSE1", {"gse_id": "GSE1", "shell_only": False, "gsm_data": {"GSM1": pd.Series([999])}}
    )
    obj = GSE("GSE1", mode="matrix", _http_client=client, _cache=cache)
    assert obj.to_dataframe("data").iloc[0, 0] == PRECISION


def test_empty_injected_cache_is_used_and_concurrent_writes_are_atomic(study, client, tmp_path):
    cache = LocalCache(tmp_path)
    GSE("GSE1", shell_only=True, _http_client=client, _cache=cache)
    assert "GSE1" in cache.list_keys()
    caches = [LocalCache(tmp_path) for _ in range(8)]

    def roundtrip(n):
        shared = caches[n % len(caches)]
        shared.set("shared", {"value": n})
        assert shared.get("shared")["value"] in range(40)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(roundtrip, range(40)))
    assert cache.get("shared")["value"] in range(40)
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("kind", [GSE, GSM])
def test_loaded_objects_cannot_be_relabelled_as_another_accession(study, client, tmp_path, kind):
    acc = "GSE1" if kind is GSE else "GSM1"
    obj = kind(acc, shell_only=True, _http_client=client, _cache=LocalCache(tmp_path))
    with pytest.raises(ValueError):
        if kind is GSE:
            obj.gse_id = "GSE2"
        else:
            obj.gsm_id = "GSM2"


def test_clear_cache_tracks_variants_with_a_minimal_cache_backend(study, client):
    class MemoryCache:
        def __init__(self):
            self.entries = {}

        def get(self, key):
            return self.entries.get(key)

        def set(self, key, value):
            self.entries[key] = value

        def exists(self, key):
            return key in self.entries

        def delete(self, key):
            return self.entries.pop(key, None) is not None

        def __bool__(self):
            return bool(self.entries)

    cache = MemoryCache()
    GSE("GSE1", mode="matrix", _http_client=client, _cache=cache)
    obj = GSE("GSE1", mode="supp", file_index=1, _http_client=client, _cache=cache)
    assert len(cache.entries) == 3  # one metadata snapshot, two data requests
    assert obj.clear_cache()
    assert cache.entries == {}


def test_auto_can_fall_back_from_unsupported_rnaseq_organism(study, client, tmp_path):
    study.add(
        "/query?acc=GSE1",
        """^SERIES = GSE1
!Series_title = Non-model organism
!Series_type = Expression profiling by high throughput sequencing
!Series_sample_organism = An unknown species
!Series_platform_id = GPL1
!Series_sample_id = GSM1
!Series_sample_id = GSM10
""",
    )
    expected = load_expression("GSE1", http_client=client)
    obj = GSE("GSE1", _http_client=client, _cache=LocalCache(tmp_path))
    assert expected.source == obj.to_dict()["source"] == "series_matrix"
    pd.testing.assert_frame_equal(obj.to_dataframe("data"), expected.matrix_df, check_exact=True)


def test_gsm_integer_accessor_and_cache_keep_large_counts_exact(study, client, tmp_path):
    study.add(
        "/data?acc=GSM1",
        "!sample_table_begin\nID_REF\tVALUE\n0001234\t9007199254740993\n0005678\t2\n!sample_table_end\n",
    )
    cache = LocalCache(tmp_path)
    for _ in range(2):
        obj = GSM("GSM1", _http_client=client, _cache=cache)
        assert obj["0001234"] == 9007199254740993
        assert isinstance(obj["0001234"], int)
