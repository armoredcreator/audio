from __future__ import annotations

import sqlite3
from pathlib import Path

from .contracts import SpeechDecision, SpeechStatus


SCHEMA = """
CREATE TABLE IF NOT EXISTS armored_speech_results (
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
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (source_id, item_id)
);
"""


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(SCHEMA)
    connection.commit()
    return connection


def record_result(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    item_id: str,
    video_path: Path,
    wav_path: Path | None,
    decision: SpeechDecision,
    status: SpeechStatus,
    original_sha256_before: str | None = None,
    original_sha256_after: str | None = None,
    error: str | None = None,
) -> None:
    decision.validate()
    connection.execute(
        """
        INSERT INTO armored_speech_results (
            source_id, item_id, video_path, wav_path, audio_class, language,
            speech_detected, music_detected, confidence, rvc_eligible, status,
            original_sha256_before, original_sha256_after, error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        ON CONFLICT(source_id, item_id) DO UPDATE SET
            video_path=excluded.video_path,
            wav_path=excluded.wav_path,
            audio_class=excluded.audio_class,
            language=excluded.language,
            speech_detected=excluded.speech_detected,
            music_detected=excluded.music_detected,
            confidence=excluded.confidence,
            rvc_eligible=excluded.rvc_eligible,
            status=excluded.status,
            original_sha256_before=excluded.original_sha256_before,
            original_sha256_after=excluded.original_sha256_after,
            error=excluded.error,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (
            source_id, item_id, str(video_path), str(wav_path) if wav_path else None,
            decision.audio_class.value, decision.language, int(decision.speech_detected),
            None if decision.music_detected is None else int(decision.music_detected),
            decision.confidence, int(decision.rvc_eligible), status.value,
            original_sha256_before, original_sha256_after, error,
        ),
    )
    connection.commit()
