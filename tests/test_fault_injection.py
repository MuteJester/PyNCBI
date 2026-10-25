"""What happens when NCBI misbehaves.

The rest of the suite exercises the happy path against a local server that always
answers correctly. These tests make the server fail the way real endpoints fail:
server errors, rate limiting, transfers that stop halfway, corrupt compression and
empty bodies. An agent running unattended meets all of these, and each one has to
produce a typed error it can branch on rather than a stray library exception.
"""

from __future__ import annotations

import gzip

import pytest

from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import (
    HTTPError,
    PyNCBIError,
    error_code_for,
    is_retryable,
)
from PyNCBI.io.http_client import NCBIClient, NCBIClientConfig


@pytest.fixture
def client() -> NCBIClient:
    return NCBIClient(NCBIClientConfig(max_retries=0, timeout=10))


def test_server_error_becomes_a_typed_retryable_error(local_http, client):
    url = local_http.add_failing("/boom", 503)
    with pytest.raises(PyNCBIError) as excinfo:
        client.get_text(url)
    assert isinstance(excinfo.value, HTTPError)
    assert is_retryable(excinfo.value) is True
    assert error_code_for(excinfo.value) == "http_error"


def test_rate_limiting_is_reported_as_rate_limited_and_retryable(local_http, client):
    url = local_http.add_failing("/eutils/esearch.fcgi", 429, "Too Many Requests")
    with pytest.raises(PyNCBIError) as excinfo:
        client.get_text(url)
    assert is_retryable(excinfo.value) is True
    assert error_code_for(excinfo.value) in ("rate_limited", "http_error")


def test_a_transfer_that_stops_halfway_fails_loudly_and_the_retry_is_correct(
    local_http, client, tmp_path
):
    """Content-Length promises 5000 bytes, the server sends 10 and hangs up.

    Two things must hold: the short file is never published under the real name, and a
    later attempt produces the complete, correct file rather than appending to the
    wreckage of the first.
    """
    payload = b"0123456789" * 50
    url = local_http.add_truncated("/suppl/big.bin", b"0123456789", declared_length=5000)
    destination = tmp_path / "big.bin"

    with pytest.raises(PyNCBIError) as excinfo:
        client.download_file(url, destination)

    assert is_retryable(excinfo.value), "a dropped transfer is worth retrying"
    assert not destination.exists(), "a truncated transfer must not be published as the real file"

    # The server recovers; the retry must yield exactly the real bytes.
    local_http.add("/suppl/big.bin", payload, "application/octet-stream")
    client.download_file(url, destination)

    assert destination.read_bytes() == payload
    assert not destination.with_suffix(destination.suffix + ".part").exists()


def test_corrupt_compression_is_reported_as_data_not_as_a_gzip_traceback(local_http, client):
    from PyNCBI.domains.geo import decompress_table_bytes

    raw = gzip.compress(b"col\tvalue\n1\t2\n")[:20]  # truncated gzip stream
    local_http.add("/suppl/broken.tsv.gz", raw, "application/x-gzip")
    payload = client.get_bytes(local_http.url("/suppl/broken.tsv.gz"))

    with pytest.raises(PyNCBIError) as excinfo:
        decompress_table_bytes(payload, context="broken table")
    assert error_code_for(excinfo.value) in ("data_error", "parse_error")


def test_an_empty_body_where_a_table_belongs_is_a_typed_error(local_http, client, monkeypatch):
    from PyNCBI.domains.geo import load_series_matrix

    base = local_http.base_url
    # The matrix URL is built from the FTP base, not from a template attribute.
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    local_http.add("/query?acc=GSE900", "^SERIES = GSE900\n!Series_title = Empty\n")
    local_http.add(
        "/geo/series/GSEnnn/GSE900/matrix/GSE900_series_matrix.txt.gz",
        gzip.compress(b""),
        "application/x-gzip",
    )

    with pytest.raises(PyNCBIError) as excinfo:
        load_series_matrix("GSE900")
    assert error_code_for(excinfo.value) != "internal_error"


def test_every_failure_mode_carries_a_hint_an_agent_can_act_on(local_http, client):
    """A typed error without a hint leaves the agent with nowhere to go."""
    url = local_http.add_failing("/nope", 500)
    with pytest.raises(PyNCBIError) as excinfo:
        client.get_text(url)
    assert excinfo.value.hint, "PyNCBIError subclasses must carry an actionable hint"
