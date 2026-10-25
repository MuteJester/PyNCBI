"""SuperSeries awareness in download/bundle, and progress events for streamed tables."""

from __future__ import annotations

import pytest

from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import NoDataAvailableError
from PyNCBI.core.reporter import Verbosity, get_reporter, verbosity_scope
from PyNCBI.domains.bundle import build_study_bundle
from PyNCBI.domains.geo import download_geo_files
from PyNCBI.io.http_client import NCBIClient

SUPER = """^SERIES = GSE900
!Series_title = Super study
!Series_type = Expression profiling by high throughput sequencing
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL24676
!Series_relation = SuperSeries of: GSE901
!Series_relation = SuperSeries of: GSE902
!Series_sample_id = GSM9001
!Series_sample_id = GSM9002
"""


def _sub(gse: str, gsm: str, base: str) -> str:
    return f"""^SERIES = {gse}
!Series_title = Sub {gse}
!Series_type = Expression profiling by high throughput sequencing
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL24676
!Series_relation = SubSeries of: GSE900
!Series_sample_id = {gsm}
!Series_supplementary_file = {base}/geo/series/{gse}/suppl/{gse}_counts.tsv.gz
"""


def _batch(gsm: str) -> str:
    return f"^SAMPLE = {gsm}\n!Sample_title = t\n!Sample_series_id = GSE900\n!Sample_characteristics_ch1 = group: a\n!Sample_supplementary_file_1 = NONE\n"


@pytest.fixture
def fake_super(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    local_http.add("/query?acc=GSE900", SUPER)
    local_http.add("/batch?acc=GSE900", _batch("GSM9001") + _batch("GSM9002"))
    for gse, gsm in (("GSE901", "GSM9001"), ("GSE902", "GSM9002")):
        local_http.add(f"/query?acc={gse}", _sub(gse, gsm, base))
        local_http.add(f"/batch?acc={gse}", _batch(gsm))
        local_http.add(f"/geo/series/{gse}/suppl/{gse}_counts.tsv.gz", f"{gse} bytes")
    return local_http


def test_download_superseries_without_own_files_explains_subseries(fake_super, tmp_path):
    with pytest.raises(NoDataAvailableError) as excinfo:
        download_geo_files("GSE900", tmp_path / "x", source="supp")
    msg = str(excinfo.value)
    assert "SuperSeries" in msg and "GSE901" in msg and "GSE902" in msg
    assert "expand_subseries" in msg or "--subseries" in msg


def test_download_superseries_with_expand_fans_out_to_subseries_folders(fake_super, tmp_path):
    paths = download_geo_files("GSE900", tmp_path / "super", source="supp", expand_subseries=True)
    names = sorted(p.relative_to(tmp_path / "super").as_posix() for p in paths)
    assert names == ["GSE901/GSE901_counts.tsv.gz", "GSE902/GSE902_counts.tsv.gz"]


def test_bundle_superseries_warns_and_can_expand(fake_super, tmp_path):
    bundle = build_study_bundle(
        "GSE900", tmp_path / "b", include_sra=False, include_pubmed=False, check_file_sizes=False
    )
    assert any("SuperSeries" in w and "GSE901" in w for w in bundle.warnings)
    overview = bundle.files["overview"].read_text()
    assert "SubSeries" in overview and "GSE902" in overview

    expanded = build_study_bundle(
        "GSE900",
        tmp_path / "b2",
        include_sra=False,
        include_pubmed=False,
        check_file_sizes=False,
        expand_subseries=True,
    )
    assert set(expanded.subseries_bundles) == {"GSE901", "GSE902"}
    assert (tmp_path / "b2" / "subseries" / "GSE901" / "STUDY_OVERVIEW.md").exists()
    assert "subseries/GSE902/STUDY_OVERVIEW.md" in expanded.files["overview"].read_text()


def test_stream_lines_reports_download_progress_events(local_http):
    text = "\n".join(f"row{i}" for i in range(5000)) + "\n"
    url = local_http.add("/big_table.tsv", text)
    events = []
    reporter = get_reporter()
    unsub = reporter.subscribe(events.append)
    try:
        with verbosity_scope(Verbosity.VERBOSE):
            assert len(list(NCBIClient().stream_lines(url))) == 5000
    finally:
        unsub()
    starts = [e for e in events if e.domain == "DOWNLOAD" and "big_table.tsv" in e.message]
    assert starts, [e.message for e in events]
