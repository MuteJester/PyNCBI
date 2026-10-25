"""Short-lived HTTP response cache: deduplicate identical metadata requests within a workflow
without ever caching job/status endpoints, file bodies or streams."""

from __future__ import annotations

import json
import time

import pytest

from PyNCBI.core.config import Config, get_config, reset_config
from PyNCBI.io.http_client import NCBIClient
from PyNCBI.io.response_cache import ResponseCache

# --- the cache primitive ----------------------------------------------------------------------


def test_response_cache_stores_until_ttl_then_expires():
    cache = ResponseCache(max_entries=10, max_entry_bytes=1024)
    cache.set("k", "payload", ttl=5.0, size_bytes=7)
    assert cache.get("k") == "payload"
    assert cache.stats()["entries"] == 1 and cache.stats()["hits"] == 1

    cache.set("expiring", "x", ttl=0.0, size_bytes=1)
    assert cache.get("expiring") is None
    assert cache.stats()["misses"] >= 1


def test_response_cache_is_bounded_and_evicts_oldest():
    cache = ResponseCache(max_entries=3, max_entry_bytes=1024)
    for i in range(5):
        cache.set(f"k{i}", i, ttl=60.0, size_bytes=1)
    assert cache.stats()["entries"] == 3
    assert cache.get("k0") is None and cache.get("k4") == 4
    assert cache.stats()["evictions"] >= 2

    cache.set("huge", "x", ttl=60.0, size_bytes=99999)  # refused, too big to keep
    assert cache.get("huge") is None

    cache.clear()
    assert cache.stats()["entries"] == 0


# --- what the client caches ---------------------------------------------------------------------


@pytest.fixture
def api(local_http, monkeypatch):
    """A stand-in for the NCBI endpoints the client is allowed to cache."""
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_BASE_URL", base + "/geo/query/acc.cgi")
    monkeypatch.setattr(
        Config,
        "NCBI_QUERY_URL_TEMPLATE",
        base + "/geo/query/acc.cgi?acc={accession}&targ=self&form=text",
    )
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/entrez/eutils")
    monkeypatch.setattr(Config, "NCBI_BLAST_URL", base + "/Blast.cgi")
    counter = {"n": 0}

    def changing(_query: dict[str, str]):
        counter["n"] += 1
        return f"call {counter['n']}".encode(), "text/plain"

    local_http.add_dynamic("/geo/query/acc.cgi", changing)
    local_http.add_dynamic("/Blast.cgi", changing)
    local_http.add_dynamic(
        "/entrez/eutils/esearch.fcgi",
        lambda q: (json.dumps({"n": counter["n"]}).encode(), "application/json"),
    )
    local_http.add_dynamic("/geo/download/", changing)
    local_http.add("/big.txt", "line1\nline2\n")
    local_http.counter = counter  # type: ignore[attr-defined]
    return local_http


def _acc_url(base: str, acc: str) -> str:
    return f"{base}/geo/query/acc.cgi?acc={acc}&targ=self&form=text"


def test_identical_metadata_request_is_served_from_cache(api):
    client = NCBIClient()
    url = _acc_url(api.base_url, "GSE1")
    first = client.get_text(url)
    second = client.get_text(url)
    assert first == second == "call 1"
    assert sum(1 for h in api.hits if h.startswith("/geo/query")) == 1
    summary = client.metrics.summary()
    assert summary["cache_hits"] == 1 and summary["total_requests"] == 1
    assert summary["cache"]["entries"] == 1


def test_different_urls_do_not_collide_and_ttl_expiry_refetches(api, monkeypatch):
    client = NCBIClient()
    assert client.get_text(_acc_url(api.base_url, "GSE1")) == "call 1"
    assert client.get_text(_acc_url(api.base_url, "GSE2")) == "call 2"
    assert client.get_text(_acc_url(api.base_url, "GSE1")) == "call 1"

    monkeypatch.setenv("PYNCBI_HTTP_CACHE_TTL", "0.05")
    reset_config()
    fresh = NCBIClient()
    assert fresh.get_text(_acc_url(api.base_url, "GSE3")) == "call 3"
    time.sleep(0.08)
    assert fresh.get_text(_acc_url(api.base_url, "GSE3")) == "call 4"
    reset_config()


def test_the_cache_key_ignores_the_api_key(api, monkeypatch):
    client = NCBIClient()
    url = _acc_url(api.base_url, "GSE1")
    assert client.get_text(url) == "call 1"
    assert client.get_text(url + "&api_key=ROTATED") == "call 1"
    assert client.metrics.summary()["cache_hits"] == 1


@pytest.mark.parametrize(
    "path",
    ["/Blast.cgi?CMD=Get&RID=X&FORMAT_OBJECT=SearchInfo", "/geo/download/?acc=GSE1&format=file"],
)
def test_job_status_and_file_endpoints_are_never_cached(api, path):
    client = NCBIClient()
    url = api.base_url + path
    assert client.get_text(url) != client.get_text(url)  # a poll must always see fresh state
    assert client.metrics.summary()["cache_hits"] == 0


def test_streams_ranges_and_downloads_are_never_cached(api, tmp_path):
    client = NCBIClient()
    url = api.base_url + "/big.txt"
    assert list(client.stream_lines(url)) == ["line1", "line2"]
    assert list(client.stream_lines(url)) == ["line1", "line2"]
    client.get_range_bytes(url, max_bytes=4)
    client.download_file(url, tmp_path / "f.txt")
    assert client.metrics.summary()["cache_hits"] == 0
    assert sum(1 for h in api.hits if h.startswith("/big.txt")) == 4


