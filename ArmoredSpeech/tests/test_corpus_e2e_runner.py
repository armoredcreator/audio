from __future__ import annotations

import json
from pathlib import Path

import pytest

from ArmoredSpeech.corpus_e2e_runner import (
    CorpusBatchError,
    discover_videos,
    run_corpus_batch,
)


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test mp4 bytes")
    return path


def test_discover_videos_counts_only_source_inputs(tmp_path):
    storage = tmp_path / "storage"
    first = _touch(storage / "sample_a.mp4")
    second = _touch(storage / "nested" / "sample_b.mp4")
    legitimate_underscore = _touch(storage / "legitimate_source_.mp4")
    _touch(storage / "validation" / "old-report" / "output.mp4")
    _touch(storage / "Videos GRUPO_FONTE_corpus14_old" / "001" / "original_001.mp4")
    _touch(storage / "Videos GRUPO_FONTE_teste" / "5" / "5_finaldomeulinknovo.mp4")
    _touch(storage / "Videos GRUPO_FONTE_teste" / "6" / "6_.mp4")

    found = discover_videos(tmp_path, storage, expected_count=3)

    assert set(found) == {first.resolve(), second.resolve(), legitimate_underscore.resolve()}
    assert (storage / "Videos GRUPO_FONTE_teste" / "6" / "6_.mp4").resolve() not in found


def test_discover_videos_fails_closed_on_unexpected_count(tmp_path):
    storage = tmp_path / "storage"
    _touch(storage / "sample_a.mp4")

    with pytest.raises(CorpusBatchError, match="Esperados exatamente 14 vídeos"):
        discover_videos(tmp_path, storage, expected_count=14)


def test_list_only_writes_an_explicit_input_manifest_without_processing(tmp_path):
    storage = tmp_path / "storage"
    first = _touch(storage / "sample_a.mp4")
    second = _touch(storage / "nested" / "sample_b.mp4")

    report = run_corpus_batch(
        root=tmp_path,
        video_root=Path("storage"),
        expected_count=2,
        batch_id="inventory-test",
        list_only=True,
    )

    input_manifest = Path(report["inputs_manifest"])
    assert report["status"] == "INPUTS_INVENTORIED"
    assert report["discovered_count"] == 2
    assert json.loads(input_manifest.read_text(encoding="utf-8")) == [
        second.relative_to(tmp_path).as_posix(),
        first.relative_to(tmp_path).as_posix(),
    ]
    assert not (storage / "database" / "armored_speech_corpus2_inventory-test.sqlite3").exists()
    assert not (storage / "Videos GRUPO_FONTE_corpus2_inventory-test").exists()
