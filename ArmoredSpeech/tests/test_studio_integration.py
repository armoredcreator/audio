from __future__ import annotations

import hashlib
import sqlite3
import wave
from dataclasses import dataclass
from pathlib import Path

from ArmoredSpeech.studio_integration import process_item_with_speech


@dataclass
class FakeItem:
    source_id: str
    telegram_message_id: str
    original_path: Path


class CaptureStudio:
    def __init__(self):
        self.item = None
        self.payload = None

    def process(self, item, *, speech_payload=None):
        self.item = item
        self.payload = speech_payload
        return "accepted-by-studio"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ready_speech_registry_payload_is_forwarded_to_studio(tmp_path):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic disposable video")
    wav_path = tmp_path / "source.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 160)

    digest = _sha(original)
    database = tmp_path / "speech.sqlite3"
    with sqlite3.connect(database) as con:
        con.execute("""
            CREATE TABLE armored_speech_results (
                source_id TEXT NOT NULL, item_id TEXT NOT NULL,
                video_path TEXT NOT NULL, wav_path TEXT, audio_class TEXT NOT NULL,
                language TEXT, speech_detected INTEGER NOT NULL, music_detected INTEGER,
                confidence REAL, rvc_eligible INTEGER NOT NULL, status TEXT NOT NULL,
                original_sha256_before TEXT, original_sha256_after TEXT, error TEXT,
                updated_at TEXT NOT NULL DEFAULT 'test',
                PRIMARY KEY (source_id, item_id)
            )
        """)
        con.execute("""
            INSERT INTO armored_speech_results (
                source_id,item_id,video_path,wav_path,audio_class,language,
                speech_detected,music_detected,confidence,rvc_eligible,status,
                original_sha256_before,original_sha256_after,error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
        """, (
            "source-1", "telegram-1", str(original), str(wav_path),
            "PT_BR_NARRATION", "pt-br", 1, 0, 0.99, 1, "READY", digest, digest,
        ))

    item = FakeItem("source-1", "telegram-1", original)
    studio = CaptureStudio()
    result = process_item_with_speech(studio, item, speech_database=database)

    assert result == "accepted-by-studio"
    assert studio.item is item
    assert studio.payload["source_id"] == "source-1"
    assert studio.payload["item_id"] == "telegram-1"
    assert studio.payload["audio_artifact_path"] == str(wav_path)
    assert studio.payload["rvc_eligible"] is True
