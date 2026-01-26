import pytest


@pytest.fixture(autouse=True)
def _pyncbi_cache_dir(tmp_path, monkeypatch):
    cache_dir = tmp_path / "pyncbi_cache"
    monkeypatch.setenv("PYNCBI_CACHE_DIR", str(cache_dir))
    return cache_dir
