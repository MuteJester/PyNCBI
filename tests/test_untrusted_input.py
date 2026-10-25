"""Hostile and malformed input must be refused, not executed.

Everything here arrives from a remote server or from a string an agent composed:
tar archives from GEO submitters, and `--where` filter expressions. Both are places
where a careless implementation hands control to the input.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from native_support import fixture_table, table_empty
from PyNCBI.core.exceptions import PyNCBIError
from PyNCBI.domains.geo.samples import _apply_sample_filter
from PyNCBI.io.compression import extract_tarfile


def _tar_with(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> io.BytesIO:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for info, payload in members:
            archive.addfile(info, io.BytesIO(payload) if payload is not None else None)
    buffer.seek(0)
    return buffer


def test_a_tar_member_that_escapes_the_target_directory_is_refused(tmp_path):
    info = tarfile.TarInfo("../../escaped.txt")
    info.size = 5
    archive_path = tmp_path / "evil.tar"
    archive_path.write_bytes(_tar_with([(info, b"pwned")]).getvalue())

    destination = tmp_path / "out"
    with pytest.raises(PyNCBIError):
        extract_tarfile(archive_path, destination)

    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_an_absolute_tar_member_is_refused(tmp_path):
    info = tarfile.TarInfo("/tmp/pyncbi_absolute_escape.txt")
    info.size = 5
    archive_path = tmp_path / "absolute.tar"
    archive_path.write_bytes(_tar_with([(info, b"pwned")]).getvalue())

    with pytest.raises(PyNCBIError):
        extract_tarfile(archive_path, tmp_path / "out")


def test_a_symlink_member_is_refused(tmp_path):
    info = tarfile.TarInfo("link")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    archive_path = tmp_path / "link.tar"
    archive_path.write_bytes(_tar_with([(info, None)]).getvalue())

    destination = tmp_path / "out"
    with pytest.raises(PyNCBIError):
        extract_tarfile(archive_path, destination)
    assert not (destination / "link").exists()


def test_a_well_formed_archive_still_extracts(tmp_path):
    info = tarfile.TarInfo("inner/data.txt")
    payload = b"probe,value\ncg01,0.5\n"
    info.size = len(payload)
    archive_path = tmp_path / "good.tar"
    archive_path.write_bytes(_tar_with([(info, payload)]).getvalue())

    destination = tmp_path / "out"
    extract_tarfile(archive_path, destination)
    assert (destination / "inner" / "data.txt").read_bytes() == payload


def test_a_zip_member_that_escapes_is_refused(tmp_path):
    from PyNCBI.io.compression import extract_zipfile

    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../../escaped.txt", "pwned")

    with pytest.raises(PyNCBIError):
        extract_zipfile(archive_path, tmp_path / "out")
    assert not (tmp_path.parent / "escaped.txt").exists()


# --- filter expressions -----------------------------------------------------------------

SAMPLES = fixture_table(
    {"title": ["S1", "S2"], "age": ["62", "34"], "severity": ["Severe", "Mild"]},
    index=["GSM1", "GSM2"],
)


@pytest.mark.parametrize(
    "expression",
    [
        "@__import__('os').system('touch /tmp/pyncbi_pwned')",
        "severity == 'Severe' and @open('/etc/passwd').read()",
        "__import__('subprocess').run(['echo'])",
    ],
)
def test_a_filter_expression_cannot_reach_the_interpreter(expression):
    """`--where` is a data filter. It must never execute arbitrary Python."""
    import pathlib

    marker = pathlib.Path("/tmp/pyncbi_pwned")
    marker.unlink(missing_ok=True)
    try:
        result = _apply_sample_filter(SAMPLES, expression)
    except Exception as exc:  # noqa: BLE001 - any refusal is acceptable
        assert not isinstance(exc, SystemExit)
    else:
        # A refusal may also look like "nothing matched"; what matters is no side effect.
        assert isinstance(result, dict)
    assert not marker.exists(), "the filter expression reached the interpreter"


def test_an_unparseable_filter_is_an_error_not_a_silent_empty_table():
    """A filter the user got wrong must say so, not quietly return nothing."""
    try:
        result = _apply_sample_filter(SAMPLES, "severity === 'Severe'")
    except Exception as exc:  # noqa: BLE001
        assert "severity" in str(exc) or "filter" in str(exc).lower()
    else:
        assert not table_empty(result), "a malformed filter silently matched nothing"
