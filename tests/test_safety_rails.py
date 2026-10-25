"""Safety rails for autonomous use: download planning (dry run), size and disk-space guards,
and an append-only request audit log."""

from __future__ import annotations

import gzip
import json
import shutil

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config, get_config, reset_config
from PyNCBI.core.exceptions import DataError
from PyNCBI.domains.geo import download_geo_files, plan_geo_download
from PyNCBI.io.http_client import NCBIClient
from PyNCBI.mcp.server import execute_mcp_tool

GSE = """^SERIES = GSE100
!Series_title = Rails study
!Series_type = Methylation profiling by array
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL1
!Series_sample_id = GSM1
!Series_supplementary_file = {base}/geo/series/GSE100/suppl/GSE100_RAW.tar
!Series_supplementary_file = {base}/geo/series/GSE100/suppl/GSE100_counts.txt.gz
"""


@pytest.fixture
def geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    local_http.add("/query?acc=GSE100", GSE.format(base=base))
    local_http.add(
        "/batch?acc=GSE100", "^SAMPLE = GSM1\n!Sample_title = s\n!Sample_series_id = GSE100\n"
    )
    local_http.add("/geo/series/GSE100/suppl/GSE100_RAW.tar", b"t" * 8192, "application/x-tar")
    local_http.add(
        "/geo/series/GSE100/suppl/GSE100_counts.txt.gz", b"c" * 1024, "application/octet-stream"
    )
    return local_http


def test_plan_geo_download_reports_what_would_be_fetched(geo, tmp_path):
    plan = plan_geo_download("GSE100", tmp_path / "out", source="supp")
    assert [f["name"] for f in plan.files] == ["GSE100_RAW.tar", "GSE100_counts.txt.gz"]
    assert plan.total_bytes == 8192 + 1024
    assert plan.total_human.endswith("KB")
    assert plan.file_count == 2
    assert plan.destination == tmp_path / "out"
    assert plan.free_bytes is not None and plan.fits_on_disk is True
    assert not (tmp_path / "out").exists()  # nothing was written
    md = plan.to_markdown()
    assert "GSE100_RAW.tar" in md and "would download" in md.lower()
    assert plan.to_dict()["total_bytes"] == 9216

    one = plan_geo_download("GSE100", tmp_path / "out", source="supp", pattern="*.txt.gz")
    assert one.file_count == 1 and one.total_bytes == 1024


def test_download_refuses_to_exceed_max_download_mb(geo, tmp_path):
    with pytest.raises(DataError, match="max_download_mb") as excinfo:
        download_geo_files("GSE100", tmp_path / "a", source="supp", max_download_mb=0.001)
    assert "9.0 KB" in str(excinfo.value) or "9216" in str(excinfo.value)
    assert not any(tmp_path.glob("a/*"))
    paths = download_geo_files("GSE100", tmp_path / "b", source="supp", max_download_mb=1)
    assert len(paths) == 2


def test_download_refuses_when_disk_space_is_short(geo, tmp_path, monkeypatch):
    real = shutil.disk_usage

    def tiny(path):  # noqa: ANN001
        usage = real(path)
        return type(usage)(usage.total, usage.total - 1024, 1024)

    monkeypatch.setattr("PyNCBI.domains.geo.transfer.shutil.disk_usage", tiny)
    with pytest.raises(DataError, match="(?i)disk space"):
        download_geo_files("GSE100", tmp_path / "c", source="supp")
    assert download_geo_files("GSE100", tmp_path / "d", source="supp", check_disk_space=False)


