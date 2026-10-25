"""A successful upload must match the tested artifacts, including on a retried release job."""

import hashlib

import pytest

from scripts_support import import_script


def test_published_release_requires_matching_wheel_and_source_bytes(tmp_path):
    module = import_script("verify_pypi_release")
    files = {
        "pyncbi-1.0.0-py3-none-any.whl": b"tested wheel",
        "pyncbi-1.0.0.tar.gz": b"tested source",
    }
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    metadata = {
        "info": {"version": "1.0.0"},
        "urls": [
            {"filename": name, "digests": {"sha256": hashlib.sha256(data).hexdigest()}}
            for name, data in files.items()
        ],
    }
    module.check_published_files(tmp_path, metadata, "1.0.0")
    metadata["urls"][0]["digests"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA256 differs"):
        module.check_published_files(tmp_path, metadata, "1.0.0")
    metadata["urls"] = []
    with pytest.raises(ValueError, match="has not listed"):
        module.check_published_files(tmp_path, metadata, "1.0.0")


def test_published_release_rejects_wrong_version_or_missing_artifacts(tmp_path):
    module = import_script("verify_pypi_release")
    with pytest.raises(ValueError, match="does not identify"):
        module.check_published_files(tmp_path, {"info": {"version": "0.2.0"}}, "1.0.0")
    with pytest.raises(ValueError, match="both a wheel and a source"):
        module.check_published_files(tmp_path, {"info": {"version": "1.0.0"}}, "1.0.0")
