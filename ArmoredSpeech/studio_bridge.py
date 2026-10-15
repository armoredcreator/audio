from __future__ import annotations

"""Read-only adapter between ArmoredSpeech's experimental SQLite registry and Studio.

This module does not process media, invoke RVC, modify originals, or publish.
It translates only a persisted READY decision and fails closed for invalid rows.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

BRIDGE_CONTRACT_VERSION = 1


class BridgeError(RuntimeError):
    """Base class for invalid or unavailable persisted audio decisions."""


class DecisionNotFound(BridgeError):
    pass


class DecisionNotReady(BridgeError):
    pass


class IncompatibleDecision(BridgeError):
    pass


@dataclass(frozen=True, slots=True)
class StudioAudioDirective:
    source_id: str
    item_id: str
    original_path: Path
    wav_path: Path | None
    source_audio_class: str
    studio_audio_mode: str
    language: str | None
    confidence: float | None
    classifier_version: str | None
    rvc_eligible: bool
    requires_vocal_separation: bool
    contract_version: int = BRIDGE_CONTRACT_VERSION


# Match current ArmoredStudio AudioMode values. NOISE and NO_USABLE_NARRATION
# retain their source class while mapping to the existing non-RVC/effect route.
_STUDIO_MODE_BY_CLASS = {
    "PT_BR_NARRATION": "SPEECH",
    "PT_BR_NARRATION_WITH_MUSIC": "SPEECH_PLUS_MUSIC",
    "MUSIC_ONLY": "MUSIC_ONLY",
    "NO_AUDIO": "NO_AUDIO",
    "NOISE": "MUSIC_ONLY",
    "NO_USABLE_NARRATION": "MUSIC_ONLY",
    "FOREIGN_SPEECH": "FOREIGN_SPEECH",
}


def read_studio_directive(
    database: Path,
    *,
    source_id: str,
    item_id: str,
    require_files: bool = True,
) -> StudioAudioDirective:
    """Read one READY result; never create or mutate registry rows.

    Set require_files=False only for contract tests. Normal consumers should
    require the original and derived WAV to exist before routing.
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
                   speech_detected, confidence, rvc_eligible, status, error
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
    if row["audio_class"] not in _STUDIO_MODE_BY_CLASS:
        raise IncompatibleDecision(f"Unsupported audio class: {row['audio_class']!r}")

    original_path = Path(row["video_path"])
    wav_path = Path(row["wav_path"]) if row["wav_path"] else None
    if require_files:
        if not original_path.is_file():
            raise DecisionNotReady(f"Original media is missing: {original_path}")
        if wav_path is None or not wav_path.is_file():
            raise DecisionNotReady("Approved WAV artifact is missing")

    is_pt = (row["language"] or "").strip().lower() in {"pt", "pt-br", "pt_br"}
    audio_class = row["audio_class"]
    rvc_eligible = (
        audio_class == "PT_BR_NARRATION"
        and is_pt
        and bool(row["speech_detected"])
        and bool(row["rvc_eligible"])
    )
    return StudioAudioDirective(
        source_id=row["source_id"],
        item_id=row["item_id"],
        original_path=original_path,
        wav_path=wav_path,
        source_audio_class=audio_class,
        studio_audio_mode=_STUDIO_MODE_BY_CLASS[audio_class],
        language=row["language"],
        confidence=row["confidence"],
        classifier_version=None,
        rvc_eligible=rvc_eligible,
        requires_vocal_separation=audio_class == "PT_BR_NARRATION_WITH_MUSIC",
    )
