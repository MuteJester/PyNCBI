"""Exercise real workflows with site-packages disabled, plus transport/streaming contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from PyNCBI import iter_supplementary_records
from PyNCBI.core.exceptions import DataError, RateLimitExceededError
from PyNCBI.io.http_client import NCBIClient, NCBIClientConfig

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = "import sys; sys.path.insert(0, sys.argv[1]); "


def bare_python(program, *arguments, input=None, cwd=None):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-c", BOOTSTRAP + program, str(ROOT / "src"), *arguments],
        input=input,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
        cwd=cwd,
    )


def test_python_cli_and_mcp_import_without_site_packages(tmp_path, monkeypatch):
    monkeypatch.setenv("PYNCBI_CACHE_DIR", str(tmp_path / "cache"))
    result = bare_python(
        "import PyNCBI, json; print(json.dumps({'version': PyNCBI.__version__, 'external': sorted(name for name in sys.modules if name.split('.')[0] not in sys.stdlib_module_names and not name.startswith('PyNCBI') and name != '__main__')}))",
        cwd=tmp_path,
    )
    assert json.loads(result.stdout)["external"] == []
    status = bare_python(
        "import runpy; sys.argv = ['pyncbi', 'status', '--offline', '--json']; runpy.run_module('PyNCBI.cli', run_name='__main__')",
        cwd=tmp_path,
    )
    assert json.loads(status.stdout)["ok"] is True
    messages = (
        "\n".join(
            json.dumps(value)
            for value in (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "ncbi_status", "arguments": {"probe_network": False}},
                },
            )
        )
        + "\n"
    )
    server = bare_python(
        "import runpy; sys.argv = ['pyncbi', 'mcp']; runpy.run_module('PyNCBI.cli', run_name='__main__')",
        input=messages,
        cwd=tmp_path,
    )
    replies = [json.loads(line) for line in server.stdout.splitlines()]
    assert replies[1]["result"]["tools"]
    assert replies[2]["result"]["isError"] is False


def test_bare_interpreter_retrieves_native_metadata_and_exact_measurements(local_http, tmp_path):
    local_http.add(
        "/query?acc=GSE1", "^SERIES = GSE1\n!Series_title = Native\n!Series_sample_id = GSM1\n"
    )
    local_http.add(
        "/batch?acc=GSE1",
        "^SAMPLE = GSM1\n!Sample_title = Native sample\n!Sample_characteristics_ch1 = group: case\n",
    )
    local_http.add_gzip(
        "/geo/series/GSEnnn/GSE1/matrix/GSE1_series_matrix.txt.gz",
        "!Series_title\tNative\n!series_matrix_table_begin\nID_REF\tGSM1\nNA\t9007199254740993\n0001\t0.12345678901234567\nmissing\tNA\n!series_matrix_table_end\n",
    )
    program = """
import json
from PyNCBI import Config, NCBIClient, load_expression, iter_records
Config.NCBI_QUERY_URL_TEMPLATE = sys.argv[2] + '/query?acc={accession}'
Config.NCBI_BATCH_GSM_URL_TEMPLATE = sys.argv[2] + '/batch?acc={accession}'
Config.NCBI_FTP_HTTPS_BASE = sys.argv[2]
with NCBIClient() as client:
    result = load_expression('GSE1', prefer='matrix', http_client=client)
    print(json.dumps({'matrix': result.matrix, 'samples': result.sample_table, 'records': list(iter_records(result.matrix))}))
"""
    result = bare_python(program, local_http.base_url, cwd=tmp_path)
    payload = json.loads(result.stdout)
    assert payload["matrix"] == {
        "columns": ["feature_id", "GSM1"],
        "rows": [["NA", 9007199254740993], ["0001", 0.12345678901234567], ["missing", None]],
    }
    assert payload["records"][0] == {"feature_id": "NA", "GSM1": 9007199254740993}
    assert "group" in payload["samples"]["columns"]


def test_streaming_rows_keep_exact_integers_and_can_close_early(local_http):
    url = local_http.add(
        "/counts.tsv", "GeneID\tGSM1\n0001\t9007199254740993\nNA\t0.12345678901234567\nlast\tNA\n"
    )
    with NCBIClient(NCBIClientConfig(max_retries=0)) as client:
        rows = iter_supplementary_records(url, http_client=client)
        assert next(rows) == {"feature_id": "0001", "GSM1": 9007199254740993}
        rows.close()
        assert client.metrics.summary()["total_requests"] == 1
        assert list(iter_supplementary_records(url, http_client=client))[-1] == {
            "feature_id": "last",
            "GSM1": None,
        }


def test_streaming_generator_keeps_its_transfer_budget_active(local_http):
    url = local_http.add("/large.tsv", "id\tGSM1\n" + "probe\t1\n" * 200)
    with pytest.raises(DataError, match="max_download_mb"):
        list(iter_supplementary_records(url, max_download_mb=0.0001))


@pytest.mark.parametrize("path", ["text", "range", "lines", "file"])
def test_http_status_retries_are_one_shared_budget_not_nested(local_http, tmp_path, path):
    url = local_http.add_failing("/limited", 429)
    with (
        NCBIClient(NCBIClientConfig(max_retries=1, retry_backoff=0)) as client,
        pytest.raises(RateLimitExceededError),
    ):
        if path == "text":
            client.get_text(url)
        elif path == "range":
            client.get_range_bytes(url)
        elif path == "lines":
            list(client.stream_lines(url))
        else:
            client.download_file(url, tmp_path / "data.bin")
    assert len(local_http.hits) == 2


def test_malformed_remote_json_is_a_domain_parse_error(local_http):
    from PyNCBI.core.exceptions import ParseError

    url = local_http.add("/bad.json", "not JSON")
    with NCBIClient(NCBIClientConfig(max_retries=0)) as client, pytest.raises(ParseError):
        client.get_json(url)


@pytest.mark.parametrize("source", ["rnaseq", "matrix", "table", "auto"])
def test_converted_download_cannot_mislabel_bytes_as_parquet(tmp_path, source):
    from PyNCBI import download_geo_files
    from PyNCBI.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="JSON, CSV or TSV"):
        download_geo_files("GSE1", tmp_path / "counts.parquet", source=source)
    assert not (tmp_path / "counts.parquet").exists()


@pytest.mark.parametrize("coding", ["gzip", "deflate"])
def test_http_encoding_redirects_and_cookies_stay_client_owned(coding):
    import gzip
    import threading
    import zlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    text = "identifier\tvalue\nNA\t9007199254740993\n"
    body = gzip.compress(text.encode()) if coding == "gzip" else zlib.compress(text.encode())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/encoded")
                self.send_header("Set-Cookie", "ncbi_sid=owned; Path=/")
                self.end_headers()
            elif self.headers.get("Cookie") != "ncbi_sid=owned":
                self.send_response(403)
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Encoding", coding)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with NCBIClient(NCBIClientConfig(max_retries=0)) as client:
            assert client.get_text(base + "/start") == text
            assert list(client.stream_lines(base + "/encoded")) == text.splitlines()
        from PyNCBI.core.exceptions import HTTPError

        with NCBIClient(NCBIClientConfig(max_retries=0)) as other, pytest.raises(HTTPError):
            other.get_text(base + "/encoded")
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
