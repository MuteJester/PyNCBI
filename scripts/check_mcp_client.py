"""Smoke-test stdio and HTTP with the independent MCP SDK (protocol-test extra)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

try:
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # MCP SDK 1.x
    from mcp.client.streamable_http import streamablehttp_client as streamable_http_client

from PyNCBI.mcp.protocol import mcp_manifest
from PyNCBI.mcp.transport import create_http_server


async def exercise(read, write):
    async with ClientSession(read, write) as session:
        initialized = await session.initialize()
        tools = await session.list_tools()
        resource = await session.read_resource("pyncbi://schema/study")
        result = await session.call_tool("ncbi_status", {})
        payload = result.model_dump(by_alias=True)
        assert {tool.name for tool in tools.tools} == {
            tool["name"] for tool in mcp_manifest()["tools"]
        }
        assert not payload["isError"] and payload["structuredContent"]["ok"]
        assert resource.contents
        return {
            "protocol": initialized.model_dump(by_alias=True)["protocolVersion"],
            "tools": len(tools.tools),
        }


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "PyNCBI.cli", "mcp"],
        env={**os.environ, "PYNCBI_AGENT_MODE": "1"},
    )
    async with stdio_client(server) as (read, write):
        print("SDK stdio:", json.dumps(await exercise(read, write)))
    http = create_http_server("127.0.0.1", 0)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        async with streamable_http_client(
            f"http://127.0.0.1:{http.server_address[1]}/mcp"
        ) as streams:
            print("SDK HTTP:", json.dumps(await exercise(streams[0], streams[1])))
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


if __name__ == "__main__":
    asyncio.run(main())
