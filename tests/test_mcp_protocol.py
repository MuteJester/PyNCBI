"""MCP protocol surface: capability negotiation, tool annotations, structured content,
resources (concrete + templates), prompts, notification handling, and the HTTP transport."""

from __future__ import annotations

import json
import threading

import pytest
import requests

from PyNCBI.core.config import Config
from PyNCBI.mcp.server import (
    MCP_PROMPTS,
    MCP_RESOURCE_TEMPLATES,
    MCP_RESOURCES,
    MCP_TOOLS,
    SUPPORTED_PROTOCOL_VERSIONS,
    create_http_server,
    handle_jsonrpc_message,
    read_resource,
)

GSE = """^SERIES = GSE100
!Series_title = Protocol study
!Series_type = Methylation profiling by array
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL1
!Series_sample_id = GSM1
!Series_sample_id = GSM2
"""
BATCH = """^SAMPLE = GSM1
!Sample_title = Control_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL1
!Sample_characteristics_ch1 = gender: F
!Sample_characteristics_ch1 = disease state: Control
^SAMPLE = GSM2
!Sample_title = Case_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL1
!Sample_characteristics_ch1 = gender: M
!Sample_characteristics_ch1 = disease state: Lupus
"""


def _rpc(method: str, params: dict | None = None, msg_id: int | None = 1) -> dict | None:
    msg = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        msg["id"] = msg_id
    if params is not None:
        msg["params"] = params
    return handle_jsonrpc_message(msg)


@pytest.fixture
def geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_DATA_URL_TEMPLATE", base + "/data?acc={accession}")
    local_http.add("/query?acc=GSE100", GSE)
    local_http.add("/batch?acc=GSE100", BATCH)
    local_http.add(
        "/data?acc=GPL1",
        "^PLATFORM = GPL1\n!platform_table_begin\nID\tGene Symbol\np1\tTP53\np2\tBRCA1\n!platform_table_end\n",
    )
    return local_http


# --- capabilities & negotiation -------------------------------------------------------------


