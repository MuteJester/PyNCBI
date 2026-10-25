"""MCP schema integrity: the advertised contract must match what the handlers actually read,
every tool must declare an output schema, and the manifest must be inspectable."""

from __future__ import annotations

import json

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.mcp.server import (
    ENVELOPE_OUTPUT_SCHEMA,
    MCP_PROMPTS,
    MCP_RESOURCE_TEMPLATES,
    MCP_RESOURCES,
    MCP_TOOLS,
    execute_mcp_tool,
    mcp_manifest,
)
from PyNCBI.mcp.validation import (
    handler_argument_names,
    validate_tool_schemas,
)

JSON_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}


def test_every_tool_schema_is_structurally_valid():
    problems = validate_tool_schemas()
    assert problems == [], "\n".join(problems)


def test_schema_validator_detects_a_broken_tool():
    broken = [
        {
            "name": "ncbi_broken",
            "description": "",
            "inputSchema": {"type": "object", "properties": {"q": {}}, "required": ["missing"]},
        }
    ]
    problems = validate_tool_schemas(broken)
    joined = "\n".join(problems)
    assert "ncbi_broken" in joined
    assert "description" in joined and "required" in joined


def test_tool_properties_are_well_formed():
    for tool in MCP_TOOLS:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert set(schema.get("required", [])) <= set(schema["properties"]), tool["name"]
        for prop, spec in schema["properties"].items():
            assert spec.get("description"), f"{tool['name']}.{prop} has no description"
            declared = spec.get("type")
            if declared is not None:
                assert declared in JSON_TYPES, f"{tool['name']}.{prop}: {declared}"
            if spec.get("type") == "array":
                assert "items" in spec, f"{tool['name']}.{prop} is an array without items"


def test_no_tool_advertises_an_argument_its_handler_ignores():
    """Catches schema drift: a documented parameter that `execute_mcp_tool` never reads."""
    stale: dict[str, set[str]] = {}
    for tool in MCP_TOOLS:
        advertised = set(tool["inputSchema"]["properties"])
        consumed = handler_argument_names(tool["name"])
        ignored = advertised - consumed
        if ignored:
            stale[tool["name"]] = ignored
    assert stale == {}, f"advertised but never read: {stale}"


def test_no_handler_reads_an_argument_it_does_not_advertise():
    undocumented: dict[str, set[str]] = {}
    for tool in MCP_TOOLS:
        advertised = set(tool["inputSchema"]["properties"])
        extra = handler_argument_names(tool["name"]) - advertised
        if extra:
            undocumented[tool["name"]] = extra
    assert undocumented == {}, f"read but never advertised: {undocumented}"


def test_handler_argument_extraction_is_sound():
    names = handler_argument_names("ncbi_search")
    assert {"db", "query", "limit", "organism"} <= names
    assert handler_argument_names("ncbi_does_not_exist") == set()


def test_every_tool_declares_the_envelope_output_schema():
    assert ENVELOPE_OUTPUT_SCHEMA["type"] == "object"
    for key in ("ok", "command", "summary", "data"):
        assert key in ENVELOPE_OUTPUT_SCHEMA["properties"], key
    assert set(ENVELOPE_OUTPUT_SCHEMA["required"]) == {"ok", "command", "summary", "data"}
    for tool in MCP_TOOLS:
        assert tool.get("outputSchema") == ENVELOPE_OUTPUT_SCHEMA, tool["name"]


@pytest.fixture
def geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    local_http.add(
        "/query?acc=GSE100",
        "^SERIES = GSE100\n!Series_title = Schema study\n!Series_type = Methylation profiling by array\n"
        "!Series_sample_organism = Homo sapiens\n!Series_platform_id = GPL1\n!Series_sample_id = GSM1\n",
    )
    local_http.add(
        "/batch?acc=GSE100",
        "^SAMPLE = GSM1\n!Sample_title = s\n!Sample_series_id = GSE100\n!Sample_characteristics_ch1 = gender: F\n",
    )
    return local_http


def test_results_match_the_declared_output_schema(geo):
    """Tools that render Markdown must still carry the envelope so outputSchema holds."""
    required = set(ENVELOPE_OUTPUT_SCHEMA["required"])
    for name, args in (
        ("ncbi_inspect", {"accession": "GSE100", "check_file_sizes": False}),
        ("ncbi_samples", {"gse_id": "GSE100"}),
        ("ncbi_metadata", {"gse_id": "GSE100"}),
    ):
        text = execute_mcp_tool(name, args)
        envelope = json.loads(text.split("\n---\n")[-1]) if "\n---\n" in text else json.loads(text)
        assert required <= set(envelope), f"{name} envelope missing {required - set(envelope)}"
        assert envelope["ok"] is True and envelope["command"] == name


def test_manifest_lists_the_whole_agent_contract():
    manifest = mcp_manifest()
    assert manifest["server"]["name"] == "PyNCBI" and manifest["server"]["version"]
    assert manifest["protocol_versions"]
    assert len(manifest["tools"]) == len(MCP_TOOLS)
    assert len(manifest["resources"]) == len(MCP_RESOURCES)
    assert len(manifest["resource_templates"]) == len(MCP_RESOURCE_TEMPLATES)
    assert len(manifest["prompts"]) == len(MCP_PROMPTS)
    assert manifest["counts"] == {
        "tools": len(MCP_TOOLS),
        "resources": len(MCP_RESOURCES),
        "resource_templates": len(MCP_RESOURCE_TEMPLATES),
        "prompts": len(MCP_PROMPTS),
    }
    json.dumps(manifest)  # must be plain JSON


def test_cli_can_print_the_mcp_manifest(capsys):
    rc = cli_main(["schema", "mcp"])
    assert rc == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["counts"]["tools"] == len(MCP_TOOLS)
    assert any(t["name"] == "ncbi_screen" for t in manifest["tools"])


LIST_FRIENDLY_TOOLS = [
    ("ncbi_fasta", "accessions", ["NM_007294.4", "NM_000546.6"]),
    ("ncbi_pubmed", "pmids_or_query", ["32788292", "27713574"]),
    ("ncbi_gene", "query", ["BRCA1", "TP53"]),
    ("ncbi_taxonomy", "query", ["9606", "10090"]),
]


@pytest.mark.parametrize(("tool", "field", "value"), LIST_FRIENDLY_TOOLS, ids=lambda v: str(v)[:20])
def test_a_json_array_argument_is_read_as_the_list_it_is(tool, field, value):
    """Agents pass arrays for plural arguments; that must not look like an empty result.

    These schemas declare a comma-separated string. A JSON array used to be stringified
    into `"['NM_007294.4']"`, which NCBI could not resolve, so the tool reported success
    with zero records: the worst possible answer, because the agent concludes the record
    does not exist.
    """
    from PyNCBI.mcp.handlers import _argument_text

    assert _argument_text({field: value}, field) == ",".join(value)
    assert _argument_text({field: value[0]}, field) == value[0]
    assert _argument_text({field: 9606}, field) == "9606"
    assert _argument_text({}, field, default="fallback") == "fallback"
