"""Metadata layer surfaces: CLI (`metadata`, `schema`, `samples --harmonize`), MCP
(`ncbi_metadata` with pagination, `ncbi_samples harmonize`), bundle layout, top-level exports."""

from __future__ import annotations

import json

import pytest

import PyNCBI
from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.domains.bundle import build_study_bundle
from PyNCBI.mcp.server import MCP_TOOLS, execute_mcp_tool
from test_metadata_build_store import (  # noqa: I001 - tests dir is on sys.path under pytest
    BATCH_SOFT,
    GSE_SOFT,
)


@pytest.fixture
def fake_study(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/eutils")
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    local_http.add("/query?acc=GSE100", GSE_SOFT.format(base=base))
    local_http.add("/batch?acc=GSE100", BATCH_SOFT.format(base=base))
    local_http.add("/geo/series/GSE100/suppl/GSE100_RAW.tar", b"x" * 2048, "application/x-tar")
    return local_http


def test_top_level_exports():
    for name in (
        "build_study_record",
        "StudyRecord",
        "SampleRecord",
        "harmonize_characteristics",
        "MetadataStore",
        "load_study_record",
        "assess_study",
        "vocabulary",
        "json_schema",
    ):
        assert hasattr(PyNCBI, name), name


def test_cli_metadata_json_markdown_and_save(fake_study, tmp_path, capsys):
    rc = cli_main(["metadata", "GSE100", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    data = payload["data"]
    assert data["record_type"] == "study" and data["schema_version"] == PyNCBI.SCHEMA_VERSION
    assert data["samples"][0]["harmonized"]["sex"]["value"] == "female"
    assert data["quality"]["score"] <= 100 and data["harmonization"]["mapped_fields"] == [
        "age",
        "disease",
        "sex",
        "tissue",
    ]

    rc = cli_main(["metadata", "GSE100", "--markdown"])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("# GSE100") and "Quality score" in out

    rc = cli_main(["metadata", "GSE100", "-o", str(tmp_path / "meta"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert any(p.endswith("manifest.json") for p in payload["saved_files"])
    assert PyNCBI.load_study_record(tmp_path / "meta").gse_id == "GSE100"

    rc = cli_main(
        ["metadata", "GSE100", "--where", "disease state: Control", "--no-samples", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["data"]["sample_count"] == 1 and payload["data"]["samples"] == []


def test_cli_schema_and_vocabulary(capsys):
    rc = cli_main(["schema", "sample"])
    assert rc == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "PyNCBI SampleRecord"
    rc = cli_main(["schema", "vocabulary", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "sex" in payload["data"]["fields"] and payload["data"]["fields"]["sex"]["synonyms"]


def test_cli_samples_harmonize_flag(fake_study, tmp_path, capsys):
    rc = cli_main(["samples", "GSE100", "--harmonize", "--json", "-o", str(tmp_path / "h.csv")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    cols = payload["data"]["columns"]
    assert "sex" in cols and "age_years" in cols and "raw:facilitation score" in cols
    assert payload["data"]["harmonization"]["unmapped_keys"] == ["facilitation score"]
    text = (tmp_path / "h.csv").read_text()
    assert "female" in text


def test_mcp_metadata_tool_paginates_samples_and_bounds_payload(fake_study):
    assert any(t["name"] == "ncbi_metadata" for t in MCP_TOOLS)
    out = json.loads(execute_mcp_tool("ncbi_metadata", {"gse_id": "GSE100", "page_size": 2}))
    data = out["data"]
    assert data["sample_count"] == 3 and len(data["samples"]) == 2 and data["next_page_token"]
    out2 = json.loads(
        execute_mcp_tool(
            "ncbi_metadata",
            {"gse_id": "GSE100", "page_size": 2, "page_token": data["next_page_token"]},
        )
    )
    assert [s["gsm_id"] for s in out2["data"]["samples"]] == ["GSM1003"] and out2["data"][
        "next_page_token"
    ] is None
    # samples tool: harmonize + pagination
    out3 = json.loads(
        execute_mcp_tool(
            "ncbi_samples", {"gse_id": "GSE100", "harmonize": True, "page_size": 2, "as_json": True}
        )
    )
    assert out3["data"]["rows"][0]["sex"] == "female" and out3["data"]["next_page_token"]


def test_bundle_writes_metadata_layout(fake_study, tmp_path):
    bundle = build_study_bundle(
        "GSE100", tmp_path / "b", include_sra=False, include_pubmed=False, check_file_sizes=False
    )
    names = {p.name for p in (tmp_path / "b").iterdir()}
    assert {
        "study.json",
        "samples_harmonized.csv",
        "provenance.csv",
        "quality.json",
        "manifest.json",
        "vocabulary.json",
    } <= names
    assert "record" in bundle.files and bundle.files["record"].name == "study.json"
    rec = PyNCBI.load_study_record(tmp_path / "b")
    assert rec.gse_id == "GSE100" and rec.quality is not None
    overview = (tmp_path / "b" / "STUDY_OVERVIEW.md").read_text()
    assert "Quality score" in overview and "Harmonized" in overview
