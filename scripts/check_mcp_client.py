"""Exercise research workflows with the independent MCP SDK over stdio and HTTP.

Uses a loopback NCBI fixture, not live services or model-provider APIs. Install the
protocol-test extra before running. This checks workflows, not model tool selection.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from agent_workflow_fixture import ncbi_fixture
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

try:
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # MCP SDK 1.x
    from mcp.client.streamable_http import streamablehttp_client as streamable_http_client

from PyNCBI.core.config import Config, get_config, set_config
from PyNCBI.mcp.protocol import mcp_manifest
from PyNCBI.mcp.transport import create_http_server


async def exercise(read, write, root, hits):
    async with ClientSession(read, write) as session:
        initialized = await session.initialize()
        tools = await session.list_tools()
        resource = await session.read_resource("pyncbi://schema/study")
        prompts = await session.list_prompts()
        assert {tool.name for tool in tools.tools} == {
            tool["name"] for tool in mcp_manifest()["tools"]
        }
        assert resource.contents and prompts.prompts
        response_sizes = []

        async def call(name, arguments, *, error=False):
            result = await session.call_tool(name, arguments)
            payload = result.model_dump(by_alias=True)
            assert bool(payload["isError"]) is error, payload
            envelope = payload["structuredContent"]
            assert envelope["ok"] is not error and envelope["schema_version"] == "2.0"
            size = len(json.dumps(payload).encode())
            assert size < 30_000, (name, size)
            response_sizes.append(size)
            return envelope

        await call("ncbi_status", {"probe_network": False})
        await call("ncbi_inspect", {"accession": "GSE100", "check_file_sizes": False})
        first = await call("ncbi_samples", {"gse_id": "GSE100", "as_json": True, "page_size": 1})
        second = await call(
            "ncbi_samples",
            {
                "gse_id": "GSE100",
                "as_json": True,
                "page_size": 1,
                "page_token": first["data"]["next_page_token"],
            },
        )
        assert [first["data"]["rows"][0]["gsm_id"], second["data"]["rows"][0]["gsm_id"]] == [
            "GSM1",
            "GSM2",
        ]
        assert second["data"]["next_page_token"] is None
        selected = await call(
            "ncbi_samples", {"gse_id": "GSE100", "as_json": True, "where": "group == 'case'"}
        )
        assert [row["gsm_id"] for row in selected["data"]["rows"]] == ["GSM1"]

        destination = root / "matrix.json"
        matrix = await call(
            "ncbi_expression",
            {
                "gse_id": "GSE100",
                "prefer": "matrix",
                "output_path": str(destination),
            },
        )
        saved = json.loads(destination.read_text(encoding="utf-8"))
        assert saved == {
            "columns": ["feature_id", "GSM1", "GSM2"],
            "rows": [["0001", 9007199254740993, 7], ["NA", 0.12345678901234567, None]],
        }
        assert str(destination) in matrix["saved_files"]
        assert destination.with_name("matrix_samples.csv").is_file()

        raw = root / "raw"
        before = len(hits)
        plan = await call(
            "ncbi_download",
            {
                "accession": "GSE100",
                "source": "supp",
                "output_path": str(raw),
                "dry_run": True,
            },
        )
        assert plan["data"]["file_count"] == 1 and not raw.exists()
        assert ("GET", "/supp/raw.tsv") not in hits[before:]
        before = len(hits)
        failure = await call("ncbi_download", {"accession": "GSE100", "dryrun": True}, error=True)
        assert failure["error_code"] == "invalid_argument" and len(hits) == before
        await call("ncbi_status", {"probe_network": False})  # errors must leave the session usable
        return {
            "protocol": initialized.model_dump(by_alias=True)["protocolVersion"],
            "tools": len(tools.tools),
            "calls": len(response_sizes),
            "largest_response_bytes": max(response_sizes),
        }


async def main() -> None:
    with (
        tempfile.TemporaryDirectory(prefix="pyncbi-mcp-workflows-") as folder,
        ncbi_fixture() as (endpoints, hits),
    ):
        root = Path(folder)
        env = {**os.environ, "PYNCBI_AGENT_MODE": "1", "PYNCBI_CACHE_DIR": str(root / "cache")}
        for name in (
            "NCBI_API_KEY",
            "PYNCBI_API_KEY",
            "NCBI_EMAIL",
            "PYNCBI_EMAIL",
            "PYNCBI_AUDIT_LOG",
        ):
            env.pop(name, None)
        bootstrap = (
            "import json,sys; from PyNCBI.core.config import Config; "
            "[setattr(Config,k,v) for k,v in json.loads(sys.argv[1]).items()]; "
            "from PyNCBI.mcp.transport import run_mcp_server; run_mcp_server()"
        )
        server = StdioServerParameters(
            command=sys.executable, args=["-c", bootstrap, json.dumps(endpoints)], env=env
        )
        async with stdio_client(server) as (read, write):
            print("SDK stdio:", json.dumps(await exercise(read, write, root / "stdio", hits)))

        previous = get_config()
        original = {name: getattr(Config, name) for name in endpoints}
        for name, value in endpoints.items():
            setattr(Config, name, value)
        set_config(
            Config(
                cache_folder=root / "http-cache",
                api_key=None,
                email=None,
                audit_log_path=None,
                agent_mode=True,
            )
        )
        http = create_http_server("127.0.0.1", 0)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        try:
            async with streamable_http_client(
                f"http://127.0.0.1:{http.server_address[1]}/mcp"
            ) as streams:
                print(
                    "SDK HTTP:",
                    json.dumps(await exercise(streams[0], streams[1], root / "http", hits)),
                )
        finally:
            http.shutdown()
            http.server_close()
            worker.join()
            for name, value in original.items():
                setattr(Config, name, value)
            set_config(previous)


if __name__ == "__main__":
    asyncio.run(main())
