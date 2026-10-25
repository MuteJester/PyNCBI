"""Comprehensive offline unit tests for the redesigned PyNCBI engine, parsers,
formatter, cache, CLI, and MCP server.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from native_support import cell, data_shape, fixture_table, identifiers, is_missing, labels
from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.exceptions import (
    AccessionNotFoundError,
    AccessionPrivateError,
    InvalidAccessionError,
    InvalidAPIKeyError,
    RateLimitExceededError,
    redact_url,
)
from PyNCBI.core.formatter import (
    format_table_preview,
    render_output,
    save_data,
    summarize_characteristics,
)
from PyNCBI.core.models import FASTASequence
from PyNCBI.core.reporter import ActivityEvent, Verbosity, get_reporter, verbosity_scope
from PyNCBI.core.types import DataStatus, FetchMode, SmartFileSelector, validate_accession
from PyNCBI.domains.geo.preview import _parse_tabular_preview_lines
from PyNCBI.domains.geo.samples import _apply_sample_filter
from PyNCBI.io.http_client import (
    NCBIClient,
    RequestEvent,
    format_size_human,
    infer_file_type,
    normalize_ncbi_url,
)
from PyNCBI.io.rate_limiter import NCBIRealmLimiter, TokenBucketLimiter, classify_ncbi_realm
from PyNCBI.mcp.server import handle_jsonrpc_message
from PyNCBI.parsing.matrix_parser import SeriesMatrixParser
from PyNCBI.parsing.soft_parser import get_parser

if TYPE_CHECKING:
    from pathlib import Path


def test_normalize_ncbi_url_and_helpers():
    assert (
        normalize_ncbi_url(
            "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE85nnn/GSE85506/suppl/GSE85506_RAW.tar"
        )
        == "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE85nnn/GSE85506/suppl/GSE85506_RAW.tar"
    )
    assert format_size_human(512) == "512 B"
    assert format_size_human(1024 * 1024 * 5) == "5.0 MB"
    assert infer_file_type("GSE85506_series_matrix.txt.gz") == "series_matrix.txt.gz"
    assert infer_file_type("GSE85506_RAW.tar") == "tar"


def test_token_bucket_limiter():
    limiter = TokenBucketLimiter(rate_per_sec=50.0)
    limiter.acquire()
    assert limiter.rate_per_sec == 50.0


def test_smart_file_selector_never_blocks():
    selector = SmartFileSelector()
    files = [
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE1nnn/GSE1/suppl/GSE1_RAW.tar",
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE1nnn/GSE1/suppl/GSE1_counts.tsv.gz",
    ]
    # Should automatically prefer the tabular .tsv.gz file (index 1) over .tar (index 0)
    assert selector.select(files, "GSE1") == 1


def test_batch_gse_soft_parser_handles_ragged_characteristics():
    batch_soft = """^SAMPLE = GSM1001
!Sample_title = Control_1
!Sample_platform_id = GPL13534
!Sample_series_id = GSE100
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = age: 45
!Sample_characteristics_ch1 = sex: Female
!Sample_data_row_count = 485577
^SAMPLE = GSM1002
!Sample_title = Case_1
!Sample_platform_id = GPL13534
!Sample_series_id = GSE100
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = sex: Male
!Sample_characteristics_ch1 = disease state: Fibromyalgia
!Sample_data_row_count = 485577
"""
    parser = get_parser()
    info_df, char_df = parser.parse_gse_samples_batch(batch_soft)

    assert list(identifiers(info_df)) == ["GSM1001", "GSM1002"]
    assert cell(char_df, "GSM1001", "age") == "45"
    assert is_missing(cell(char_df, "GSM1002", "age"))
    assert cell(char_df, "GSM1002", "sex") == "Male"
    assert cell(char_df, "GSM1002", "disease state") == "Fibromyalgia"

    summaries = summarize_characteristics(char_df)
    summary_names = {s.name for s in summaries}
    assert {"tissue", "age", "sex", "disease state"} <= summary_names


def test_gsm_data_table_parser_and_indexing():
    sample_table_text = """#ID_REF = Probe ID
#VALUE = Beta value
!sample_table_begin
ID_REF\tVALUE
cg00000029\t0.512
cg00000108\t0.875
!sample_table_end
"""
    parser = get_parser()
    df = parser.parse_gsm_data_table(sample_table_text, "GSM1001")

    assert df["columns"] == ["feature_id", "GSM1001"]
    assert df["columns"][0] == "feature_id"
    assert "cg00000029" in identifiers(df)
    assert float(cell(df, "cg00000029", "GSM1001")) == pytest.approx(0.512, rel=1e-3)


def test_series_matrix_parser():
    matrix_text = """!Series_title\t"Test RNA-Seq / Array Study"
