from __future__ import annotations

import pytest

from ArmoredSpeech.corpus_e2e_runner import CorpusBatchError, discover_videos


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test mp4 bytes")
    return path


def test_discover_videos_counts_only_source_inputs(tmp_path):
    storage = tmp_path / "storage"
    first = _touch(storage / "sample_a.mp4")
    second = _touch(storage / "nested" / "sample_b.mp4")
    _touch(storage / "validation" / "old-report" / "output.mp4")
    _touch(storage / "Videos GRUPO_FONTE_corpus14_old" / "001" / "original_001.mp4")
    _touch(storage / "Videos GRUPO_FONTE_teste" / "5" / "5_finaldomeulinknovo.mp4")
    _touch(storage / "Videos GRUPO_FONTE_teste" / "6" / "6_.mp4")

    found = discover_videos(tmp_path, storage, expected_count=2)

    assert found == sorted([first.resolve(), second.resolve()], key=lambda p: str(p).casefold())


def test_discover_videos_fails_closed_on_unexpected_count(tmp_path):
    storage = tmp_path / "storage"
    _touch(storage / "sample_a.mp4")

    with pytest.raises(CorpusBatchError, match="Esperados exatamente 14 vídeos"):
        discover_videos(tmp_path, storage, expected_count=14)
