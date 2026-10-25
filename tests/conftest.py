from __future__ import annotations

import gzip
import inspect
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

import pytest


@pytest.fixture(autouse=True)
def _pyncbi_cache_dir(tmp_path, monkeypatch):
    cache_dir = tmp_path / "pyncbi_cache"
    monkeypatch.setenv("PYNCBI_CACHE_DIR", str(cache_dir))
    for var in ("NCBI_API_KEY", "PYNCBI_API_KEY", "NCBI_EMAIL", "PYNCBI_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    from PyNCBI.core.config import reset_config

    reset_config()
    _clear_default_client_cache()
    yield cache_dir
    _clear_default_client_cache()
    reset_config()


def _clear_default_client_cache() -> None:
    """The default client is a process singleton; its response cache must not cross tests."""
    from PyNCBI.io import http_client

    if http_client._default_client is not None:
        http_client._default_client.clear_response_cache()
        http_client._default_client.metrics.reset()


@pytest.fixture(autouse=True)
def _no_accidental_network(request, monkeypatch):
    """Offline tests may talk to 127.0.0.1 and nothing else.

    A test that patches a config attribute which does not exist (``raising=False``) silently
    falls back to the real NCBI URL and passes for the wrong reason, while making the suite
    slow, flaky and dependent on the network. Blocking outbound sockets turns that mistake
    into an immediate, obvious failure. Tests marked `integration` are exempt.
    """
    if request.node.get_closest_marker("integration"):
        yield
        return

    import socket

    real_connect = socket.socket.connect
    loopback = {"127.0.0.1", "::1", "localhost"}

    class BlockedNetworkError(OSError):
        """Raised instead of connecting. Subclasses OSError so urllib3 still closes the
        socket it just opened; a plain RuntimeError leaks the file descriptor."""

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in loopback:
            raise BlockedNetworkError(
                f"offline test attempted a network connection to {host!r}; "
                "point the Config URL at the local_http fixture instead"
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield


def _unpack(found) -> tuple[bytes, str, int, int | None]:
    """Normalise a route value to `(body, content_type, status, declared_length)`."""
    body, ctype = found[0], found[1]
    status = found[2] if len(found) > 2 else 200
    declared = found[3] if len(found) > 3 else None
    return body, ctype, status, declared


class LocalRoutes:
    """Mutable route table for the local HTTP test server.

    Routes map a path (with optional query string) to `(body_bytes, content_type)`.
    A route value may also be a callable returning that tuple.
    """

    def __init__(self) -> None:
        self.routes: dict[str, tuple[bytes, str]] = {}
        self.hits: list[str] = []
        self.base_url = ""

    def add(
        self,
        path: str,
        body: bytes | str,
        content_type: str = "text/plain",
        status: int = 200,
    ) -> str:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.routes[path] = (body, content_type, status)
        return self.base_url + path

    def add_failing(self, path: str, status: int, body: bytes | str = b"server error") -> str:
        """A route that answers with an HTTP error, for fault-injection tests."""
        return self.add(path, body, "text/plain", status)

    def add_truncated(self, path: str, body: bytes | str, declared_length: int) -> str:
        """A route that promises `declared_length` bytes but sends fewer (a dropped transfer)."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.routes[path] = (body, "application/octet-stream", 200, declared_length)
        return self.base_url + path

    def add_gzip(self, path: str, text: str, content_type: str = "application/x-gzip") -> str:
        return self.add(path, gzip.compress(text.encode("utf-8")), content_type)

    def add_dynamic(self, path: str, func) -> str:
        """Register a callable returning `(body_bytes, content_type)` per request.

        The callable may take no arguments, or one argument which receives the parsed
        query string as a dict (useful for CGI-style APIs that switch on a parameter).
        """
        self.routes[path] = func  # type: ignore[assignment]
        return self.base_url + path

    def url(self, path: str) -> str:
        return self.base_url + path


@pytest.fixture
def local_http(monkeypatch) -> LocalRoutes:
    """Spin up a real local HTTP server (supports HEAD and Range) for client tests."""
    table = LocalRoutes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # silence
            return

        def _lookup(self):
            parsed = urlparse(self.path)
            key_full = parsed.path + ("?" + parsed.query if parsed.query else "")
            table.hits.append(key_full)
            found = table.routes.get(key_full) or table.routes.get(parsed.path)
            if callable(found):
                # A dynamic route may accept the parsed query string (one positional argument)
                takes_query = len(inspect.signature(found).parameters) >= 1
                return found(dict(parse_qsl(parsed.query))) if takes_query else found()
            return found

        def do_HEAD(self):
            found = self._lookup()
            if not found:
                self.send_response(404)
                self.end_headers()
                return
            body, ctype, status = _unpack(found)[:3]
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            found = self._lookup()
            if not found:
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html>not found</html>")
                return
            body, ctype, status, declared = _unpack(found)
            if status >= 400:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if declared is not None:
                # Promise more than we send: the transfer looks dropped mid-stream.
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(declared))
                self.end_headers()
                self.wfile.write(body)
                return
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start_s, _, end_s = rng[len("bytes=") :].partition("-")
                start = int(start_s or 0)
                end = int(end_s) if end_s else len(body) - 1
                end = min(end, len(body) - 1)
                chunk = body[start : end + 1]
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    table.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield table
    finally:
        server.shutdown()
        server.server_close()
