"""Loopback NCBI fixture for deterministic client acceptance checks; never production data."""

from __future__ import annotations

import contextlib
import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextlib.contextmanager
def ncbi_fixture() -> Iterator[tuple[dict[str, str], list[tuple[str, str]]]]:
    hits: list[tuple[str, str]] = []
    raw = b"id\tGSM1\tGSM2\n0001\t9007199254740993\t7\nNA\t0.12345678901234567\tNA\n"
    matrix = gzip.compress(b"!series_matrix_table_begin\n" + raw + b"!series_matrix_table_end\n")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def reply(self, *, head: bool) -> None:
            hits.append((self.command, self.path))
            parsed = urlsplit(self.path)
            accession = parse_qs(parsed.query).get("acc", [""])[0]
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            body: bytes | None = None
            if parsed.path == "/query" and accession == "GSE100":
                body = (
                    "^SERIES = GSE100\n!Series_title = MCP acceptance fixture\n"
                    "!Series_type = Expression profiling by array\n"
                    "!Series_sample_organism = Homo sapiens\n!Series_platform_id = GPL1\n"
                    "!Series_sample_id = GSM1\n!Series_sample_id = GSM2\n"
                    f"!Series_supplementary_file = {base}/supp/raw.tsv\n"
                ).encode()
            elif parsed.path == "/batch" and accession == "GSE100":
                body = "".join(
                    f"^SAMPLE = {gsm}\n!Sample_title = {group} sample\n"
                    "!Sample_series_id = GSE100\n!Sample_platform_id = GPL1\n"
                    "!Sample_organism_ch1 = Homo sapiens\n"
                    f"!Sample_characteristics_ch1 = group: {group}\n"
                    for gsm, group in (("GSM1", "case"), ("GSM2", "control"))
                ).encode()
            elif parsed.path.endswith("GSE100_series_matrix.txt.gz"):
                body = matrix
            elif parsed.path == "/supp/raw.tsv":
                body = raw
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            if not head and body:
                self.wfile.write(body)

        def do_GET(self) -> None:
            self.reply(head=False)

        def do_HEAD(self) -> None:
            self.reply(head=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    endpoints = {
        "NCBI_QUERY_URL_TEMPLATE": base + "/query?acc={accession}",
        "NCBI_BATCH_GSM_URL_TEMPLATE": base + "/batch?acc={accession}",
        "NCBI_DATA_URL_TEMPLATE": base + "/data?acc={accession}",
        "NCBI_FTP_HTTPS_BASE": base,
        "NCBI_GEO_DOWNLOAD_URL": base + "/download",
        "NCBI_EUTILS_BASE_URL": base + "/eutils",
        "NCBI_DATASETS_BASE_URL": base + "/datasets",
        "NCBI_BLAST_URL": base + "/blast",
        "ENA_PORTAL_API_URL": base + "/ena",
    }
    try:
        yield endpoints, hits
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
