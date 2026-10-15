from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("validate_real_audio.py")
SPEC = importlib.util.spec_from_file_location("validate_real_audio_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def test_resolves_video_nested_under_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(VALIDATOR, "ROOT", tmp_path)
    video = tmp_path / "storage" / "corpus" / "nested" / "291_6VKD4FrQVJ.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"disposable test fixture")

    assert VALIDATOR.resolve_video(video.name) == video.resolve()


def test_rejects_ambiguous_duplicate_video(tmp_path, monkeypatch):
    monkeypatch.setattr(VALIDATOR, "ROOT", tmp_path)
    root_video = tmp_path / "291_6VKD4FrQVJ.mp4"
    stored_video = tmp_path / "storage" / "291_6VKD4FrQVJ.mp4"
    stored_video.parent.mkdir()
    root_video.write_bytes(b"root fixture")
    stored_video.write_bytes(b"storage fixture")

    with pytest.raises(FileNotFoundError, match="encontrados 2"):
        VALIDATOR.resolve_video(root_video.name)


def test_reports_missing_video_without_hashing_or_processing(tmp_path, monkeypatch):
    monkeypatch.setattr(VALIDATOR, "ROOT", tmp_path)

    with pytest.raises(FileNotFoundError, match="nenhum arquivo"):
        VALIDATOR.resolve_video("291_6VKD4FrQVJ.mp4")
