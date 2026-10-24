import hashlib
import io
import tarfile

import pytest

from scripts import build_rvc311_wheel as recipe


def source_archive(tmp_path, monkeypatch, *, name="rvc_python-0.1.5/pyproject.toml", link=False):
    archive = tmp_path / "source.tar.gz"
    text = '\n'.join(recipe.REPLACEMENTS).encode()
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo(name)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = "outside"
            tar.addfile(member)
        else:
            member.size = len(text)
            tar.addfile(member, io.BytesIO(text))
    monkeypatch.setattr(recipe, "UPSTREAM_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    return archive


def test_fork_changes_build_recipe_explicitly(tmp_path, monkeypatch):
    archive = source_archive(tmp_path, monkeypatch)
    root = recipe.prepare_source(archive, tmp_path / "build")
    patched = (root / "pyproject.toml").read_text()
    assert 'version = "0.1.5+armored311.1"' in patched
    assert '"fairseq==0.12.3.1"' in patched
    assert '"omegaconf==2.3.0"' in patched
    assert '"hydra-core==1.3.2"' in patched
    assert (root / "ARMORED_BUILD.json").is_file()


def test_wrong_source_digest_never_extracts(tmp_path, monkeypatch):
    archive = source_archive(tmp_path, monkeypatch)
    monkeypatch.setattr(recipe, "UPSTREAM_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="digest"):
        recipe.prepare_source(archive, tmp_path / "build")
    assert not (tmp_path / "build").exists()


@pytest.mark.parametrize("name,link", [("../outside", False), ("link", True)])
def test_non_regular_or_escaping_archive_rejected(tmp_path, monkeypatch, name, link):
    archive = source_archive(tmp_path, monkeypatch, name=name, link=link)
    with pytest.raises(ValueError):
        recipe.prepare_source(archive, tmp_path / "build")
    assert not (tmp_path / "outside").exists()


def test_existing_build_source_is_preserved(tmp_path, monkeypatch):
    archive = source_archive(tmp_path, monkeypatch)
    destination = tmp_path / "build"
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_bytes(b"preserved")
    with pytest.raises(FileExistsError):
        recipe.prepare_source(archive, destination)
    assert sentinel.read_bytes() == b"preserved"
