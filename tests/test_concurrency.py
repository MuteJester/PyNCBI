"""Correctness under concurrency.

PyNCBI downloads cohorts in parallel, screens studies in parallel, and shares one
client (with one response cache, one rate limiter and one telemetry buffer) across
those threads. These tests hammer that shared state and check that nothing is lost,
duplicated or corrupted.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from PyNCBI.domains.geo import download_urls_parallel
from PyNCBI.io.http_client import NCBIClient, NCBIClientConfig

WORKERS = 8
FILES = 24


@pytest.fixture
def many_files(local_http):
    for index in range(FILES):
        local_http.add(
            f"/suppl/file_{index:03d}.txt",
            f"content-of-file-{index:03d}\n" * 64,
            "text/plain",
        )
    return local_http


@pytest.fixture
def client() -> NCBIClient:
    return NCBIClient(NCBIClientConfig(max_retries=0, timeout=15))


def test_parallel_downloads_write_every_file_exactly_once(many_files, client, tmp_path):
    urls = [many_files.url(f"/suppl/file_{i:03d}.txt") for i in range(FILES)]

    paths = download_urls_parallel(client, urls, tmp_path, max_workers=WORKERS)

    assert len(paths) == FILES
    assert len({p.name for p in paths}) == FILES, "a name collision would overwrite a file"
    for index, path in enumerate(sorted(paths, key=lambda p: p.name)):
        expected = f"content-of-file-{index:03d}\n" * 64
        assert path.read_text(encoding="utf-8") == expected, f"{path.name} is not intact"
    assert not list(tmp_path.glob("*.part")), "no partial files may survive a clean run"


def test_shared_client_counts_every_request_under_contention(many_files, client):
    """Telemetry is a shared counter; concurrent requests must not lose increments."""
    urls = [many_files.url(f"/suppl/file_{i:03d}.txt") for i in range(FILES)]
    client.metrics.reset()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        bodies = list(pool.map(client.get_text, urls))

    assert len(bodies) == FILES
    summary = client.metrics.summary(include_recent=0)
    assert summary["total_requests"] == FILES, summary


def test_the_response_cache_serves_one_body_to_many_threads(local_http, client):
    """Concurrent readers of the same URL must all get the same bytes, cached or not."""
    url = local_http.add("/geo/query/acc.cgi?acc=GSE1", "^SERIES = GSE1\n!Series_title = One\n")
    client.clear_response_cache()
    client.metrics.reset()

    barrier = threading.Barrier(WORKERS)

    def fetch() -> str:
        barrier.wait()  # maximise the overlap on the cache
        return client.get_text(url)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = [f.result() for f in [pool.submit(fetch) for _ in range(WORKERS)]]

    assert len(set(results)) == 1, "threads disagreed about the body of one URL"
    assert "GSE1" in results[0]
    summary = client.metrics.summary(include_recent=0)
    assert summary["total_requests"] + summary.get("cache_hits", 0) >= WORKERS


def test_two_sessions_never_share_a_cache_or_a_counter(local_http):
    """Sessions are the isolation boundary; concurrent use must not bleed across them."""
    import PyNCBI

    url_a = local_http.add("/geo/query/acc.cgi?acc=GSE2", "^SERIES = GSE2\n")
    url_b = local_http.add("/geo/query/acc.cgi?acc=GSE3", "^SERIES = GSE3\n")

    with PyNCBI.Session(name="a") as session_a, PyNCBI.Session(name="b") as session_b:
        errors: list[BaseException] = []

        def hammer(session, url: str, times: int) -> None:
            try:
                for _ in range(times):
                    session.client.get_text(url)
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [
            threading.Thread(target=hammer, args=(session_a, url_a, 5)),
            threading.Thread(target=hammer, args=(session_b, url_b, 7)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        metrics_a = session_a.metrics()
        metrics_b = session_b.metrics()
        assert metrics_a["total_requests"] + metrics_a.get("cache_hits", 0) == 5
        assert metrics_b["total_requests"] + metrics_b.get("cache_hits", 0) == 7


def test_the_audit_log_stays_parseable_when_many_threads_write(tmp_path):
    """Every audit line must be a complete JSON object, even under heavy concurrency.

    The log is what an operator reads to see what an unattended agent asked NCBI for.
    Interleaved partial writes would make it unparseable exactly when it matters.
    """
    import json

    from PyNCBI.io.telemetry import ClientMetrics, RequestEvent

    audit_path = tmp_path / "audit.jsonl"
    metrics = ClientMetrics(audit_path=audit_path)
    events = 60
    # A long URL makes a short line impossible, so any interleaving shows up.
    long_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?id=" + ",".join(
        str(n) for n in range(400)
    )

    def record(index: int) -> None:
        metrics.record(
            RequestEvent(
                method="GET",
                realm="eutils",
                url=f"{long_url}&seq={index}",
                status_code=200,
                elapsed_ms=1.0,
                rate_wait_ms=0.0,
                bytes_received=10,
            )
        )

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(record, range(events)))

    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == events, f"expected {events} audit lines, found {len(lines)}"
    for number, line in enumerate(lines, start=1):
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - the failure we are hunting
            pytest.fail(f"audit line {number} is not valid JSON ({exc}): {line[:120]}...")
    assert metrics.summary(include_recent=0)["total_requests"] == events
