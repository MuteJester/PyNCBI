"""GEOReader facade and logging helpers (offline)."""

from __future__ import annotations

import logging

import pytest

from native_support import identifiers
from PyNCBI.core.config import Config
from PyNCBI.core.logging import (
    LogLevel,
    configure_logging,
    get_logger,
    log_level,
    set_level,
    silence,
    verbose,
)
from PyNCBI.geo import GEOReader

GSE = """^SERIES = GSE100
!Series_title = Reader study
!Series_type = Methylation profiling by array
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL13534
!Series_sample_id = GSM1001
!Series_sample_id = GSM1002
"""
BATCH = """^SAMPLE = GSM1001
!Sample_title = Control_1
!Sample_series_id = GSE100
!Sample_characteristics_ch1 = disease state: Control
^SAMPLE = GSM1002
!Sample_title = Case_1
!Sample_series_id = GSE100
!Sample_characteristics_ch1 = disease state: Fibromyalgia
"""
GSM_TABLE = "^SAMPLE = GSM1001\n!Sample_title = Control_1\n!Sample_series_id = GSE100\n!Sample_data_row_count = 10\n"
GSM_SUPP = "^SAMPLE = GSM1002\n!Sample_title = Case_1\n!Sample_series_id = GSE100\n!Sample_supplementary_file = https://x/GSM1002_Grn.idat.gz\n!Sample_data_row_count = 0\n"
GSM_NONE = "^SAMPLE = GSM1003\n!Sample_title = Empty\n!Sample_series_id = GSE100\n!Sample_supplementary_file = NONE\n!Sample_data_row_count = 0\n"


@pytest.fixture
def fake_geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    local_http.add("/query?acc=GSE100", GSE)
    local_http.add("/batch?acc=GSE100", BATCH)
    local_http.add("/query?acc=GSM1001", GSM_TABLE)
    local_http.add("/query?acc=GSM1002", GSM_SUPP)
    local_http.add("/query?acc=GSM1003", GSM_NONE)
    return local_http


def test_georeader_facade_methods(fake_geo, tmp_path):
    reader = GEOReader()
    assert reader.list_gse_samples("GSE100") == ["GSM1001", "GSM1002"]
    info = reader.get_gse_info("GSE100")
    assert info["title"] == "Reader study"
    df = reader.extract_gse_sample_info(
        "GSE100", where="disease state: Fibromyalgia", save_to=tmp_path / "s.csv"
    )
    assert list(identifiers(df)) == ["GSM1002"] and (tmp_path / "s.csv").exists()
    gsm = reader.extract_gsm_info("GSM1001")
    assert isinstance(gsm, dict) and gsm["^SAMPLE"] == "GSM1001" and gsm["title"] == "Control_1"
    assert reader.get_gsm_data_status("GSM1001") == 0
    assert reader.get_gsm_data_status("GSM1002") == 1
    assert reader.get_gsm_data_status("GSM1003") == -1
    assert reader.gsms_from_gse_soft(GSE) == ["GSM1001", "GSM1002"]
    assert reader.parse_gsm_soft(GSM_TABLE)["title"] == "Control_1"
    report = reader.inspect("GSE100", check_file_sizes=False)
    assert report.record_type == "GSE" and report.sample_count == 2


def test_logging_helpers_toggle_levels(capsys):
    logger = get_logger("PyNCBI.test")
    assert logger.name.startswith("PyNCBI")
    configure_logging(level=LogLevel.DEBUG)
    assert logging.getLogger("PyNCBI").level <= logging.DEBUG
    verbose()
    assert logging.getLogger("PyNCBI").level <= logging.INFO
    silence()
    assert logging.getLogger("PyNCBI").level >= logging.CRITICAL
    with log_level(LogLevel.WARNING):
        assert logging.getLogger("PyNCBI").level == logging.WARNING
    set_level(LogLevel.ERROR)
    assert logging.getLogger("PyNCBI").level == logging.ERROR
    silence()
