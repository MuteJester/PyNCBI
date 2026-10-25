"""Model Context Protocol (MCP) server entry point for PyNCBI.

Implements a zero-dependency, standards-compliant JSON-RPC 2.0 MCP server over
`stdio` and Streamable HTTP, so AI agents (Claude Desktop, Claude Code, Cursor,
Antigravity, ...) can natively search, inspect, extract metadata tables, and
download raw files from NCBI.

The implementation is split by responsibility and this module is the stable import
surface for all of it:

- :mod:`~PyNCBI.mcp.tools` - the tool catalogue, behaviour annotations, output envelope
- :mod:`~PyNCBI.mcp.handlers` - tool dispatch and rendering
- :mod:`~PyNCBI.mcp.resources` - resources and resource templates
- :mod:`~PyNCBI.mcp.prompts` - ready-made agent workflows
- :mod:`~PyNCBI.mcp.protocol` - JSON-RPC handling, negotiation, and the manifest
- :mod:`~PyNCBI.mcp.transport` - the stdio and HTTP transports

Launch via::

    pyncbi mcp
    pyncbi mcp --http --port 8765
    python -m PyNCBI.mcp.server
"""

from __future__ import annotations

from PyNCBI.mcp.handlers import _structured_from_text, execute_mcp_tool
from PyNCBI.mcp.prompts import MCP_PROMPTS, get_prompt
from PyNCBI.mcp.protocol import (
    SERVER_INSTRUCTIONS,
    SUPPORTED_PROTOCOL_VERSIONS,
    handle_jsonrpc_message,
    mcp_manifest,
)
from PyNCBI.mcp.resources import (
    MCP_RESOURCE_TEMPLATES,
    MCP_RESOURCES,
    RESOURCE_TABLE_ROW_LIMIT,
    read_resource,
)
from PyNCBI.mcp.tools import ENVELOPE_OUTPUT_SCHEMA, MCP_TOOLS
from PyNCBI.mcp.transport import (
    MCP_HTTP_PATHS,
    MCPHTTPRequestHandler,
    create_http_server,
    run_http_server,
    run_mcp_server,
)

__all__ = [
    "ENVELOPE_OUTPUT_SCHEMA",
    "MCP_HTTP_PATHS",
    "MCP_PROMPTS",
    "MCP_RESOURCES",
    "MCP_RESOURCE_TEMPLATES",
    "MCP_TOOLS",
    "RESOURCE_TABLE_ROW_LIMIT",
    "SERVER_INSTRUCTIONS",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "MCPHTTPRequestHandler",
    "_structured_from_text",
    "create_http_server",
    "execute_mcp_tool",
    "get_prompt",
    "handle_jsonrpc_message",
    "mcp_manifest",
    "read_resource",
    "run_http_server",
    "run_mcp_server",
]


if __name__ == "__main__":
    run_mcp_server()
