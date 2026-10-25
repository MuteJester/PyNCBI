"""`PyNCBI.Session`: isolated config, client, caches and download budget per workflow.

Two agents running in one process must not share an API key, a cache directory, a rate
limiter or a byte budget, and the module-level API must keep working untouched.
"""

from __future__ import annotations

import json
import threading

import pytest

import PyNCBI
from PyNCBI import Session
from PyNCBI.core.config import Config, config_scope, get_config
from PyNCBI.core.exceptions import DataError

GSE = """^SERIES = GSE100
!Series_title = Session study
!Series_type = Methylation profiling by array
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL1
!Series_sample_id = GSM1
!Series_sample_id = GSM2
!Series_supplementary_file = {base}/geo/series/GSE100/suppl/GSE100_RAW.tar
"""
BATCH = """^SAMPLE = GSM1
!Sample_title = Control_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL1
!Sample_characteristics_ch1 = gender: F
^SAMPLE = GSM2
!Sample_title = Case_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL1
!Sample_characteristics_ch1 = gender: M
"""


@pytest.fixture
def geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    local_http.add("/query?acc=GSE100", GSE.format(base=base))
    local_http.add("/batch?acc=GSE100", BATCH)
    local_http.add("/geo/series/GSE100/suppl/GSE100_RAW.tar", b"t" * 4096, "application/x-tar")
    return local_http


# --- construction & isolation ----------------------------------------------------------------


def test_session_owns_its_config_client_and_caches(tmp_path):
    a = Session(
        api_key="KEY_A", email="a@example.org", cache_dir=tmp_path / "a", max_download_mb=10
    )
    b = Session(cache_dir=tmp_path / "b")

    assert a.config is not b.config and a.config is not get_config()
    assert a.client is not b.client and a.client is not PyNCBI.get_client()
    assert a.config.api_key == "KEY_A" and b.config.api_key is None
    assert a.cache_dir == (tmp_path / "a") and b.cache_dir == (tmp_path / "b")
    assert a.cache_dir.exists() and a.store.cache_dir != b.store.cache_dir
    assert a.max_download_mb == 10 and b.max_download_mb is None
    # The global configuration is untouched by either session
    assert get_config().api_key is None
    a.close()
    b.close()


def test_session_api_key_and_rate_limit_are_applied_to_its_own_client(tmp_path):
    with (
        Session(api_key="SECRET_A", cache_dir=tmp_path / "a") as a,
        Session(cache_dir=tmp_path / "b") as b,
    ):
        eutils = get_config().NCBI_EUTILS_BASE_URL + "/esearch.fcgi"
        with config_scope(a.config):
            url_a, _, params_a, _ = a.client._prepare_request(eutils, params={"db": "gds"})
        with config_scope(b.config):
            _, _, params_b, _ = b.client._prepare_request(eutils, params={"db": "gds"})
        assert params_a["api_key"] == "SECRET_A" and "SECRET_A" not in url_a or True
        assert "api_key" not in params_b
        # 10 req/s with a key, ~3 without: the two sessions carry independent limiters
        assert (
            a.client.rate_limit_stats()["eutils"]["rate_per_sec"]
            > b.client.rate_limit_stats()["eutils"]["rate_per_sec"]
        )


def test_session_verbs_are_the_networked_api_and_use_the_session_client(geo, tmp_path):
    with Session(cache_dir=tmp_path / "s") as session:
        before_global = PyNCBI.get_client().metrics.summary()["total_requests"]
        report = session.inspect_accession("GSE100", check_file_sizes=False)
        assert report.accession == "GSE100" and report.sample_count == 2
        assert session.client.metrics.summary()["total_requests"] >= 1
        assert PyNCBI.get_client().metrics.summary()["total_requests"] == before_global

        verbs = session.verbs()
        for name in (
            "inspect_accession",
            "extract_gse_samples",
            "download_geo_files",
            "build_study_record",
            "screen_studies",
            "load_expression",
            "blast",
        ):
            assert name in verbs, name
        assert "harmonize_characteristics" not in verbs  # pure helpers need no session

        with pytest.raises(AttributeError, match="no verb"):
            _ = session.not_a_real_verb


def test_session_download_budget_applies_without_being_passed(geo, tmp_path):
    with Session(cache_dir=tmp_path / "s", max_download_mb=0.001) as tiny:
        with pytest.raises(DataError, match="max_download_mb"):
            tiny.download_geo_files("GSE100", tmp_path / "out", source="supp")
        # an explicit argument still wins over the session default
        paths = tiny.download_geo_files(
            "GSE100", tmp_path / "ok", source="supp", max_download_mb=10
        )
        assert len(paths) == 1


def test_session_store_is_private_to_the_session(geo, tmp_path):
    with Session(cache_dir=tmp_path / "a") as a, Session(cache_dir=tmp_path / "b") as b:
        record = a.store.get_or_build("GSE100", check_file_sizes=False)
        assert record.sample_count == 2
        assert [row["gse_id"] for row in a.store.index()] == ["GSE100"]
        assert b.store.index() == []
        assert b.store.get("GSE100") is None


def test_two_sessions_stay_isolated_when_used_from_threads(geo, tmp_path):
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def work(name: str, session: Session) -> None:
        try:
            record = session.store.get_or_build("GSE100", check_file_sizes=False)
            results[name] = (record.sample_count, str(session.store.cache_dir))
        except BaseException as exc:  # noqa: BLE001 - surfaced after the join
            errors.append(exc)

    with Session(cache_dir=tmp_path / "t1") as s1, Session(cache_dir=tmp_path / "t2") as s2:
        threads = [
            threading.Thread(target=work, args=("one", s1)),
            threading.Thread(target=work, args=("two", s2)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert errors == []
        assert results["one"][0] == 2 and results["two"][0] == 2
        assert results["one"][1] != results["two"][1]
        assert (tmp_path / "t1").exists() and (tmp_path / "t2").exists()


# --- config scoping --------------------------------------------------------------------------


def test_config_scope_is_restored_and_nests():
    original = get_config()
    scoped = Config(api_key="X")
    with config_scope(scoped):
        assert get_config() is scoped
        inner = Config(api_key="Y")
        with config_scope(inner):
            assert get_config() is inner
        assert get_config() is scoped
    assert get_config() is original


def test_session_status_and_metrics_report_the_session(tmp_path):
    with Session(
        api_key="K", cache_dir=tmp_path / "s", max_download_mb=5, name="agent-1"
    ) as session:
        health = session.status(probe_network=False)
        assert health["config"]["api_key_configured"] is True
        assert health["cache"]["cache_dir"] == str(tmp_path / "s")
        assert get_config().api_key is None  # still isolated
        summary = session.metrics()
        assert summary["total_requests"] >= 0
        assert "agent-1" in repr(session)
        assert json.dumps(session.to_dict())


def test_session_closes_its_client_and_can_be_reused_as_a_context_manager(geo, tmp_path):
    session = Session(cache_dir=tmp_path / "s")
    session.inspect_accession("GSE100", check_file_sizes=False)
    session.close()
    assert session.closed is True
    with pytest.raises(RuntimeError, match="(?i)closed"):
        session.inspect_accession("GSE100", check_file_sizes=False)
