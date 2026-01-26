import io
import tarfile

import pytest


def test_parse_characteristics_handles_colons():
    from PyNCBI.Utilities import parse_characteristics

    data = ["key: value: extra\nno_colon\nother: 1"]
    df = parse_characteristics(data)

    assert df.loc[0, "key"] == "value: extra"
    assert df.loc[0, "other"] == "1"
    assert "no_colon" not in df.columns


def test_unzip_tarfile_blocks_traversal(tmp_path):
    from PyNCBI.Utilities import unzip_tarfile

    tar_path = tmp_path / "bad.tar"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    data = b"nope"
    with tarfile.open(tar_path, "w") as tar:
        info = tarfile.TarInfo(name="../evil.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with pytest.raises(Exception, match="(?i)path traversal"):
        unzip_tarfile(str(tar_path), str(out_dir))


def test_unzip_tarfile_blocks_symlink(tmp_path):
    from PyNCBI.Utilities import unzip_tarfile

    tar_path = tmp_path / "bad_link.tar"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with tarfile.open(tar_path, "w") as tar:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../outside.txt"
        tar.addfile(info)

    with pytest.raises(Exception, match="symlink|hardlink"):
        unzip_tarfile(str(tar_path), str(out_dir))
