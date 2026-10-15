from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ArmoredSpeech.studio_bridge import (
    DecisionNotFound,
    DecisionNotReady,
    IncompatibleDecision,
    read_studio_directive,
)


def _registry(tmp_path: Path, *, audio_class="PT_BR_NARRATION", language="pt",
              speech_detected=1, rvc_eligible=1, status="READY", wav=True):
    db = tmp_path / "speech.sqlite3"
    original = tmp_path / "original.mp4"
    original.write_bytes(b"immutable-original")
    wav_path = tmp_path / "original.wav" if wav else None
    if wav_path:
        wav_path.write_bytes(b"derived-wav")
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE armored_speech_results (
            source_id TEXT NOT NULL, item_id TEXT NOT NULL, video_path TEXT NOT NULL,
            wav_path TEXT, audio_class TEXT NOT NULL, language TEXT,
            speech_detected INTEGER NOT NULL, music_detected INTEGER, confidence REAL,
            rvc_eligible INTEGER NOT NULL, status TEXT NOT NULL,
            original_sha256_before TEXT, original_sha256_after TEXT, error TEXT,
            updated_at TEXT NOT NULL DEFAULT 'test',
            PRIMARY KEY (source_id, item_id)
        )
    """)
    con.execute("""
        INSERT INTO armored_speech_results
        (source_id,item_id,video_path,wav_path,audio_class,language,speech_detected,
         music_detected,confidence,rvc_eligible,status,error)
        VALUES ('s1','i1',?,?,?,?,?,0,0.99,?,?,NULL)
    """, (str(original), str(wav_path) if wav_path else None, audio_class, language,
          speech_detected, rvc_eligible, status))
    con.commit()
    con.close()
    return db, original


def test_ready_pt_br_narration_can_be_rvc_eligible(tmp_path):
    db, original = _registry(tmp_path)
    directive = read_studio_directive(db, source_id="s1", item_id="i1")
    assert directive.original_path == original
    assert directive.studio_audio_mode == "SPEECH"
    assert directive.rvc_eligible is True
    assert original.read_bytes() == b"immutable-original"


def test_mixed_audio_is_blocked_until_separation_is_validated(tmp_path):
    db, _ = _registry(tmp_path, audio_class="PT_BR_NARRATION_WITH_MUSIC")
    directive = read_studio_directive(db, source_id="s1", item_id="i1")
    assert directive.studio_audio_mode == "SPEECH_PLUS_MUSIC"
    assert directive.requires_vocal_separation is True
    assert directive.rvc_eligible is False


@pytest.mark.parametrize(("audio_class", "expected"), [
    ("MUSIC_ONLY", "MUSIC_ONLY"),
    ("NO_AUDIO", "NO_AUDIO"),
    ("NOISE", "MUSIC_ONLY"),
    ("NO_USABLE_NARRATION", "MUSIC_ONLY"),
    ("FOREIGN_SPEECH", "FOREIGN_SPEECH"),
])
def test_non_portuguese_narration_routes_never_use_rvc(tmp_path, audio_class, expected):
    db, _ = _registry(tmp_path, audio_class=audio_class, language="unknown",
                      speech_detected=0, rvc_eligible=0)
    directive = read_studio_directive(db, source_id="s1", item_id="i1")
    assert directive.studio_audio_mode == expected
    assert directive.rvc_eligible is False


def test_persisted_rvc_true_does_not_override_non_portuguese_language(tmp_path):
    db, _ = _registry(tmp_path, language="en")
    assert read_studio_directive(db, source_id="s1", item_id="i1").rvc_eligible is False


def test_missing_decision_fails_closed(tmp_path):
    db, _ = _registry(tmp_path)
    with pytest.raises(DecisionNotFound):
        read_studio_directive(db, source_id="missing", item_id="i1")


def test_non_ready_decision_fails_closed(tmp_path):
    db, _ = _registry(tmp_path, status="PROCESSING")
    with pytest.raises(DecisionNotReady):
        read_studio_directive(db, source_id="s1", item_id="i1")


def test_missing_wav_fails_closed(tmp_path):
    db, _ = _registry(tmp_path, wav=False)
    with pytest.raises(DecisionNotReady, match="WAV"):
        read_studio_directive(db, source_id="s1", item_id="i1")


def test_unknown_class_fails_closed(tmp_path):
    db, _ = _registry(tmp_path, audio_class="UNRECOGNIZED", rvc_eligible=0)
    with pytest.raises(IncompatibleDecision):
        read_studio_directive(db, source_id="s1", item_id="i1")