!Series_geo_accession\t"GSE99999"
!Sample_title\t"Sample_A"\t"Sample_B"
!Sample_geo_accession\t"GSM1"\t"GSM2"
!Sample_characteristics_ch1\t"tissue: Liver"\t"tissue: Brain"
!Sample_characteristics_ch1\t"age: 30"\t"age: 60"
!series_matrix_table_begin
"ID_REF"\t"GSM1"\t"GSM2"
"probe_1"\t1.25\t2.50
"probe_2"\t3.75\t4.00
!series_matrix_table_end
"""
    res = SeriesMatrixParser().parse(matrix_text)
    assert res.gse_id == "GSE99999"
    assert list(identifiers(res.sample_table)) == ["GSM1", "GSM2"]
    assert cell(res.characteristics_table, "GSM1", "tissue") == "Liver"
    assert cell(res.characteristics_table, "GSM2", "age") == "60"
    assert data_shape(res.matrix) == (2, 2)
    assert float(cell(res.matrix, "probe_1", "GSM2")) == pytest.approx(2.50)


def test_formatter_and_save_data(tmp_path: Path):
    df = fixture_table(
        {"GSM1": [0.1, 0.2], "GSM2": [0.3, 0.4]}, index=labels(["cg1", "cg2"], name="probe")
    )

    csv_path = save_data(df, tmp_path / "out.csv")
    tsv_path = save_data(df, tmp_path / "out.tsv")
    json_path = save_data(df, tmp_path / "out.json")
    assert csv_path.exists()
    assert tsv_path.exists()
    assert json_path.exists()

    md = format_table_preview(df, mode="markdown")
    assert "Table Shape:" in md
    assert "cg1" in md

    json_str = render_output(df, mode="json", command="test", target="GSE1", summary="Test summary")
    parsed = json.loads(json_str)
    assert parsed["ok"] is True
    assert parsed["command"] == "test"
    assert parsed["data"]["columns"] == ["feature_id", "GSM1", "GSM2"]
    assert parsed["data"]["rows"] == df["rows"]


def test_mcp_server_jsonrpc_protocol():
    init_resp = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
    )
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "PyNCBI"

    tools_resp = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools_resp is not None
    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
    assert {
        "ncbi_search",
        "ncbi_inspect",
        "ncbi_samples",
        "ncbi_preview",
        "ncbi_download",
        "ncbi_pubmed",
        "ncbi_gene",
        "ncbi_sra",
        "ncbi_fasta",
        "ncbi_link",
    } <= tool_names


def test_sample_cohort_filtering_and_file_preview_parser():
    df = fixture_table(
        {
            "title": ["S1", "S2", "S3"],
            "severity": ["Severe", "Moderate", "Severe"],
            "age": ["62", "34", "71"],
            "tissue": ["PBMC", "PBMC", "Whole blood"],
        },
        index=["GSM1", "GSM2", "GSM3"],
    )

    # 1. Key=value / Key:value filtering
    severe_df = _apply_sample_filter(df, "severity: Severe")
    assert list(identifiers(severe_df)) == ["GSM1", "GSM3"]

    # 2. Numeric pandas query filtering
    older_df = _apply_sample_filter(df, "age > 50")
    assert list(identifiers(older_df)) == ["GSM1", "GSM3"]

    # 3. Substring fallback filtering
    blood_df = _apply_sample_filter(df, "Whole blood")
    assert list(identifiers(blood_df)) == ["GSM3"]

    # 4. Tabular preview parser
    lines = [
        "GeneID\tGSM1\tGSM2\tGSM3",
        "100287102\t28\t36\t6",
        "653635\t221\t266\t196",
    ]
    preview = _parse_tabular_preview_lines(
        lines,
        accession="GSE152418",
        file_name="GSE152418_raw_counts.tsv.gz",
        url="https://example.org/GSE152418_raw_counts.tsv.gz",
        source="ncbi_rnaseq_counts",
        file_type="tsv.gz",
        max_rows=2,
    )
    assert preview.total_columns == 4
    assert preview.index_name == "feature_id"
    assert len(preview.preview_rows) == 2
    assert "feature_id" in preview.to_markdown()
    assert "feature_id" in preview.to_text()

    fasta = FASTASequence(
        accession="NM_007294.4",
        description="BRCA1 mRNA",
        db="nuccore",
        length=12,
        sequence="ATGCTGAGACTT",
        fasta_text=">NM_007294.4 BRCA1 mRNA\nATGCTGAGACTT\n",
    )
    assert fasta.to_dict()["length"] == 12


def test_cli_skill_and_cache_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    # Test `pyncbi skill install --dir <tmp_path>`
    rc = cli_main(["skill", "install", "--dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "SKILL.md").exists()
    capsys.readouterr()  # Clear stdout buffer from skill install

    # Test `pyncbi cache status --json`
    rc_cache = cli_main(["cache", "status", "--json"])
    assert rc_cache == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["ok"] is True
    assert payload["command"] == "cache.status"


def test_validate_accession_and_fetch_modes():
    assert validate_accession("gse85506", "GSE") == "GSE85506"
    assert validate_accession("PRJNA12345") == "PRJNA12345"
    with pytest.raises(InvalidAccessionError):
        validate_accession("bad_id", "GSM")

    assert FetchMode.from_string("matrix") == FetchMode.SERIES_MATRIX
    assert FetchMode.from_string("rnaseq") == FetchMode.RNASEQ_COUNTS
    assert DataStatus.SUPPLEMENTARY_FILES.value == 1


def test_ncbi_client_realms_redaction_and_error_inspection(capsys: pytest.CaptureFixture[str]):
    # 1. Realm classification
    assert (
        classify_ncbi_realm("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi")
        == "eutils"
    )
    assert classify_ncbi_realm("https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE1") == "geo"
    assert (
        classify_ncbi_realm("https://ftp.ncbi.nlm.nih.gov/geo/series/GSE1nnn/GSE1/matrix/") == "ftp"
    )
    assert classify_ncbi_realm("https://api.ncbi.nlm.nih.gov/datasets/v2/") == "ncbi"
    assert classify_ncbi_realm("https://example.org/file.txt") == "external"

    # 2. API key redaction in URLs and exceptions
    secret_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=gds&api_key=MY_SECRET_123&retmode=json"
    redacted = redact_url(secret_url)
    assert "MY_SECRET_123" not in redacted
    assert "api_key=***REDACTED***" in redacted

    # 3. Realm limiter independent buckets
    realm_limiter = NCBIRealmLimiter(eutils_rate=50.0, geo_rate=50.0, ftp_rate=100.0)
    realm, waited = realm_limiter.acquire(secret_url)
    assert realm == "eutils"
    assert waited >= 0.0
    stats = realm_limiter.stats()
    assert stats["eutils"]["total_acquired"] == 1
    assert stats["ftp"]["total_acquired"] == 0

    # 4. Application-level error detection (200-OK soft errors from E-Utilities and GEO)
    client = NCBIClient()
    with pytest.raises(InvalidAPIKeyError):
        client._inspect_application_errors(secret_url, "eutils", 200, '{"error":"API key invalid"}')

    with pytest.raises(RateLimitExceededError):
        client._inspect_application_errors(
            secret_url, "eutils", 200, '{"error":"API rate limit exceeded"}'
        )

    with pytest.raises(AccessionPrivateError):
        client._inspect_application_errors(
            "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE999999",
            "geo",
            200,
            "Accession GSE999999 is currently private and is scheduled to be released on Jan 01, 2030.",
        )

    with pytest.raises(AccessionNotFoundError):
        client._inspect_application_errors(
            "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE000000",
            "geo",
            200,
            "Could not find a public Series or Sample matching GSE000000",
        )

    # 5. Telemetry recording
    client.metrics.record(
        RequestEvent(
            method="GET",
            realm="eutils",
            url=redacted,
            status_code=200,
            elapsed_ms=42.5,
            rate_wait_ms=0.0,
            bytes_received=2048,
        )
    )
    summary = client.metrics.summary()
    assert summary["total_requests"] == 1
    assert summary["by_realm"]["eutils"]["requests"] == 1

    # 6. CLI `pyncbi status --offline --json`
    rc = cli_main(["status", "--offline", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["command"] == "status"
    assert "rate_limiters" in out["data"]
    assert "telemetry" in out["data"]

    # 7. Centralized ActivityReporter, verbosity_scope, and event subscription
    captured_events: list[ActivityEvent] = []
    reporter = get_reporter()
    unsub = reporter.subscribe(lambda ev: captured_events.append(ev))
    try:
        with (
            verbosity_scope(Verbosity.VERBOSE),
            reporter.task("GEO", "test_op", "Testing verbose task", target="GSE1") as task,
        ):
            task.step("Step 1 of 2", current=1, total=2)
            task.complete("Finished test_op")
    finally:
        unsub()

    phases = [e.phase for e in captured_events]
    assert phases == ["start", "step", "done"]
    stderr_out = capsys.readouterr().err
    assert "[GEO]" in stderr_out
    assert "Finished test_op" in stderr_out
