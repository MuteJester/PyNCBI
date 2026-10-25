"""Private urllib transport: TLS, cookies, redirects and streaming response bodies.

NCBI retry/rate/budget policy belongs to NCBIClient, rather than this adapter.
"""

from __future__ import annotations

import http.client
import http.cookiejar
import json
import socket
import ssl
import urllib.error
import urllib.request
import zlib
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urlencode

from PyNCBI.core.exceptions import ParseError


class TransportError(Exception):
    """An HTTP transport or body-decoding failure."""


class TransportTimeout(TransportError):
    """Socket timeout."""


class TransportConnectionError(TransportError):
    """Connection, TLS, or incomplete-body error."""


class HTTPStatusError(TransportError):
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        super().__init__(f"HTTP {response.status_code}")


def _failure(exc: BaseException) -> TransportError:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return TransportTimeout(str(exc))
    if isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason, (TimeoutError, socket.timeout)
    ):
        return TransportTimeout(str(exc.reason))
    return TransportConnectionError(str(exc))


class HTTPResponse:
    """One owned response socket, optionally buffered by the client for metadata caching."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.status_code = raw.status
        self.headers = raw.headers
        self.url = raw.geturl()
        self.encoding = raw.headers.get_content_charset() or "utf-8"
        self._content: bytes | None = None

    @property
    def content(self) -> bytes:
        return self._content or b""

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ParseError(
                "JSON response",
                "The remote endpoint returned invalid JSON",
            ) from exc

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPStatusError(self)

    def iter_content(self, chunk_size: int = 65536) -> Iterator[bytes]:
        if self._content is not None:
            for offset in range(0, len(self._content), chunk_size):
                yield self._content[offset : offset + chunk_size]
            return
        coding = self.headers.get("Content-Encoding", "").lower()
        decoder = (
            zlib.decompressobj(16 + zlib.MAX_WBITS)
            if coding == "gzip"
            else zlib.decompressobj()
            if coding == "deflate"
            else None
        )
        received = 0
        length = self.headers.get("Content-Length", "")
        try:
            while True:
                chunk = self.raw.read(chunk_size)
                if not chunk:
                    break
                received += len(chunk)
                data = decoder.decompress(chunk) if decoder else chunk
                if data:
                    yield data
            if length.isdigit() and received < int(length):
                raise TransportConnectionError(f"Incomplete body: {received} of {length} bytes")
            if decoder is not None:
                tail = decoder.flush()
                if tail:
                    yield tail
                if not decoder.eof:
                    raise TransportConnectionError("Incomplete HTTP content encoding")
        except (OSError, http.client.HTTPException, zlib.error) as exc:
            raise _failure(exc) from exc

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> HTTPResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class HTTPTransport:
    """Client-owned urllib opener; every request owns and closes its response handle."""

    def __init__(self, headers: Mapping[str, str] | None = None) -> None:
        self.headers = dict(headers or {})
        self.cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        self._closed = False

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, Any] | bytes | None = None,
        timeout: float = 30,
        stream: bool = True,
        allow_redirects: bool = True,
    ) -> HTTPResponse:
        if self._closed:
            raise TransportConnectionError("HTTP transport is closed")
        body = urlencode(data).encode("utf-8") if isinstance(data, Mapping) else data
        request = urllib.request.Request(
            url, data=body, headers={**self.headers, **(headers or {})}, method=method
        )
        try:
            try:
                raw = self._opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                raw = exc  # preserve error bodies/status; NCBIClient owns the taxonomy
            response = HTTPResponse(raw)
            if not stream or method == "HEAD":
                try:
                    if method != "HEAD":
                        response._content = b"".join(response.iter_content())
                finally:
                    response.close()
            return response
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            raise _failure(exc) from exc

    def get(self, url: str, **kwargs: Any) -> HTTPResponse:
        return self.request("GET", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> HTTPResponse:
        return self.request("HEAD", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> HTTPResponse:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._closed = True
        self.cookies.clear()
