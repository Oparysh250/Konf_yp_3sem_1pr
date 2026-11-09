"""Тесты парсера и VFS."""

import zipfile

import pytest

from src.main import Vfs, VfsError, parse_line


def make_zip(tmp_path, files):
    path = tmp_path / "vfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return str(path)


def test_parse_simple():
    assert parse_line("ls -l") == ("ls", ["-l"])


def test_parse_quotes():
    assert parse_line('echo "a b"') == ("echo", ["a b"])


def test_load_and_list(tmp_path):
    path = make_zip(tmp_path, {"dir/a.txt": b"hello"})
    vfs = Vfs.from_zip(path)
    vfs.change_dir("dir")
    assert [n.name for n in vfs.listdir(".")] == ["a.txt"]
    assert vfs.read_text("a.txt") == "hello"


def test_missing(tmp_path):
    path = make_zip(tmp_path, {})
    vfs = Vfs.from_zip(path)
    with pytest.raises(VfsError):
        vfs.resolve("nope")


def test_size(tmp_path):
    path = make_zip(tmp_path, {"x/y.txt": b"12345"})
    vfs = Vfs.from_zip(path)
    assert vfs.size_of("x") == 5