def test_initialize_advertises_tools_resources_and_prompts():
    resp = _rpc(
        "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t", "version": "1"}}
    )
    assert resp is not None
    result = resp["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "PyNCBI"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["capabilities"]["resources"] == {"subscribe": False, "listChanged": False}
    assert result["capabilities"]["prompts"] == {"listChanged": False}
    assert result["instructions"]


def test_initialize_negotiates_down_to_a_supported_version():
    resp = _rpc("initialize", {"protocolVersion": "1999-01-01"})
    assert resp is not None
    assert resp["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]
    assert SUPPORTED_PROTOCOL_VERSIONS[0] >= "2025-06-18"


def test_notifications_are_accepted_without_a_response():
    assert _rpc("notifications/initialized", {}, msg_id=None) is None
    assert _rpc("notifications/cancelled", {"requestId": 7, "reason": "user"}, msg_id=None) is None
    assert _rpc("ping", {}, msg_id=42)["result"] == {}


def test_unknown_method_returns_method_not_found():
    resp = _rpc("does/not/exist")
    assert resp is not None and resp["error"]["code"] == -32601


# --- tools: annotations & structured content -------------------------------------------------


def test_every_tool_declares_behaviour_annotations():
    by_name = {t["name"]: t for t in MCP_TOOLS}
    for tool in MCP_TOOLS:
        ann = tool.get("annotations")
        assert ann, f"{tool['name']} has no annotations"
        assert isinstance(ann.get("title"), str) and ann["title"]
        assert isinstance(ann.get("readOnlyHint"), bool)
        assert ann.get("openWorldHint") is True  # every tool talks to NCBI
        if not ann["readOnlyHint"]:
            assert ann.get("destructiveHint") is False  # PyNCBI only ever creates files

    for name in (
        "ncbi_search",
        "ncbi_screen",
        "ncbi_inspect",
        "ncbi_metadata",
        "ncbi_preview",
        "ncbi_status",
    ):
        assert by_name[name]["annotations"]["readOnlyHint"] is True, name
    for name in ("ncbi_download", "ncbi_bundle", "ncbi_fastq", "ncbi_genome"):
        assert by_name[name]["annotations"]["readOnlyHint"] is False, name


def test_tool_call_returns_structured_content_alongside_text(geo):
    resp = _rpc(
        "tools/call",
        {
            "name": "ncbi_samples",
            "arguments": {"gse_id": "GSE100", "harmonize": True, "as_json": True},
        },
    )
    assert resp is not None
    result = resp["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    structured = result["structuredContent"]
    assert structured["ok"] is True and structured["data"]["sample_count"] == 2
    assert json.loads(result["content"][0]["text"]) == structured


def test_tool_error_is_structured_too():
    resp = _rpc("tools/call", {"name": "ncbi_metadata", "arguments": {}})
    assert resp is not None
    assert resp["result"]["isError"] is True
    assert resp["result"]["structuredContent"]["error_code"] in (
        "invalid_argument",
        "internal_error",
    )


# --- resources --------------------------------------------------------------------------------


def test_resources_list_exposes_concrete_contracts():
    resp = _rpc("resources/list")
    assert resp is not None
    uris = {r["uri"] for r in resp["result"]["resources"]}
    assert {"pyncbi://schema/study", "pyncbi://schema/sample", "pyncbi://vocabulary"} <= uris
    for res in resp["result"]["resources"]:
        assert res["name"] and res["mimeType"] and res["description"]
    assert MCP_RESOURCES


def test_resource_templates_cover_studies_and_platforms():
    resp = _rpc("resources/templates/list")
    assert resp is not None
    templates = {t["uriTemplate"] for t in resp["result"]["resourceTemplates"]}
    assert "pyncbi://study/{gse_id}/overview" in templates
    assert "pyncbi://study/{gse_id}/samples" in templates
    assert "pyncbi://study/{gse_id}/record" in templates
    assert "pyncbi://platform/{gpl_id}/table" in templates
    assert MCP_RESOURCE_TEMPLATES


def test_read_schema_and_vocabulary_resources():
    schema = read_resource("pyncbi://schema/study")
    assert schema["mimeType"] == "application/json"
    assert json.loads(schema["text"])["title"] == "PyNCBI StudyRecord"
    vocab = read_resource("pyncbi://vocabulary")
    assert "sex" in json.loads(vocab["text"])["fields"]


def test_read_study_resources_via_jsonrpc(geo):
    resp = _rpc("resources/read", {"uri": "pyncbi://study/GSE100/overview"})
    assert resp is not None
    contents = resp["result"]["contents"][0]
    assert contents["uri"] == "pyncbi://study/GSE100/overview"
    assert contents["mimeType"] == "text/markdown"
    assert "GSE100" in contents["text"] and "Protocol study" in contents["text"]

    samples = read_resource("pyncbi://study/GSE100/samples")
    assert samples["mimeType"] == "text/csv" and "GSM1" in samples["text"]

    record = read_resource("pyncbi://study/GSE100/record")
    assert record["mimeType"] == "application/json"
    assert json.loads(record["text"])["record_type"] == "study"

    table = read_resource("pyncbi://platform/GPL1/table")
    assert "TP53" in table["text"]


def test_unknown_resource_uri_is_an_error():
    with pytest.raises(ValueError, match="(?i)uri"):
        read_resource("pyncbi://nope/xyz")
    resp = _rpc("resources/read", {"uri": "pyncbi://nope/xyz"})
    assert resp is not None and "error" in resp


# --- prompts ----------------------------------------------------------------------------------


def test_prompts_list_and_get_render_arguments():
    resp = _rpc("prompts/list")
    assert resp is not None
    names = {p["name"] for p in resp["result"]["prompts"]}
    assert {"find_dataset", "study_deep_dive", "cohort_download", "paper_methods"} <= names
    for prompt in resp["result"]["prompts"]:
        assert prompt["description"]
        for arg in prompt.get("arguments", []):
            assert arg["name"] and isinstance(arg.get("required"), bool)
    assert MCP_PROMPTS

    got = _rpc("prompts/get", {"name": "study_deep_dive", "arguments": {"gse_id": "GSE152418"}})
    assert got is not None
    messages = got["result"]["messages"]
    assert messages[0]["role"] == "user"
    text = messages[0]["content"]["text"]
    assert "GSE152418" in text and "ncbi_metadata" in text

    missing = _rpc("prompts/get", {"name": "study_deep_dive", "arguments": {}})
    assert missing is not None and "error" in missing
    unknown = _rpc("prompts/get", {"name": "nope", "arguments": {}})
    assert unknown is not None and "error" in unknown


# --- HTTP transport ---------------------------------------------------------------------------


@pytest.fixture
def http_server():
    server = create_http_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()


def test_http_transport_serves_jsonrpc_with_a_session_id(http_server):
    init = requests.post(
        f"{http_server}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=10,
    )
    assert init.status_code == 200
    assert init.headers["Content-Type"].startswith("application/json")
    session = init.headers["Mcp-Session-Id"]
    assert session
    assert init.json()["result"]["serverInfo"]["name"] == "PyNCBI"

    listed = requests.post(
        f"{http_server}/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={"Accept": "application/json", "Mcp-Session-Id": session},
        timeout=10,
    )
    assert listed.status_code == 200
    assert len(listed.json()["result"]["tools"]) == len(MCP_TOOLS)


def _http_session(http_server):
    init = requests.post(
        f"{http_server}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        timeout=10,
    )
    return {"Accept": "application/json", "Mcp-Session-Id": init.headers["Mcp-Session-Id"]}


def test_http_transport_accepts_notifications_and_rejects_batches(http_server):
    headers = _http_session(http_server)
    note = requests.post(
        f"{http_server}/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=headers,
        timeout=10,
    )
    assert note.status_code == 202 and note.content == b""
    batch = requests.post(
        f"{http_server}/mcp",
        json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
        headers=headers,
        timeout=10,
    )
    assert batch.status_code == 400 and batch.json()["error"]["code"] == -32600


def test_http_transport_rejects_foreign_origins_and_bad_requests(http_server):
    headers = _http_session(http_server)
    bad_origin = requests.post(
        f"{http_server}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Origin": "https://evil.example", "Accept": "application/json"},
        timeout=10,
    )
    assert bad_origin.status_code == 403

    ok_origin = requests.post(
        f"{http_server}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={**headers, "Origin": "http://localhost:3000"},
        timeout=10,
    )
    assert ok_origin.status_code == 200

    malformed = requests.post(
        f"{http_server}/mcp",
        data=b"not json",
        headers={**headers, "Content-Type": "application/json"},
        timeout=10,
    )
    assert malformed.status_code == 400 and malformed.json()["error"]["code"] == -32700

    assert requests.get(f"{http_server}/mcp", headers=headers, timeout=10).status_code == 405
    assert (
        requests.post(
            f"{http_server}/other", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, timeout=10
        ).status_code
        == 404
    )
    assert requests.delete(f"{http_server}/mcp", headers=headers, timeout=10).status_code == 204


def test_cli_mcp_http_flags_exist():
    from PyNCBI.cli.main import build_parser

    action = next(
        a
        for a in build_parser()._subparsers._group_actions[0].choices["mcp"]._actions
        if a.dest == "http"
    )
    assert action.option_strings == ["--http"]


def test_documented_module_launch_imports_the_server_exactly_once():
    """`python -m PyNCBI.mcp.server` is documented, so it must not double-import the module.

    When the package `__init__` imports `server` eagerly, runpy executes it a second time as
    `__main__`, leaving two copies of the tool catalogue and session state in memory and
    printing a RuntimeWarning on the agent's stderr. `-W error::RuntimeWarning` turns that
    into a non-zero exit so the test cannot pass by accident.
    """
    import subprocess
    import sys

    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    result = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "PyNCBI.mcp.server"],
        input=request + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "RuntimeWarning" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
    assert '"protocolVersion"' in result.stdout, result.stdout

    cli = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "PyNCBI.cli", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "RuntimeWarning" not in cli.stderr, cli.stderr
    assert cli.returncode == 0, cli.stderr
    assert "pyncbi" in cli.stdout
