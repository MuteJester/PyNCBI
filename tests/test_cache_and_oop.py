"""Pickle-free local cache, and GSE/GSM facades that reuse it without extra requests."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from PyNCBI.core.config import Config
from PyNCBI.io.cache import (
    CompressedPickleCache,
    LocalCache,
    decode_cache_value,
    encode_cache_value,
)

GSE_SOFT = """^SERIES = GSE100
!Series_title = Whole blood methylation
!Series_type = Methylation profiling by array
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL13534
!Series_sample_id = GSM1001
!Series_sample_id = GSM1002
!Series_supplementary_file = {base}/geo/series/GSE100/suppl/GSE100_RAW.tar
"""
BATCH_SOFT = """^SAMPLE = GSM1001
!Sample_title = Control_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_characteristics_ch1 = disease state: Control
!Sample_data_row_count = 2
^SAMPLE = GSM1002
!Sample_title = Case_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_characteristics_ch1 = disease state: Fibromyalgia
!Sample_data_row_count = 2
"""
GSM_SOFT = """^SAMPLE = GSM1001
!Sample_title = Control_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_characteristics_ch1 = disease state: Control
!Sample_data_row_count = 2
"""
GSM_DATA = """^SAMPLE = GSM1001
!sample_table_begin
ID_REF\tVALUE
cg00000029\t0.51
cg00000108\t0.87
!sample_table_end
"""


def test_cache_value_codec_roundtrips_pandas_and_nested_containers():
    df = pd.DataFrame({"GSM1": [0.1, 0.2]}, index=pd.Index(["cg1", "cg2"], name="probe"))
    series = pd.Series({"title": "x", "age": "45"}, name="GSM1")
    value = {
        "df": df,
        "series": series,
        "list": [1, "a", None],
        "path": Path("/tmp/x"),
        "nested": {"k": df},
    }

    encoded = json.loads(json.dumps(encode_cache_value(value)))
    decoded = decode_cache_value(encoded)

    pd.testing.assert_frame_equal(decoded["df"], df)
    pd.testing.assert_series_equal(decoded["series"], series)
    assert decoded["list"] == [1, "a", None]
    assert decoded["path"] == str(value["path"])
    pd.testing.assert_frame_equal(decoded["nested"]["k"], df)


def test_local_cache_is_json_gzip_and_ignores_legacy_pickles(tmp_path):
    cache: LocalCache[dict] = LocalCache(tmp_path)
    cache.set("GSE1", {"info": pd.Series({"title": "t"}), "n": 3})
    stored = cache._key_to_path("GSE1")
    assert stored.suffix == ".gz" and stored.name.endswith(".json.gz")
    raw = json.loads(gzip.decompress(stored.read_bytes()))
    assert raw["n"] == 3  # plain JSON, no pickle opcodes
    assert cache.get("GSE1")["info"]["title"] == "t"

    legacy = tmp_path / "GSE2.ch"
    legacy.write_bytes(b"\x80\x04\x95 not a real pickle")
    assert cache.get("GSE2") is None
    assert cache.exists("GSE2") is False
    assert "GSE2" not in cache.list_keys()
    removed = cache.clear()
    assert removed == 2 and not legacy.exists()

    # Backward-compatible alias
    assert CompressedPickleCache is LocalCache


@pytest.fixture
def fake_geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_DATA_URL_TEMPLATE", base + "/data?acc={accession}")
    local_http.add("/query?acc=GSE100", GSE_SOFT.format(base=base))
    local_http.add("/batch?acc=GSE100", BATCH_SOFT)
    local_http.add("/query?acc=GSM1001", GSM_SOFT)
    local_http.add("/data?acc=GSM1001", GSM_DATA)
    return local_http


def test_gse_shell_uses_two_requests_then_zero_from_cache(fake_geo):
    from PyNCBI import GSE

    gse = GSE("GSE100", shell_only=True)
    assert gse.sample_count == 2
    assert list(gse.characteristics_df.index) == ["GSM1001", "GSM1002"]
    assert gse["GSM1002"].characteristics["disease state"] == "Fibromyalgia"
    assert len(fake_geo.hits) == 2  # series header + batch sample view

    again = GSE("GSE100", shell_only=True)
    assert len(fake_geo.hits) == 2
    assert again.title == "Whole blood methylation"
    assert again["GSM1001"].gse == "GSE100"
    assert again.is_cached()


def test_gsm_full_fetch_roundtrips_data_table_through_cache(fake_geo):
    from PyNCBI import GSM

    gsm = GSM("GSM1001")
    assert gsm.has_data
    assert gsm["cg00000029"] == pytest.approx(0.51)
    hits = len(fake_geo.hits)

    cached = GSM("GSM1001")
    assert len(fake_geo.hits) == hits
    assert cached["cg00000108"] == pytest.approx(0.87)
    assert cached.characteristics["disease state"] == "Control"
