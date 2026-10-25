import io
import tarfile

import pytest

from native_support import cell, data_fields


def test_parse_characteristics_handles_colons():
    from PyNCBI.parsing import parse_characteristics

    data = ["key: value: extra\nno_colon\nother: 1"]
    df = parse_characteristics(data)

    assert cell(df, "0", "key") == "value: extra"
    assert cell(df, "0", "other") == "1"
    assert "no_colon" not in data_fields(df)


def test_extract_tarfile_blocks_traversal(tmp_path):
    from PyNCBI.io.compression import extract_tarfile

    tar_path = tmp_path / "bad.tar"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    data = b"nope"
    with tarfile.open(tar_path, "w") as tar:
        info = tarfile.TarInfo(name="../evil.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with pytest.raises(Exception, match="(?i)path traversal"):
        extract_tarfile(str(tar_path), str(out_dir))


def test_extract_tarfile_blocks_symlink(tmp_path):
    from PyNCBI.io.compression import extract_tarfile

    tar_path = tmp_path / "bad_link.tar"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with tarfile.open(tar_path, "w") as tar:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../outside.txt"
        tar.addfile(info)

    with pytest.raises(Exception, match="symlink|hardlink"):
        extract_tarfile(str(tar_path), str(out_dir))
