from __future__ import annotations

import hashlib
import json
import sqlite3
import wave
from pathlib import Path

from ArmoredSpeech import model_processor
from ArmoredSpeech.contracts import AudioClass, SpeechDecision
from armored_core.storage import Storage
from ArmoredSpeech.prepare_v2_smoke_fixture import (
    create_real_video_smoke_fixture,
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


def test_real_video_smoke_fixture_uses_model_processor_and_only_mutates_cloned_db(tmp_path, monkeypatch):
    database, registered_original, registered_wav, _ = _fixture(tmp_path)
    source_video = tmp_path / "storage" / "Videos GRUPO_FONTE_teste" / "2" / "2_narração_pt.mp4"
    source_video.parent.mkdir(parents=True, exist_ok=True)
    source_video.write_bytes(b"byte-identical real-video stand-in")
    source_bytes = source_video.read_bytes()
    source_hash = _sha(source_video)
    registered_wav_hash = _sha(registered_wav)
    decision = SpeechDecision(
        audio_class=AudioClass.PT_BR_NARRATION,
        language="pt-br",
        speech_detected=True,
        music_detected=False,
        confidence=0.993,
        rationale="real model processor mocked for isolated unit test",
    )

    class FakeProcessor:
        def analyze(self, path):
            assert Path(path).is_file()
            assert Path(path) != source_video
            return decision

        def prepare_wav(self, video_path, wav_path, prepared_decision):
            assert prepared_decision is decision
            assert Path(video_path) != source_video
            _write_wav(Path(wav_path))

    monkeypatch.setattr(model_processor, "ModelProcessor", FakeProcessor)

    fixture = create_real_video_smoke_fixture(
        tmp_path,
        source_video=source_video,
        test_source_id="source_teste",
    )

    copied_original = Path(fixture["original_copy"])
    copied_wav = Path(fixture["wav_copy"])
    database_copy = Path(fixture["speech_database_copy"])
    manifest_path = Path(fixture["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert fixture["audio_class"] == "PT_BR_NARRATION"
    assert fixture["language"] == "pt-br"
    assert fixture["rvc_eligible"] == "true"
    assert fixture["music_detected"] == "false"
    assert fixture["source_database_modified"] == "false"
    assert source_video.read_bytes() == source_bytes
    assert _sha(source_video) == source_hash
    assert copied_original.read_bytes() == source_bytes
    assert _sha(copied_original) == source_hash
    assert copied_wav.is_file()
    assert copied_original.parent == copied_wav.parent
    assert copied_original.parent == source_video.parent
    assert copied_original.parent.parent == tmp_path / "storage" / "Videos GRUPO_FONTE_teste"
    assert fixture["test_item_id"] == "2"
    assert copied_original.name == "2_finallinkoriginal.mp4"
    assert copied_wav.name == "2_finallinkoriginal.wav"
    assert manifest["items"][0]["source_id"] == "source_teste"
    assert manifest["items"][0]["content_id"] == "source_teste_2"
    assert manifest["items"][0]["telegram_message_id"] == "2"
    assert manifest["items"][0]["workspace"] == str(source_video.parent)
    assert manifest["items"][0]["affiliate_url"].endswith("/finaldomeulinknovo")
    assert manifest["items"][0]["original_path"] == str(copied_original)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM armored_speech_results").fetchone()[0] == 1
        original_row = connection.execute(
            "SELECT video_path, wav_path FROM armored_speech_results WHERE source_id=? AND item_id=?",
            ("original-source", "telegram-123"),
        ).fetchone()
    assert original_row == (str(registered_original), str(registered_wav))
    assert _sha(registered_wav) == registered_wav_hash

    with sqlite3.connect(database_copy) as connection:
        real_row = connection.execute(
            """
            SELECT video_path, wav_path, audio_class, language, speech_detected,
                   music_detected, rvc_eligible, status, original_sha256_before,
                   original_sha256_after
            FROM armored_speech_results WHERE source_id=? AND item_id=?
            """,
            ("source_teste", fixture["test_item_id"]),
        ).fetchone()
    assert real_row == (
        str(copied_original), str(copied_wav), "PT_BR_NARRATION", "pt-br",
        1, 0, 1, "READY", source_hash, source_hash,
    )


def test_real_video_smoke_fixture_rejects_validation_fixtures(tmp_path):
    database, _, _, _ = _fixture(tmp_path)
    fixture_video = tmp_path / "storage" / "validation" / "synthetic.mp4"
    fixture_video.parent.mkdir(parents=True, exist_ok=True)
    fixture_video.write_bytes(b"fixture")
    try:
        create_real_video_smoke_fixture(
            tmp_path, source_video=fixture_video, test_source_id="source_teste"
        )
    except Exception as exc:
        assert "storage/validation" in str(exc)
    else:
        raise AssertionError("validation media must not be accepted as a real storage source")


def test_named_test_source_uses_original_source_workspace_pattern(tmp_path):
    storage = Storage(tmp_path)
    assert storage.source_workspace_root("source1") == tmp_path / "storage" / "Videos GRUPO_FONTE_1"
    assert storage.source_workspace_root("source_teste") == (
        tmp_path / "storage" / "Videos GRUPO_FONTE_teste"
    )


def test_real_video_smoke_fixture_allows_resolved_non_melody_route(tmp_path, monkeypatch):
    database, _, _, _ = _fixture(tmp_path)
    source_video = tmp_path / "storage" / "Videos GRUPO_FONTE_teste" / "1" / "1_ruido.mp4"
    source_video.parent.mkdir(parents=True, exist_ok=True)
    source_video.write_bytes(b"real-video stand-in for resolved noise route")
    source_hash = _sha(source_video)
    decision = SpeechDecision(
        audio_class=AudioClass.NOISE,
        language=None,
        speech_detected=False,
        music_detected=False,
        confidence=0.91,
        rationale="resolved noise route in isolated unit test",
    )

    class FakeProcessor:
        def analyze(self, path):
            assert Path(path).is_file()
            assert Path(path) != source_video
            return decision

        def prepare_wav(self, video_path, wav_path, prepared_decision):
            assert prepared_decision is decision
            _write_wav(Path(wav_path))

    monkeypatch.setattr(model_processor, "ModelProcessor", FakeProcessor)
    fixture = create_real_video_smoke_fixture(
        tmp_path, source_video=source_video, test_source_id="source_teste"
    )

    assert fixture["audio_class"] == "NOISE"
    assert fixture["rvc_eligible"] == "false"
    assert fixture["music_detected"] == "false"
    assert fixture["test_item_id"] == "1"
    assert Path(fixture["original_copy"]).name == "1_finallinkoriginal.mp4"
    assert Path(fixture["original_copy"]).parent == source_video.parent
    assert Path(fixture["original_copy"]).parent.parent == (
        tmp_path / "storage" / "Videos GRUPO_FONTE_teste"
    )
    assert _sha(source_video) == source_hash
    with sqlite3.connect(Path(fixture["speech_database"])) as connection:
        row = connection.execute(
            "SELECT audio_class, rvc_eligible, status FROM armored_speech_results"
        ).fetchall()
    assert row == [("NOISE", 0, "READY")]

