from __future__ import annotations

"""Export ArmoredSpeech decisions as a versioned, tool-neutral hand-off payload.

This module reads only Speech-owned SQLite storage. A receiving tool must
consume the serialized contract and apply its own internal routing; it must
not query this database or import Speech implementation details.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

BRIDGE_CONTRACT_VERSION = 1

SUPPORTED_AUDIO_CLASSES = frozenset({
    "PT_BR_NARRATION",
    "PT_BR_NARRATION_WITH_MUSIC",
    "MUSIC_ONLY",
    "NO_AUDIO",
    "NOISE",
    "NO_USABLE_NARRATION",
    "FOREIGN_SPEECH",
})


class BridgeError(RuntimeError):
    """Base class for invalid or unavailable persisted audio decisions."""


class DecisionNotFound(BridgeError):
    pass


class DecisionNotReady(BridgeError):
    pass


class IncompatibleDecision(BridgeError):
    pass


@dataclass(frozen=True, slots=True)
class SpeechAudioDirective:
    """Neutral JSON contract; it intentionally has no Studio-specific mode."""

    source_id: str
    item_id: str
    original_path: Path
    audio_artifact_path: Path
    audio_class: str
    language: str | None
    speech_detected: bool
    music_detected: bool | None
    confidence: float | None
    rvc_eligible: bool
    requires_vocal_separation: bool
    original_sha256_before: str | None = None
    original_sha256_after: str | None = None
    classifier_version: str | None = None
    contract_version: int = BRIDGE_CONTRACT_VERSION

    def to_payload(self) -> dict[str, object]:
        """Return JSON-safe data without exposing a SQLite row or Studio enums."""
        return {
            "contract_version": self.contract_version,
            "source_id": self.source_id,
            "item_id": self.item_id,
            "original_path": str(self.original_path),
            "audio_artifact_path": str(self.audio_artifact_path),
            "audio_class": self.audio_class,
            "language": self.language,
            "speech_detected": self.speech_detected,
            "music_detected": self.music_detected,
            "confidence": self.confidence,
            "rvc_eligible": self.rvc_eligible,
            "requires_vocal_separation": self.requires_vocal_separation,
            "original_sha256_before": self.original_sha256_before,
            "original_sha256_after": self.original_sha256_after,
            "classifier_version": self.classifier_version,
        }


def export_audio_directive(
    database: Path,
    *,
    source_id: str,
    item_id: str,
    require_files: bool = True,
) -> SpeechAudioDirective:
    """Export one READY Speech decision; never create or mutate registry rows.

    This is a producer-side adapter only. It does not integrate with or invoke
    ArmoredStudio. Set require_files=False only for isolated contract tests.
    """
    database = Path(database)
    if not database.is_file():
        raise DecisionNotFound(f"Speech registry database not found: {database}")

    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT source_id, item_id, video_path, wav_path, audio_class, language,
                   speech_detected, music_detected, confidence, rvc_eligible, status,
                   error, original_sha256_before, original_sha256_after
            FROM armored_speech_results
            WHERE source_id = ? AND item_id = ?
            """,
            (source_id, item_id),
        ).fetchone()
    except sqlite3.Error as exc:
        raise IncompatibleDecision(
            f"Speech registry schema is incompatible: {exc}"
        ) from exc
    finally:
        connection.close()

    if row is None:
        raise DecisionNotFound(
            f"No Speech decision for source_id={source_id!r}, item_id={item_id!r}"
        )
    if row["status"] != "READY":
        detail = f": {row['error']}" if row["error"] else ""
        raise DecisionNotReady(f"Speech decision status is {row['status']!r}{detail}")

    audio_class = row["audio_class"]
    if audio_class not in SUPPORTED_AUDIO_CLASSES:
        raise IncompatibleDecision(f"Unsupported audio class: {audio_class!r}")

    original_path = Path(row["video_path"])
    if not row["wav_path"]:
        raise DecisionNotReady("Approved WAV artifact path is missing")
    audio_artifact_path = Path(row["wav_path"])
    if require_files:
        if not original_path.is_file():
            raise DecisionNotReady(f"Original media is missing: {original_path}")
        if not audio_artifact_path.is_file():
            raise DecisionNotReady("Approved WAV artifact is missing")

    before = row["original_sha256_before"]
    after = row["original_sha256_after"]
    if before and after and before.lower() != after.lower():
        raise DecisionNotReady("Original media hash changed during Speech processing")

    confidence = row["confidence"]
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise IncompatibleDecision("Persisted confidence is not numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise IncompatibleDecision("Persisted confidence is outside [0, 1]")

    language = row["language"]
    is_pt = (language or "").strip().lower() in {"pt", "pt-br", "pt_br"}
    # Fail closed: mixed speech/music remains ineligible until a separately
    # validated vocal-separation artifact is part of a future contract version.
    rvc_eligible = (
        audio_class == "PT_BR_NARRATION"
        and is_pt
        and bool(row["speech_detected"])
        and bool(row["rvc_eligible"])
    )
    music_detected = (
        bool(row["music_detected"])
        if row["music_detected"] is not None else None
    )
    return SpeechAudioDirective(
        source_id=row["source_id"],
        item_id=row["item_id"],
        original_path=original_path,
        audio_artifact_path=audio_artifact_path,
        audio_class=audio_class,
        language=language,
        speech_detected=bool(row["speech_detected"]),
        music_detected=music_detected,
        confidence=confidence,
        rvc_eligible=rvc_eligible,
        requires_vocal_separation=audio_class == "PT_BR_NARRATION_WITH_MUSIC",
        original_sha256_before=before,
        original_sha256_after=after,
        classifier_version=None,
    )
