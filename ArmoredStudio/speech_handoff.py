from __future__ import annotations

"""Minimal Studio-side validation for the completed ArmoredSpeech audio artifact.

The Studio boundary deliberately contains no audio classification, route policy,
RVC validation, component-track inspection, mixing, gain, fade, or filter-graph
execution. Speech owns all of those. Studio validates one final AAC track and
its timing/hash contract, then stream-copies it into the visual render.
"""

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpeechHandoffError(RuntimeError):
    """The final audio artifact or its identity/timing contract is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _valid_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ValidatedAudioPackage:
    """Only the final ready-to-mux audio track crosses into Studio."""
    source_id: str
    item_id: str
    original_path: Path
    final_audio_track_path: Path
    final_audio_sha256: str
    final_audio_duration_seconds: float
    original_sha256: str
    visual_plan_sha256: str
    audio_timeline: dict[str, Any]


def validate_audio_package(
    payload: dict[str, Any],
    *,
    workspace_root: Path,
    expected_source_id: str,
    expected_item_id: str,
    expected_original_path: Path | None = None,
    track_hash_cache: dict[tuple[str, int, int, int, int], str] | None = None,
) -> ValidatedAudioPackage:
    """Fail closed unless Speech provides one complete AAC soundtrack.

    The validator never reads voice/effect component paths and never validates or
    executes an audio route. It checks only artifact identity, containment,
    cryptographic integrity, canonical format metadata and timeline agreement.
    """
    if not isinstance(payload, dict) or payload.get("contract_version") != 4:
        raise SpeechHandoffError("Studio requires ArmoredSpeech final-audio contract v4")
    if payload.get("status") != "READY":
        raise SpeechHandoffError("Speech final-audio artifact is not READY")
    if (
        str(payload.get("source_id")) != expected_source_id
        or str(payload.get("item_id")) != expected_item_id
    ):
        raise SpeechHandoffError("Speech artifact identity does not match Studio item")

    try:
        workspace = Path(workspace_root).resolve(strict=True)
        original = Path(payload["original_path"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError("Original media unavailable in Speech artifact") from exc
    if not original.is_file() or not _is_within(original, workspace):
        raise SpeechHandoffError("Original must remain inside the lab workspace")
    if expected_original_path is not None:
        if original != Path(expected_original_path).resolve(strict=True):
            raise SpeechHandoffError("Original path does not match the current Studio item")

    expected_original_before = payload.get("original_sha256_before")
    expected_original_after = payload.get("original_sha256_after")
    original_hash = sha256_file(original)
    if (
        not _valid_sha256(expected_original_before)
        or not _valid_sha256(expected_original_after)
        or expected_original_before.lower() != expected_original_after.lower()
        or expected_original_before.lower() != original_hash
    ):
        raise SpeechHandoffError("Speech original SHA-256 evidence is missing or inconsistent")

    track = payload.get("final_audio_track")
    if not isinstance(track, dict):
        raise SpeechHandoffError("Speech must deliver one final audio track")
    try:
        audio_path = Path(track["path"]).resolve(strict=True)
        declared_hash = track["sha256"]
        audio_duration = float(track["duration_seconds"])
        sample_rate = int(track["sample_rate"])
        channels = int(track["channels"])
        size_bytes = int(track["size_bytes"])
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError("Final Speech audio metadata/path is invalid") from exc
    if not audio_path.is_file() or not _is_within(audio_path, workspace):
        raise SpeechHandoffError("Final Speech audio must remain inside the lab workspace")
    if audio_path.suffix.lower() != ".m4a" or track.get("format") != "aac-m4a" or track.get("codec") != "aac":
        raise SpeechHandoffError("Final Speech audio must be AAC in an M4A container")
    if (
        audio_path.stat().st_size <= 128
        or size_bytes != audio_path.stat().st_size
        or sample_rate != 44100
        or channels != 2
        or not _valid_sha256(declared_hash)
        or not math.isfinite(audio_duration)
        or audio_duration <= 0
        or track.get("composition_owner") != "ArmoredSpeech"
    ):
        raise SpeechHandoffError("Final Speech audio format/hash/size metadata is invalid")

    stat = audio_path.stat()
    cache_key = (
        str(audio_path),
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )
    actual_audio_hash = (
        track_hash_cache.get(cache_key) if track_hash_cache is not None else None
    )
    if actual_audio_hash is None:
        actual_audio_hash = sha256_file(audio_path)
        if track_hash_cache is not None:
            track_hash_cache[cache_key] = actual_audio_hash
    if actual_audio_hash.lower() != declared_hash.lower():
        raise SpeechHandoffError("Final Speech audio SHA-256 mismatch")

    timeline = payload.get("audio_timeline")
    if not isinstance(timeline, dict):
        raise SpeechHandoffError("Speech timeline metadata is missing")
    plan_hash = timeline.get("visual_plan_sha256")
    if not _valid_sha256(plan_hash):
        raise SpeechHandoffError("Visual plan SHA-256 is missing or invalid")
    if timeline.get("composition_owner") != "ArmoredSpeech":
        raise SpeechHandoffError("The final soundtrack must be composed by ArmoredSpeech")

    try:
        audio_start = float(timeline["audio_start_seconds"])
        video_start = float(timeline["video_start_seconds"])
        main_duration = float(timeline["main_duration_seconds"])
        intro_duration = float(timeline["intro_duration_seconds"])
        final_duration = float(timeline["final_audio_duration_seconds"])
        intro_enabled = timeline["intro_enabled"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SpeechHandoffError("Speech audio timeline metadata is invalid") from exc

    numbers = (audio_start, video_start, main_duration, intro_duration, final_duration)
    if (
        not all(math.isfinite(value) for value in numbers)
        or audio_start < 0
        or video_start < 0
        or main_duration <= 0
        or not isinstance(intro_enabled, bool)
        or intro_duration < 0
        or abs(intro_duration - (2.0 if intro_enabled else 0.0)) > 0.01
        or abs(final_duration - (main_duration + intro_duration)) > 0.18
        or abs(audio_duration - final_duration) > 0.18
    ):
        raise SpeechHandoffError("Speech final soundtrack duration/timeline is inconsistent")
    position = timeline.get("intro_position")
    if (intro_enabled and position not in {"inicio", "final"}) or (
        not intro_enabled and position is not None
    ):
        raise SpeechHandoffError("Speech intro timeline metadata is inconsistent")
    try:
        prepared_video_duration = float(payload["prepared_video_duration_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SpeechHandoffError("Speech prepared-video duration is invalid") from exc
    if (
        not math.isfinite(prepared_video_duration)
        or prepared_video_duration <= 0
        or abs(prepared_video_duration - main_duration) > 0.02
    ):
        raise SpeechHandoffError("Speech main video duration does not match its timeline")

    return ValidatedAudioPackage(
        source_id=expected_source_id,
        item_id=expected_item_id,
        original_path=original,
        final_audio_track_path=audio_path,
        final_audio_sha256=actual_audio_hash,
        final_audio_duration_seconds=audio_duration,
        original_sha256=original_hash,
        visual_plan_sha256=plan_hash.lower(),
        audio_timeline=dict(timeline),
    )