def test_cache_hits_are_not_written_to_the_audit_log(api, tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("PYNCBI_AUDIT_LOG", str(audit))
    reset_config()
    client = NCBIClient()
    url = _acc_url(api.base_url, "GSE9")
    client.get_text(url)
    client.get_text(url)
    lines = [ln for ln in audit.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1  # the audit records what was actually sent to NCBI
    reset_config()


def test_cache_can_be_disabled_and_cleared(api, monkeypatch):
    monkeypatch.setenv("PYNCBI_HTTP_CACHE_TTL", "0")
    reset_config()
    assert get_config().http_cache_ttl == 0
    off = NCBIClient()
    url = _acc_url(api.base_url, "GSE5")
    assert off.get_text(url) != off.get_text(url)
    assert off.metrics.summary()["cache_hits"] == 0

    monkeypatch.delenv("PYNCBI_HTTP_CACHE_TTL")
    reset_config()
    on = NCBIClient()
    assert on.get_text(url) == on.get_text(url)
    on.clear_response_cache()
    assert on.metrics.summary()["cache"]["entries"] == 0


def test_sessions_do_not_share_a_response_cache(api, tmp_path):
    from PyNCBI import Session

    with Session(cache_dir=tmp_path / "a") as a, Session(cache_dir=tmp_path / "b") as b:
        url = _acc_url(api.base_url, "GSE7")
        assert a.client.get_text(url) == "call 1"
        assert b.client.get_text(url) == "call 2"  # b has its own cache, so it really fetches
        assert a.client.get_text(url) == "call 1"
        assert a.client.metrics.summary()["cache_hits"] == 1
        assert b.client.metrics.summary()["cache_hits"] == 0


def test_a_multi_step_workflow_stops_refetching_the_same_record(local_http, monkeypatch):
    """The motivating case: inspect -> metadata used to re-fetch the same SOFT record twice.

    Uses the real NCBI path shape (`/geo/query/acc.cgi` with different `targ` values), because
    cacheability is decided from the URL: metadata endpoints yes, job and file endpoints no.
    """
    import PyNCBI

    base = local_http.base_url
    acc_cgi = base + "/geo/query/acc.cgi"
    monkeypatch.setattr(
        Config,
        "NCBI_QUERY_URL_TEMPLATE",
        acc_cgi + "?acc={accession}&targ=self&form=text&view=quick",
    )
    monkeypatch.setattr(
        Config,
        "NCBI_BATCH_GSM_URL_TEMPLATE",
        acc_cgi + "?acc={accession}&targ=gsm&form=text&view=brief",
    )

    series = (
        "^SERIES = GSE100\n!Series_title = t\n!Series_type = Methylation profiling by array\n"
        "!Series_sample_organism = Homo sapiens\n!Series_platform_id = GPL1\n!Series_sample_id = GSM1\n"
    )
    samples = "^SAMPLE = GSM1\n!Sample_title = s\n!Sample_series_id = GSE100\n!Sample_characteristics_ch1 = gender: F\n"
    calls = {"self": 0, "gsm": 0}

    def acc_route(query: dict[str, str]):
        targ = query.get("targ", "self")
        calls[targ] = calls.get(targ, 0) + 1
        return (samples if targ == "gsm" else series).encode(), "geo/text"

    local_http.add_dynamic("/geo/query/acc.cgi", acc_route)

    client = NCBIClient()
    PyNCBI.inspect_accession("GSE100", check_file_sizes=False, http_client=client)
    PyNCBI.build_study_record("GSE100", check_file_sizes=False, http_client=client)

    # Without the cache this costs 2 series fetches and 2 batch fetches
    assert calls["self"] == 1 and calls["gsm"] == 1
    assert client.metrics.summary()["cache_hits"] >= 2
    assert client.metrics.summary()["total_requests"] == 2


def test_two_long_queries_promoted_to_post_do_not_share_a_cache_entry(local_http, monkeypatch):
    """Long E-Utilities requests are sent as POST, with the query moved into the body.

    The cache key is built before that promotion, from the full URL, so the two requests
    stay distinct. If the key were ever derived from the post-promotion URL they would
    collide and the second caller would silently receive the first caller's records.
    """
    from PyNCBI.core.config import Config

    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/entrez/eutils")

    def echo(query: dict) -> tuple[bytes, str]:
        return (f"ids={query.get('id', '')[:24]}".encode(), "text/plain")

    local_http.add_dynamic("/entrez/eutils/esummary.fcgi", echo)

    client = NCBIClient()
    client.clear_response_cache()
    long_a = ",".join(str(n) for n in range(1000, 1600))  # well past the POST threshold
    long_b = ",".join(str(n) for n in range(5000, 5600))
    url = f"{base}/entrez/eutils/esummary.fcgi"

    first = client.get_text(url, params={"db": "gene", "id": long_a})
    second = client.get_text(url, params={"db": "gene", "id": long_b})
    repeat = client.get_text(url, params={"db": "gene", "id": long_a})

    assert first != second, "two different id lists must not share a cached response"
    assert first.startswith("ids=1000")
    assert second.startswith("ids=5000")
    assert repeat == first, "the repeat of the first query should hit the cache"
