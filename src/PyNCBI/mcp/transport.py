"""MCP stdio and Streamable HTTP transports (single JSON-RPC messages)."""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from PyNCBI import __version__
from PyNCBI.core.exceptions import error_payload
from PyNCBI.core.logging import get_logger
from PyNCBI.core.serialization import json_dumps
from PyNCBI.mcp.protocol import SUPPORTED_PROTOCOL_VERSIONS, handle_jsonrpc_message

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
MCP_HTTP_PATHS = frozenset({"/mcp", "/"})
_MAX_BODY_BYTES = 8 * 1024 * 1024
_SESSION_TTL = 3600
_MAX_SESSIONS = 1024


def _origin_is_local(origin: str) -> bool:
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
        return parsed.scheme in ("http", "https") and parsed.hostname in _LOOPBACK_HOSTS
    except ValueError:
        return False


def _parse_message(raw: str) -> Any:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON constant: {value}")

    return json.loads(raw, parse_constant=invalid_constant)


def _rpc_error(code: int, message: str, request_id: Any = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _handle_message(message: Any) -> dict[str, Any] | None:
    try:
        return handle_jsonrpc_message(message)
    except Exception as exc:  # malformed input must never terminate a transport
        request_id = message.get("id") if isinstance(message, dict) else None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32603,
                "message": "Internal error",
                "data": error_payload(exc, command="mcp"),
            },
        }


class _MCPHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, MCPHTTPRequestHandler)
        self.mcp_sessions: dict[str, tuple[str, float]] = {}
        self.session_lock = threading.Lock()

    def expire_sessions(self) -> None:
        now = time.monotonic()
        for sid, (_, seen) in list(self.mcp_sessions.items()):
            if now - seen > _SESSION_TTL:
                del self.mcp_sessions[sid]


class MCPHTTPRequestHandler(BaseHTTPRequestHandler):
    """JSON responses, validated session lifecycle and local browser origins."""

    server: _MCPHTTPServer
    server_version = f"PyNCBI/{__version__}"
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        get_logger(__name__).debug("MCP HTTP %s", format % args)

    def _send(
        self, status: int, payload: Any = None, *, headers: dict[str, str] | None = None
    ) -> None:
        body = b"" if payload is None else json_dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, status: int, code: int, message: str) -> None:
        # Rejected requests may still have an unread body. Do not parse it as the next request.
        self.close_connection = True
        self._send(status, _rpc_error(code, message))

    def _validate_endpoint(self) -> bool:
        if not _origin_is_local(self.headers.get("Origin", "")):
            self._error(403, -32600, "Origin not allowed; only local HTTP(S) origins are accepted")
            return False
        if urlparse(self.path).path not in MCP_HTTP_PATHS:
            self._error(404, -32601, "No MCP endpoint here; use /mcp")
            return False
        return True

    def _validate_session(self) -> bool:
        sid = self.headers.get("Mcp-Session-Id", "")
        if not sid:
            self._error(400, -32600, "Missing Mcp-Session-Id; initialize a session first")
            return False
        with self.server.session_lock:
            self.server.expire_sessions()
            entry = self.server.mcp_sessions.get(sid)
            if entry is not None:
                self.server.mcp_sessions[sid] = (entry[0], time.monotonic())
        if entry is None:
            self._error(404, -32600, "Unknown or terminated MCP session; initialize again")
            return False
        version = self.headers.get("MCP-Protocol-Version", entry[0])
        if version not in SUPPORTED_PROTOCOL_VERSIONS or version != entry[0]:
            self._error(400, -32600, "Invalid MCP-Protocol-Version for this session")
            return False
        return True

    def do_POST(self) -> None:  # noqa: N802
        if not self._validate_endpoint():
            return
        if self.headers.get_content_type() != "application/json":
            self._error(415, -32600, "Content-Type must be application/json")
            return
        accept = self.headers.get("Accept", "application/json")
        if "application/json" not in accept and "*/*" not in accept:
            self._error(406, -32600, "Accept must include application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or self.headers.get("Transfer-Encoding"):
            self._error(400, -32600, "A positive Content-Length is required")
            return
        if length > _MAX_BODY_BYTES:
            self._error(413, -32600, "MCP request body is too large")
            return
        try:
            message = _parse_message(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._error(400, -32700, "Parse error: request body is not valid JSON")
            return
        if not isinstance(message, dict):
            self._error(400, -32600, "Expected a single JSON-RPC object; batches are not supported")
            return
        initialize = message.get("method") == "initialize"
        if not initialize and not self._validate_session():
            return
        reply = _handle_message(message)
        headers: dict[str, str] = {}
        if initialize and reply is not None and "result" in reply:
            with self.server.session_lock:
                self.server.expire_sessions()
                if len(self.server.mcp_sessions) >= _MAX_SESSIONS:
                    self._error(503, -32603, "Too many active sessions; try again later")
                    return
                sid = uuid.uuid4().hex
                self.server.mcp_sessions[sid] = (
                    reply["result"]["protocolVersion"],
                    time.monotonic(),
                )
            headers["Mcp-Session-Id"] = sid
        self._send(202 if reply is None else 200, reply, headers=headers)

    def do_GET(self) -> None:  # noqa: N802
        if self._validate_endpoint() and self._validate_session():
            self._error(405, -32601, "SSE streams are not offered; POST JSON-RPC to /mcp")

    def do_DELETE(self) -> None:  # noqa: N802
        if self._validate_endpoint() and self._validate_session():
            with self.server.session_lock:
                self.server.mcp_sessions.pop(self.headers["Mcp-Session-Id"], None)
            self._send(204)


def create_http_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return _MCPHTTPServer((host, port))


def run_http_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    from PyNCBI.core.logging import silence

    silence()
    server = create_http_server(host, port)
    bound_host, bound_port = str(server.server_address[0]), server.server_address[1]
    if bound_host not in _LOOPBACK_HOSTS:
        sys.stderr.write(
            f"WARNING: binding MCP HTTP to {bound_host} exposes it to the network; use your own authentication.\n"
        )
    sys.stderr.write(
        f"PyNCBI {__version__} MCP HTTP transport on http://{bound_host}:{bound_port}/mcp\n"
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def run_mcp_server() -> None:
    from PyNCBI.core.logging import silence

    silence()
    response: dict[str, Any] | None
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            request = _parse_message(raw_line)
        except ValueError:
            response = _rpc_error(-32700, "Parse error: invalid JSON")
        else:
            response = _handle_message(request)
        if response is not None:
            sys.stdout.write(json_dumps(response) + "\n")
            sys.stdout.flush()
