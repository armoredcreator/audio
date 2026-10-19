from __future__ import annotations

import hashlib
from pathlib import Path

from armored_core.integrity import sha256_file


def test_sha256_file_matches_digest_with_bounded_reads(
    tmp_path: Path, monkeypatch
) -> None:
    payload = b"ArmoredAudioLab-integrity-test-" * 120000
    target = tmp_path / "sample.mp4"
    target.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()

    original_open = Path.open
    requested_sizes: list[int] = []

    class TrackingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self.stream.__exit__(exc_type, exc, tb)

        def read(self, size=-1):
            requested_sizes.append(size)
            assert 0 < size <= 1024 * 1024
            return self.stream.read(size)

    def tracked_open(path, *args, **kwargs):
        return TrackingStream(original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", tracked_open)

    assert sha256_file(target) == expected
    assert len(requested_sizes) >= 2