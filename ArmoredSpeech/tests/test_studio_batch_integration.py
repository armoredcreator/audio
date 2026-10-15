from __future__ import annotations

import hashlib
import sqlite3
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from ArmoredSpeech.studio_bridge import DecisionNotReady
from ArmoredSpeech.studio_integration import process_items_with_speech


@dataclass(frozen=True)
class FakeItem:
    source_id: str
    telegram_message_id: str
    original_path: Path


class CaptureStudio:
    def __init__(self):
        self.calls = []

    def process(self, item, *, speech_payload=None):
        self.calls.append((item, speech_payload))
        return item.telegram_message_id


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 160)


def _make_item(tmp_path: Path, item_id: str) -> FakeItem:
    original = tmp_path / f"{item_id}.mp4"
    original.write_bytes(f"synthetic disposable video {item_id}".encode())
    _write_wav(tmp_path / f"{item_id}.wav")
    return FakeItem("source-1", item_id, original)


def _make_registry(database: Path, items: list[FakeItem], *, blocked: set[str] = set()) -> None:
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
        for item in items:
            digest = hashlib.sha256(item.original_path.read_bytes()).hexdigest()
            status = "BLOCKED" if item.telegram_message_id in blocked else "READY"
            con.execute("""
                INSERT INTO armored_speech_results (
                    source_id,item_id,video_path,wav_path,audio_class,language,
                    speech_detected,music_detected,confidence,rvc_eligible,status,
                    original_sha256_before,original_sha256_after,error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                item.source_id, item.telegram_message_id, str(item.original_path),
                str(item.original_path.with_suffix(".wav")), "PT_BR_NARRATION",
                "pt-br", 1, 0, 0.99, 1, status, digest, digest,
                "deliberately blocked for test" if status == "BLOCKED" else None,
            ))


def test_batch_preflights_every_speech_result_before_first_studio_call(tmp_path):
    first = _make_item(tmp_path, "100")
    second = _make_item(tmp_path, "200")
    database = tmp_path / "speech.sqlite3"
    _make_registry(database, [first, second], blocked={"200"})
    studio = CaptureStudio()

    with pytest.raises(DecisionNotReady, match="BLOCKED"):
        process_items_with_speech(studio, [first, second], speech_database=database)

    assert studio.calls == []


def test_batch_hands_all_ready_payloads_to_studio_in_input_order(tmp_path):
    first = _make_item(tmp_path, "100")
    second = _make_item(tmp_path, "200")
    database = tmp_path / "speech.sqlite3"
    _make_registry(database, [first, second])
    studio = CaptureStudio()

    results = process_items_with_speech(
        studio, [first, second], speech_database=database
    )

    assert results == ["100", "200"]
    assert [item.telegram_message_id for item, _ in studio.calls] == ["100", "200"]
    assert [payload["item_id"] for _, payload in studio.calls] == ["100", "200"]
    assert all(payload["contract_version"] == 1 for _, payload in studio.calls)
