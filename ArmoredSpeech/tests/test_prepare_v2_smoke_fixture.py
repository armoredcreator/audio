from __future__ import annotations

import hashlib
import json
import sqlite3
import wave
from pathlib import Path

from ArmoredSpeech.prepare_v2_smoke_fixture import (
    create_smoke_fixture,
    list_ready_candidates,
)


SCHEMA = """
CREATE TABLE armored_speech_results (
    source_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    video_path TEXT NOT NULL,
    wav_path TEXT,
    audio_class TEXT NOT NULL,
    language TEXT,
    speech_detected INTEGER NOT NULL,
    music_detected INTEGER,
    confidence REAL,
    rvc_eligible INTEGER NOT NULL,
    status TEXT NOT NULL,
    original_sha256_before TEXT,
    original_sha256_after TEXT,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT 'test',
    PRIMARY KEY (source_id, item_id)
)
"""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(16000 * 2))


def _fixture(root: Path):
    original = root / "original.mp4"
    original.write_bytes(b"synthetic but byte-stable MP4 fixture")
    wav_path = root / "original.wav"
    _write_wav(wav_path)
    digest = _sha(original)

    database = root / "storage" / "database" / "speech_experimental.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(SCHEMA)
        connection.execute(
            """
            INSERT INTO armored_speech_results (
                source_id, item_id, video_path, wav_path, audio_class, language,
                speech_detected, music_detected, confidence, rvc_eligible, status,
                original_sha256_before, original_sha256_after, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                "original-source", "telegram-123", str(original), str(wav_path),
                "PT_BR_NARRATION", "pt-br", 1, 0, 0.99, 1, "READY", digest, digest,
            ),
        )
    return database, original, wav_path, digest


def test_list_ready_candidates_requires_clean_ready_melody_route(tmp_path):
    database, original, wav_path, digest = _fixture(tmp_path)

    candidates = list_ready_candidates(tmp_path, database)

    assert len(candidates) == 1
    assert candidates[0]["source_id"] == "original-source"
    assert candidates[0]["item_id"] == "telegram-123"
    assert Path(candidates[0]["original_path"]) == original
    assert Path(candidates[0]["wav_path"]) == wav_path
    assert _sha(original) == digest


def test_smoke_fixture_clones_database_and_media_without_mutating_sources(tmp_path):
    database, original, wav_path, digest = _fixture(tmp_path)
    original_bytes = original.read_bytes()
    wav_hash = _sha(wav_path)

    fixture = create_smoke_fixture(
        tmp_path,
        database,
        source_id="original-source",
        item_id="telegram-123",
    )

    manifest_path = Path(fixture["manifest_path"])
    database_copy = Path(fixture["speech_database_copy"])
    copied_original = Path(fixture["original_copy"])
    copied_wav = Path(fixture["wav_copy"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["phase_status"] == "COMPLETE"
    assert manifest["approved"] is True
    assert manifest["allow_copy_only"] is False
    assert manifest["expected_item_count"] == 1
    item = manifest["items"][0]
    assert item["source_id"] == "source1"
    assert item["telegram_message_id"] == fixture["test_item_id"]
    assert Path(item["workspace"]) == copied_original.parent
    assert Path(item["original_path"]) == copied_original
    assert item["original_sha256"] == digest
    assert copied_original.read_bytes() == original_bytes
    assert _sha(copied_original) == digest
    assert _sha(copied_wav) == wav_hash
    assert copied_original.stem == copied_wav.stem
    assert database_copy.is_file()

    # The source experimental registry remains one row and points at the same
    # source files; only the disposable cloned registry contains the test row.
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM armored_speech_results"
        ).fetchone()[0] == 1
        row = connection.execute(
            "SELECT video_path, wav_path FROM armored_speech_results WHERE source_id=? AND item_id=?",
            ("original-source", "telegram-123"),
        ).fetchone()
    assert row == (str(original), str(wav_path))

    with sqlite3.connect(database_copy) as connection:
        cloned = connection.execute(
            """
            SELECT source_id, item_id, video_path, wav_path, status,
                   original_sha256_before, original_sha256_after
            FROM armored_speech_results WHERE source_id=? AND item_id=?
            """,
            ("source1", fixture["test_item_id"]),
        ).fetchone()
    assert cloned == (
        "source1",
        fixture["test_item_id"],
        str(copied_original),
        str(copied_wav),
        "READY",
        digest,
        digest,
    )