def test_env_default_for_max_download(geo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYNCBI_MAX_DOWNLOAD_MB", "0.001")
    reset_config()
    assert get_config().max_download_mb == 0.001
    with pytest.raises(DataError, match="max_download_mb"):
        download_geo_files("GSE100", tmp_path / "e", source="supp")
    reset_config()


def test_audit_log_records_every_request_as_jsonl(geo, tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("PYNCBI_AUDIT_LOG", str(audit))
    reset_config()
    client = NCBIClient()
    client.get_text(get_config().get_ncbi_query_url("GSE100"))
    client.inspect_url(geo.url("/geo/series/GSE100/suppl/GSE100_counts.txt.gz"))
    lines = [json.loads(ln) for ln in audit.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    first = lines[0]
    assert first["method"] == "GET" and first["status_code"] == 200 and first["bytes_received"] > 0
    assert first["realm"] == "external" and first["timestamp"].endswith("Z")
    assert "acc=GSE100" in first["url"] and "elapsed_ms" in first
    assert lines[1]["method"] == "HEAD"
    reset_config()


def test_audit_log_redacts_api_keys(tmp_path, monkeypatch):
    audit = tmp_path / "a.jsonl"
    monkeypatch.setenv("PYNCBI_AUDIT_LOG", str(audit))
    monkeypatch.setenv("NCBI_API_KEY", "SUPER_SECRET_KEY")
    reset_config()
    client = NCBIClient()
    from PyNCBI.io.http_client import RequestEvent

    client.metrics.record(
        RequestEvent(
            method="GET",
            realm="eutils",
            url="https://x/f.cgi?api_key=SUPER_SECRET_KEY",
            status_code=200,
            elapsed_ms=1.0,
            rate_wait_ms=0.0,
            bytes_received=5,
        )
    )
    text = audit.read_text()
    assert "SUPER_SECRET_KEY" not in text and "REDACTED" in text
    reset_config()


def test_cli_dry_run_and_mcp_dry_run(geo, tmp_path, capsys):
    rc = cli_main(
        ["download", "GSE100", "--source", "supp", "-o", str(tmp_path / "x"), "--dry-run", "--json"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["total_bytes"] == 9216 and payload["data"]["file_count"] == 2
    assert payload["saved_files"] == []
    assert not (tmp_path / "x").exists()

    out = json.loads(
        execute_mcp_tool(
            "ncbi_download",
            {
                "accession": "GSE100",
                "source": "supp",
                "output_path": str(tmp_path / "y"),
                "dry_run": True,
            },
        )
    )
    assert out["data"]["file_count"] == 2 and out["saved_files"] == []
    assert not (tmp_path / "y").exists()


GSE_RNASEQ_SOFT = """^SERIES = GSE400
!Series_title = Counts series
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL24676
"""


@pytest.fixture
def rnaseq_geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    local_http.add("/query?acc=GSE400", GSE_RNASEQ_SOFT)
    local_http.add_gzip(
        "/geo/download/?type=rnaseq_counts&acc=GSE400&format=file&file=GSE400_raw_counts_GRCh38.p13_NCBI.tsv.gz",
        "GeneID\tGSM1\tGSM2\n672\t10\t20\n7157\t30\t40\n",
    )
    return local_http


def test_compressed_output_path_is_a_file_not_a_folder(rnaseq_geo, tmp_path):
    """`-o counts.tsv.gz` names the file to write, exactly as the docs show.

    `.gz` is not one of the table extensions, so the destination used to be treated as a
    folder: the download landed inside a *directory* called `counts.tsv.gz`, and anything
    that then opened that path got IsADirectoryError.
    """
    target = tmp_path / "counts.tsv.gz"
    paths = download_geo_files("GSE400", target, source="rnaseq")

    assert target.is_file(), f"{target} should be the downloaded file, got dir={target.is_dir()}"
    assert paths == [target]
    with gzip.open(target, "rt") as handle:
        assert handle.readline().startswith("GeneID")


def test_folder_output_path_still_receives_the_ncbi_named_file(rnaseq_geo, tmp_path):
    """A destination without a file extension keeps the previous folder behaviour."""
    folder = tmp_path / "counts_dir"
    paths = download_geo_files("GSE400", folder, source="rnaseq")

    assert folder.is_dir()
    assert len(paths) == 1
    assert paths[0].parent == folder
    assert paths[0].name == "GSE400_raw_counts_GRCh38.p13_NCBI.tsv.gz"


def test_named_destination_is_honoured_for_matrix_and_single_supplementary(
    local_http, tmp_path, monkeypatch
):
    """Every single-file source writes to the exact name the caller gave."""
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    local_http.add(
        "/query?acc=GSE401",
        "^SERIES = GSE401\n!Series_title = One file\n"
        f"!Series_supplementary_file = {base}/suppl/GSE401_signal.csv.gz\n",
    )
    local_http.add_gzip("/suppl/GSE401_signal.csv.gz", "probe,GSM1\ncg01,0.5\n")

    target = tmp_path / "my_signal.csv.gz"
    paths = download_geo_files("GSE401", target, source="supp")
    assert target.is_file()
    assert paths == [target]
    with gzip.open(target, "rt") as handle:
        assert handle.readline().startswith("probe")
