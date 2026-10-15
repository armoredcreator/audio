from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .contracts import AudioClass, SpeechDecision, SpeechStatus
from .registry import SCHEMA, record_result


@dataclass(frozen=True, slots=True)
class SpeechItem:
    source_id: str
    item_id: str
    video_path: Path


class SpeechProcessor(Protocol):
    """Provider seam for the audio analyzer/separator implementation."""

    def analyze(self, video_path: Path) -> SpeechDecision: ...
    def prepare_wav(self, video_path: Path, wav_path: Path, decision: SpeechDecision) -> None: ...


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wav_path_for(video_path: Path) -> Path:
    # Keep the derived WAV next to the original and retain the exact basename.
    # Example: 291_finallinkoriginal.mp4 -> 291_finallinkoriginal.wav
    return video_path.with_suffix(".wav")


def run_bulk(
    items: Sequence[SpeechItem],
    *,
    database: Path,
    processor: SpeechProcessor,
) -> dict[str, int]:
    """Process source-by-source, recording every item outcome in SQLite.

    READY means prepared for Studio, not approved for RVC. RVC eligibility is
    represented separately by the rvc_eligible column. Original MP4s are read-only.
    """
    counts = {"prepared": 0, "rvc_eligible": 0, "blocked": 0, "failed": 0}
    ordered = sorted(items, key=lambda item: (item.source_id, item.item_id))
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(SCHEMA)
        connection.commit()
        for item in ordered:
            video = item.video_path.expanduser().resolve()
            if not video.is_file():
                decision = SpeechDecision(
                    audio_class=AudioClass.UNKNOWN,
                    language=None,
                    speech_detected=False,
                    music_detected=None,
                    confidence=None,
                    rationale="Original video is missing; no media was changed.",
                )
                record_result(
                    connection, source_id=item.source_id, item_id=item.item_id,
                    video_path=video, wav_path=None, decision=decision,
                    status=SpeechStatus.FAILED, error="VIDEO_NOT_FOUND",
                )
                counts["failed"] += 1
                continue

            before = sha256(video)
            wav = wav_path_for(video)
            try:
                decision = processor.analyze(video)
                decision.validate()
                # Unknown labels cannot produce an artifact that downstream code
                # might mistake for approved narration.
                if decision.audio_class is AudioClass.UNKNOWN:
                    record_result(
                        connection, source_id=item.source_id, item_id=item.item_id,
                        video_path=video, wav_path=None, decision=decision,
                        status=SpeechStatus.BLOCKED, original_sha256_before=before,
                        original_sha256_after=sha256(video), error="CLASSIFICATION_UNKNOWN",
                    )
                    counts["blocked"] += 1
                    continue

                processor.prepare_wav(video, wav, decision)
                if not wav.is_file() or wav.stat().st_size == 0:
                    raise RuntimeError("WAV_NOT_CREATED_OR_EMPTY")
                after = sha256(video)
                if before != after:
                    raise RuntimeError("ORIGINAL_HASH_CHANGED")

                # Music-only/no-audio/no-usable/foreign speech still need a
                # persisted decision and prepared sidecar for Studio; they are
                # READY for Studio but explicitly not eligible for RVC.
                record_result(
                    connection, source_id=item.source_id, item_id=item.item_id,
                    video_path=video, wav_path=wav, decision=decision,
                    status=SpeechStatus.READY, original_sha256_before=before,
                    original_sha256_after=after,
                )
                counts["prepared"] += 1
                if decision.rvc_eligible:
                    counts["rvc_eligible"] += 1
                else:
                    counts["blocked"] += 1
            except Exception as exc:
                after = sha256(video) if video.exists() else None
                failed = SpeechDecision(
                    audio_class=AudioClass.UNKNOWN,
                    language=None,
                    speech_detected=False,
                    music_detected=None,
                    confidence=None,
                    rationale="Processing failed; fail-closed for downstream RVC.",
                )
                record_result(
                    connection, source_id=item.source_id, item_id=item.item_id,
                    video_path=video, wav_path=wav if wav.exists() else None,
                    decision=failed, status=SpeechStatus.FAILED,
                    original_sha256_before=before, original_sha256_after=after,
                    error=f"{type(exc).__name__}: {exc}",
                )
                counts["failed"] += 1
    return counts
