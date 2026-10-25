"""JSON-RPC 2.0 message handling, capability negotiation, and the server manifest."""

from __future__ import annotations

from typing import Any

from PyNCBI import __version__
from PyNCBI.core.exceptions import error_payload
from PyNCBI.core.formatter import begin_run
from PyNCBI.core.serialization import json_dumps
from PyNCBI.mcp.handlers import _structured_from_text, execute_mcp_tool
from PyNCBI.mcp.prompts import MCP_PROMPTS, get_prompt
from PyNCBI.mcp.resources import MCP_RESOURCE_TEMPLATES, MCP_RESOURCES, read_resource
from PyNCBI.mcp.tools import MCP_TOOLS

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-06-18", "2025-03-26", "2024-11-05")


SERVER_INSTRUCTIONS = (
    "PyNCBI exposes NCBI (GEO, Entrez, PubMed/PMC, SRA, ClinVar, BioSample, Taxonomy, Datasets) as "
    "structured tools. Recommended path: `ncbi_screen` to compare candidate studies without downloading, "
    "`ncbi_metadata` for the harmonized cohort (every value carries provenance and confidence), "
    "`ncbi_expression` for the analysis-ready matrix, then `ncbi_download`/`ncbi_fastq` for raw files "
    "(pass dry_run=true first for anything large). Errors return error_code/hint/retryable; large results "
    "are paginated with page_token. Read `pyncbi://schema/study` for the record contract."
)


def mcp_manifest() -> dict[str, Any]:
    """The complete agent contract: server info, tools, resources, templates and prompts.

    Printed by `pyncbi schema mcp`, so the surface can be reviewed and diffed in CI.
    """
    return {
        "server": {"name": "PyNCBI", "version": __version__, "instructions": SERVER_INSTRUCTIONS},
        "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "transports": ["stdio", "streamable-http"],
        "tools": MCP_TOOLS,
        "resources": MCP_RESOURCES,
        "resource_templates": MCP_RESOURCE_TEMPLATES,
        "prompts": MCP_PROMPTS,
        "counts": {
            "tools": len(MCP_TOOLS),
            "resources": len(MCP_RESOURCES),
            "resource_templates": len(MCP_RESOURCE_TEMPLATES),
            "prompts": len(MCP_PROMPTS),
        },
    }


def handle_jsonrpc_message(msg: Any) -> dict[str, Any] | None:
    """Process a single JSON-RPC 2.0 message for the MCP server."""

    def invalid(code: int, message: str, request_id: Any = None) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return invalid(-32600, "Invalid Request: expected a JSON-RPC 2.0 object")
    msg_id = msg.get("id")
    if "id" in msg and (isinstance(msg_id, bool) or not isinstance(msg_id, (int, str))):
        return invalid(-32600, "Invalid Request: id must be a string or integer")
    # Client responses need no reply; the server currently initiates no requests.
    if "method" not in msg and ("result" in msg or "error" in msg):
        return None
    method = msg.get("method")
    if not isinstance(method, str) or not method:
        return invalid(-32600, "Invalid Request: method must be a nonempty string")
    params = msg.get("params", {})
    if not isinstance(params, dict):
        return (
            invalid(-32602, "Invalid params: expected an object", msg_id) if "id" in msg else None
        )
    if "id" not in msg:
        return None
    if method == "tools/call" and (
        not isinstance(params.get("name"), str) or not isinstance(params.get("arguments", {}), dict)
    ):
        return invalid(-32602, "Tool name must be a string and arguments an object", msg_id)
    if method == "prompts/get" and not isinstance(params.get("arguments", {}), dict):
        return invalid(-32602, "Prompt arguments must be an object", msg_id)

    if method == "initialize":
        requested = str(params.get("protocolVersion", SUPPORTED_PROTOCOL_VERSIONS[0]))
        negotiated = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        )
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "PyNCBI",
                    "version": __version__,
                    "title": "PyNCBI (NCBI for agents)",
                },
                "instructions": SERVER_INSTRUCTIONS,
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": MCP_TOOLS},
        }

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": MCP_RESOURCES}}

    if method == "resources/templates/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"resourceTemplates": MCP_RESOURCE_TEMPLATES},
        }

    if method == "resources/read":
        uri = str(params.get("uri", ""))
        try:
            contents = read_resource(uri)
        except Exception as e:  # noqa: BLE001 - surfaced as a JSON-RPC error
            payload = error_payload(e, command="resources/read", target=uri)
            code = -32602 if isinstance(e, ValueError) else -32603
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": code, "message": str(payload["message"]), "data": payload},
            }
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"contents": [contents]}}

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": MCP_PROMPTS}}

    if method == "prompts/get":
        try:
            rendered = get_prompt(str(params.get("name", "")), params.get("arguments") or {})
        except Exception as e:  # noqa: BLE001 - surfaced as a JSON-RPC error
            payload = error_payload(e, command="prompts/get", target=str(params.get("name", "")))
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": str(payload["message"]), "data": payload},
            }
        return {"jsonrpc": "2.0", "id": msg_id, "result": rendered}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        begin_run()
        try:
            output_text = execute_mcp_tool(tool_name, tool_args)
            result: dict[str, Any] = {
                "content": [{"type": "text", "text": output_text}],
                "isError": False,
            }
            structured = _structured_from_text(output_text)
            if structured is not None:
                result["structuredContent"] = structured
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:
            target = ""
            for key in (
                "accession",
                "gse_id",
                "gpl_id",
                "query",
                "pmids_or_query",
                "accessions",
                "accession_or_query",
            ):
                if tool_args.get(key):
                    target = str(tool_args[key])
                    break
            payload = error_payload(e, command=tool_name, target=target)
            payload["tool"] = tool_name
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json_dumps(payload, indent=2)}],
                    "structuredContent": payload,
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }
