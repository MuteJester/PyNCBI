"""Cohort-filtered (`where=`) and multi-accession parallel downloads, served by a local
HTTP server standing in for GEO (Config URL templates are redirected to it)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import NoDataAvailableError
from PyNCBI.domains.geo import download_geo_files, download_many, resolve_cohort_files


def _batch_soft(base: str) -> str:
    return f"""^SAMPLE = GSM1001
!Sample_title = Control_1
!Sample_series_id = GSE100
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = disease state: Control
!Sample_supplementary_file = {base}/geo/samples/GSM1001/suppl/GSM1001_Grn.idat.gz
!Sample_supplementary_file = {base}/geo/samples/GSM1001/suppl/GSM1001_Red.idat.gz
!Sample_supplementary_file = {base}/geo/samples/GSM1001/suppl/GSM1001_peaks.bed.gz
^SAMPLE = GSM1002
!Sample_title = Case_1
!Sample_series_id = GSE100
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = disease state: Fibromyalgia
!Sample_supplementary_file = {base}/geo/samples/GSM1002/suppl/GSM1002_Grn.idat.gz
!Sample_supplementary_file = {base}/geo/samples/GSM1002/suppl/GSM1002_Red.idat.gz
^SAMPLE = GSM1003
!Sample_title = Case_2
!Sample_series_id = GSE100
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = disease state: Fibromyalgia
!Sample_supplementary_file = {base}/geo/samples/GSM1003/suppl/GSM1003_Grn.idat.gz
!Sample_supplementary_file_1 = NONE
"""


def _gsm_soft(base: str, gsm: str) -> str:
    return f"""^SAMPLE = {gsm}
!Sample_title = {gsm} title
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_supplementary_file = {base}/geo/samples/{gsm}/suppl/{gsm}_Grn.idat.gz
!Sample_data_row_count = 0
"""


@pytest.fixture
def fake_geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    local_http.add("/batch?acc=GSE100", _batch_soft(base))
    for gsm in ("GSM1001", "GSM1002", "GSM1003"):
        local_http.add(f"/query?acc={gsm}", _gsm_soft(base, gsm))
        for suffix in ("Grn.idat.gz", "Red.idat.gz", "peaks.bed.gz"):
            local_http.add(f"/geo/samples/{gsm}/suppl/{gsm}_{suffix}", f"{gsm} {suffix} bytes")
    return local_http


def test_resolve_cohort_files_filters_samples_and_patterns_in_one_request(fake_geo):
    resolved = resolve_cohort_files(
        "GSE100", where="disease state: Fibromyalgia", pattern="*_Grn.idat.gz"
    )

    assert [r.gsm_id for r in resolved] == ["GSM1002", "GSM1003"]
    assert all(r.url.endswith("_Grn.idat.gz") for r in resolved)
    # Exactly one GEO request (the batch sample view) was needed to resolve everything
    assert sum(1 for h in fake_geo.hits if h.startswith("/batch")) == 1
    assert not any(h.startswith("/query") for h in fake_geo.hits)


def test_download_geo_files_with_where_downloads_only_matching_samples_in_parallel(
    fake_geo, tmp_path
):
    out = tmp_path / "cohort"
    paths = download_geo_files(
        "GSE100", out, where="disease state == 'Fibromyalgia'", pattern="*.idat.gz", max_workers=3
    )

    names = sorted(p.name for p in paths)
    assert names == ["GSM1002_Grn.idat.gz", "GSM1002_Red.idat.gz", "GSM1003_Grn.idat.gz"]
    assert (out / "GSM1002_Red.idat.gz").read_text() == "GSM1002 Red.idat.gz bytes"
    assert not (out / "GSM1001_Grn.idat.gz").exists()


def test_download_geo_files_with_where_and_no_matching_samples_raises_clear_error(
    fake_geo, tmp_path
):
    with pytest.raises(NoDataAvailableError, match="(?i)no samples matched"):
        download_geo_files("GSE100", tmp_path, where="disease state: Lupus")


def test_download_many_runs_accessions_in_parallel_and_returns_per_accession_paths(
    fake_geo, tmp_path
):
    results = download_many(["GSM1001", "GSM1002"], tmp_path / "many", max_workers=2)

    assert set(results) == {"GSM1001", "GSM1002"}
    assert [p.name for p in results["GSM1001"]] == ["GSM1001_Grn.idat.gz"]
    assert (tmp_path / "many" / "GSM1002" / "GSM1002_Grn.idat.gz").exists()


def test_cli_download_accepts_multiple_accessions_and_where(fake_geo, tmp_path, capsys):
    rc = cli_main(
        ["download", "GSM1001", "GSM1002", "-o", str(tmp_path / "cli"), "--json", "--workers", "2"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert set(payload["data"]["downloaded_files"]) == {"GSM1001", "GSM1002"}
    assert len(payload["saved_files"]) == 2

    rc = cli_main(
        [
            "download",
            "GSE100",
            "--where",
            "disease state: Fibromyalgia",
            "--pattern",
            "*_Grn*",
            "-o",
            str(tmp_path / "cli2"),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert sorted(Path(p).name for p in payload["saved_files"]) == [
        "GSM1002_Grn.idat.gz",
        "GSM1003_Grn.idat.gz",
    ]
