from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ArmoredSpeech.studio_bridge import (
    DecisionNotFound,
    DecisionNotReady,
    IncompatibleDecision,
    export_audio_directive,
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
    return db, original, wav_path


def test_ready_pt_br_narration_exports_neutral_json_contract(tmp_path):
    db, original, wav_path = _registry(tmp_path)
    directive = export_audio_directive(db, source_id="s1", item_id="i1")
    assert directive.original_path == original
    assert directive.audio_artifact_path == wav_path
    assert directive.audio_class == "PT_BR_NARRATION"
    assert directive.rvc_eligible is True
    assert original.read_bytes() == b"immutable-original"

    payload = directive.to_payload()
    assert payload["contract_version"] == 1
    assert payload["source_id"] == "s1"
    assert payload["item_id"] == "i1"
    assert payload["audio_class"] == "PT_BR_NARRATION"
    assert payload["audio_artifact_path"] == str(wav_path)
    assert "studio_audio_mode" not in payload
    assert "StudioAudioMode" not in json.dumps(payload)
    json.dumps(payload)  # payload is JSON serializable


def test_mixed_audio_is_blocked_until_separation_is_validated(tmp_path):
    db, _, _ = _registry(tmp_path, audio_class="PT_BR_NARRATION_WITH_MUSIC")
    directive = export_audio_directive(db, source_id="s1", item_id="i1")
    assert directive.audio_class == "PT_BR_NARRATION_WITH_MUSIC"
    assert directive.requires_vocal_separation is True
    assert directive.rvc_candidate_eligible is True
    assert directive.rvc_eligible is False
    assert directive.to_payload()["rvc_candidate_eligible"] is True


@pytest.mark.parametrize("audio_class", [
    "MUSIC_ONLY",
    "NO_AUDIO",
    "NOISE",
    "NO_USABLE_NARRATION",
    "FOREIGN_SPEECH",
])
def test_non_portuguese_narration_never_uses_rvc(tmp_path, audio_class):
    db, _, _ = _registry(tmp_path, audio_class=audio_class, language="unknown",
                         speech_detected=0, rvc_eligible=0)
    directive = export_audio_directive(db, source_id="s1", item_id="i1")
    assert directive.audio_class == audio_class
    assert directive.rvc_eligible is False


def test_persisted_rvc_true_does_not_override_non_portuguese_language(tmp_path):
    db, _, _ = _registry(tmp_path, language="en")
    assert export_audio_directive(
        db, source_id="s1", item_id="i1"
    ).rvc_eligible is False


def test_missing_decision_fails_closed(tmp_path):
    db, _, _ = _registry(tmp_path)
    with pytest.raises(DecisionNotFound):
        export_audio_directive(db, source_id="missing", item_id="i1")


def test_non_ready_decision_fails_closed(tmp_path):
    db, _, _ = _registry(tmp_path, status="PROCESSING")
    with pytest.raises(DecisionNotReady):
        export_audio_directive(db, source_id="s1", item_id="i1")


def test_missing_wav_fails_closed(tmp_path):
    db, _, _ = _registry(tmp_path, wav=False)
    with pytest.raises(DecisionNotReady, match="WAV"):
        export_audio_directive(db, source_id="s1", item_id="i1")


def test_unknown_class_fails_closed(tmp_path):
    db, _, _ = _registry(tmp_path, audio_class="UNRECOGNIZED", rvc_eligible=0)
    with pytest.raises(IncompatibleDecision):
        export_audio_directive(db, source_id="s1", item_id="i1")


def test_changed_original_hash_fails_closed(tmp_path):
    db, _, _ = _registry(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE armored_speech_results SET original_sha256_before=?, "
            "original_sha256_after=? WHERE source_id='s1' AND item_id='i1'",
            ("before-hash", "after-hash"),
        )
    with pytest.raises(DecisionNotReady, match="hash changed"):
        export_audio_directive(db, source_id="s1", item_id="i1")
