"""Release regressions: exact data, bounded transfers and interoperable agent output."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import threading

import pandas as pd
import pytest
import requests

import PyNCBI
from PyNCBI.core.config import Config, reset_config
from PyNCBI.core.exceptions import PyNCBIError
from PyNCBI.core.formatter import render_output, save_data
from PyNCBI.io.cache import LocalCache
from PyNCBI.io.http_client import NCBIClient
from PyNCBI.mcp.server import create_http_server, handle_jsonrpc_message
from PyNCBI.mcp.transport import _origin_is_local
from PyNCBI.parsing.matrix_parser import SeriesMatrixParser


def _strict_json(text):
    def reject(value):
        raise ValueError(value)

    return json.loads(text, parse_constant=reject)


def test_cache_preserves_exact_floats_large_integers_and_nullable_columns(tmp_path):
    frame = pd.DataFrame(
        {
            "beta": [0.12345678901234567, float("nan")],
            "count": pd.array([9007199254740993, None], dtype="Int64"),
            "flag": pd.array([True, None], dtype="boolean"),
            "label": pd.array(["0001234", None], dtype="string"),
            "category": pd.Categorical(
                ["case", "control"], categories=["control", "case"], ordered=True
            ),
            "date": pd.to_datetime(["2026-01-01", None], utc=True),
        },
        index=pd.Index(["0001234", "0005678"], name="probe"),
    )
    cache = LocalCache(tmp_path)
    cache.set("GSE1", {"data": frame, "series": frame["beta"]})
    restored = cache.get("GSE1")
    pd.testing.assert_frame_equal(restored["data"], frame, check_exact=True)
    pd.testing.assert_series_equal(restored["series"], frame["beta"], check_exact=True)
    assert restored["data"].iloc[0, 1] == 9007199254740993


def test_old_rounded_cache_is_a_miss_and_is_replaced(tmp_path):
    cache = LocalCache(tmp_path)
    legacy = {
        "data": {
            "__pyncbi__": "DataFrame",
            "value": '{"columns":["GSM1"],"index":["cg1"],"data":[[0.123456789]]}',
        }
    }
    cache._key_to_path("GSE1").write_bytes(gzip.compress(json.dumps(legacy).encode()))
    assert cache.get("GSE1") is None
    cache.set("GSE1", pd.DataFrame({"GSM1": [0.12345678901234567]}))
    assert cache.get("GSE1").iloc[0, 0] == 0.12345678901234567


def test_json_table_export_retains_ids_and_full_precision_and_has_no_nan(tmp_path):
    frame = pd.DataFrame(
        {"GSM1": [0.12345678901234567, float("nan"), float("inf")]},
        index=pd.Index(["0001234", "cg2", "cg3"], name="probe"),
    )
    path = save_data(frame, tmp_path / "measurements.json")
    rows = _strict_json(path.read_text(encoding="utf-8"))
    assert rows[0] == {"probe": "0001234", "GSM1": 0.12345678901234567}
    assert rows[1]["GSM1"] is None and rows[2]["GSM1"] is None
    envelope = _strict_json(render_output(frame, mode="json"))
    assert envelope["data"]["preview"][1]["GSM1"] is None


def test_numeric_feature_ids_keep_leading_zeros_in_every_table_path(tmp_path):
    table = "ID_REF\tGSM1\n0001234\t0.12345678901234567\n0005678\t0.5\n"
    path = tmp_path / "measurements.tsv"
    path.write_text(table, encoding="utf-8")
    supplementary = PyNCBI.read_supplementary_table(path)
    matrix = (
        SeriesMatrixParser()
        .parse(
            '!Series_title\t"IDs"\n!series_matrix_table_begin\n'
            + table
            + "!series_matrix_table_end\n"
        )
        .matrix_df
    )
    for frame in (supplementary, matrix):
        assert list(frame.index) == ["0001234", "0005678"]
        assert frame.iloc[0, 0] == 0.12345678901234567


def test_budget_applies_to_matrix_exports_and_unknown_head_sizes(local_http, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", local_http.base_url)
    monkeypatch.setattr(
        Config, "NCBI_QUERY_URL_TEMPLATE", local_http.base_url + "/query?acc={accession}"
    )
    table = '!Series_title\t"Budget"\n!series_matrix_table_begin\nID_REF\tGSM1\ncg1\t0.5\n!series_matrix_table_end\n'
    local_http.add_gzip("/geo/series/GSEnnn/GSE100/matrix/GSE100_series_matrix.txt.gz", table)
    with pytest.raises(PyNCBIError, match="max_download_mb"):
        PyNCBI.download_geo_files(
            "GSE100", tmp_path / "matrix.csv", source="matrix", max_download_mb=0.000001
        )
    assert not (tmp_path / "matrix.csv").exists()

    url = local_http.add("/suppl/big.bin", b"x" * 16384)
    local_http.add(
        "/query?acc=GSE100",
        "^SERIES = GSE100\n!Series_title = B\n!Series_supplementary_file = " + url + "\n",
    )
    with NCBIClient() as client:
        real = client.inspect_url

        def unknown(*args, **kwargs):
            return {**real(*args, **kwargs), "size_bytes": None}

        monkeypatch.setattr(client, "inspect_url", unknown)
        with pytest.raises(PyNCBIError, match="max_download_mb"):
            PyNCBI.download_geo_files(
                "GSE100", tmp_path / "raw", source="supp", max_download_mb=0.001, http_client=client
            )
    assert not (tmp_path / "raw" / "big.bin").exists()


def test_environment_budget_also_applies_to_fastq_and_genome(local_http, monkeypatch, tmp_path):
    monkeypatch.setenv("PYNCBI_MAX_DOWNLOAD_MB", "0.001")
    reset_config()
    url = local_http.add("/suppl/SRR1.fastq.gz", b"x" * 4096)
    manifest = pd.DataFrame(
        {"file_url": [url], "file_name": ["SRR1.fastq.gz"], "md5": [""], "run_accession": ["SRR1"]}
    )
    with pytest.raises(PyNCBIError, match="max_download_mb"):
        PyNCBI.download_fastq(manifest, tmp_path / "fastq")
    # The explicit override must win over the environment default.
    assert (
        PyNCBI.download_fastq(manifest, tmp_path / "allowed", max_download_mb=1)[0].stat().st_size
        == 4096
    )


def test_import_and_cli_version_work_without_a_writable_cache(tmp_path):
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("file", encoding="utf-8")
    env = {**os.environ, "PYNCBI_CACHE_DIR": str(blocked / "cache")}
    for args in (["-c", "import PyNCBI"], ["-m", "PyNCBI.cli", "--version"]):
        result = subprocess.run(
            [sys.executable, *args], env=env, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "message",
    [
        [],
        None,
        1,
        {"jsonrpc": "2.0", "id": True, "method": "ping"},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ncbi_status", "arguments": [1]},
        },
    ],
)
def test_malformed_jsonrpc_returns_an_error_without_raising(message):
    response = handle_jsonrpc_message(message)
    assert response["error"]["code"] in (-32600, -32602)


def test_stdio_survives_invalid_json_and_invalid_message_shapes():
    messages = '[]\nnot json\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n'
    result = subprocess.run(
        [sys.executable, "-m", "PyNCBI.cli", "mcp"],
        input=messages,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r.get("error", {}).get("code") for r in replies[:2]] == [-32600, -32700]
    assert replies[-1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_http_session_termination_and_origin_checks_cover_every_method():
    server = create_http_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
    try:
        with requests.Session() as client:
            init = client.post(
                url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}, timeout=10
            )
            headers = {"Mcp-Session-Id": init.headers["Mcp-Session-Id"]}
            ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
            assert client.post(url, json=ping, timeout=10).status_code == 400
            assert (
                client.post(
                    url, json=ping, headers={"Mcp-Session-Id": "unknown"}, timeout=10
                ).status_code
                == 404
            )
            for method in ("get", "delete"):
                assert (
                    getattr(client, method)(
                        url, headers={**headers, "Origin": "https://evil.example"}, timeout=10
                    ).status_code
                    == 403
                )
            assert client.delete(url, headers=headers, timeout=10).status_code == 204
            assert client.post(url, json=ping, headers=headers, timeout=10).status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert not _origin_is_local("http://0.0.0.0:8765")
    assert not _origin_is_local("file://localhost/")


def test_parallel_transfers_share_one_operation_budget(local_http, tmp_path):
    from PyNCBI.domains.geo import download_urls_parallel

    urls = [local_http.add(f"/file{i}.bin", b"x" * 700) for i in range(2)]
    with (
        NCBIClient() as client,
        PyNCBI.config_scope(Config(max_download_mb=0.001)),
        pytest.raises(PyNCBIError, match="max_download_mb"),
    ):
        download_urls_parallel(client, urls, tmp_path / "parallel", max_workers=2)
    assert sum(path.stat().st_size for path in (tmp_path / "parallel").glob("*.bin")) <= 1024


def test_distinct_urls_cannot_overwrite_the_same_download_name(local_http, tmp_path):
    from PyNCBI.domains.geo import download_urls_parallel

    urls = [local_http.add("/sample1/data.txt", b"A"), local_http.add("/sample2/data.txt", b"B")]
    with NCBIClient() as client, pytest.raises(PyNCBIError, match="colliding filenames"):
        download_urls_parallel(client, urls, tmp_path / "collision")
    assert not (tmp_path / "collision").exists()


def test_unknown_length_body_cannot_bypass_streaming_budget(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()  # Deliberately no Content-Length.
            self.wfile.write(b"x" * 16384)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/unknown.bin"
    try:
        with NCBIClient(app_config=Config(max_download_mb=0.001)) as client:
            with pytest.raises(PyNCBIError, match="max_download_mb"):
                client.get_bytes(url)
            with pytest.raises(PyNCBIError, match="max_download_mb"):
                client.download_file(url, tmp_path / "unknown.bin")
        assert not (tmp_path / "unknown.bin").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_genome_packages_obey_the_environment_budget(local_http, monkeypatch, tmp_path):
    monkeypatch.setenv("PYNCBI_MAX_DOWNLOAD_MB", "0.001")
    monkeypatch.setattr(Config, "NCBI_DATASETS_BASE_URL", local_http.base_url + "/datasets")
    reset_config()
    local_http.add(
        "/datasets/genome/accession/GCF_000005845.2/download_summary", "{}", "application/json"
    )
    local_http.add(
        "/datasets/genome/accession/GCF_000005845.2/download", b"x" * 4096, "application/zip"
    )
    with pytest.raises(PyNCBIError, match="max_download_mb"):
        PyNCBI.download_genome("GCF_000005845.2", tmp_path / "genome", unpack=False)
    assert not list((tmp_path / "genome").glob("*.zip"))


def test_overlapping_agent_calls_count_only_their_own_requests(local_http):
    from concurrent.futures import ThreadPoolExecutor

    from PyNCBI.core.formatter import begin_run

    barrier = threading.Barrier(2)
    urls = [local_http.add(f"/call{i}", "ok") for i in range(2)]

    def call(url):
        begin_run()
        with NCBIClient() as client:
            client.get_text(url)
        barrier.wait(timeout=10)
        return json.loads(render_output({}, mode="json"))["request_count"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(call, urls)) == [1, 1]
