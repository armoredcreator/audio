from __future__ import annotations

"""Studio-owned fail-closed validation for neutral Speech handoff payloads."""

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpeechHandoffError(RuntimeError):
    """Speech decision or artifact cannot safely be consumed."""


@dataclass(frozen=True, slots=True)
class ValidatedHandoff:
    source_id: str
    item_id: str
    original_path: Path
    wav_path: Path
    audio_class: str
    rvc_eligible: bool
    original_sha256: str
    wav_sha256: str


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


def validate_handoff(
    payload: dict[str, Any],
    *,
    workspace_root: Path,
    expected_source_id: str,
    expected_item_id: str,
    expected_original_path: Path | None = None,
) -> ValidatedHandoff:
    """Validate item identity, workspace containment, WAV readability and hashes."""
    if not isinstance(payload, dict) or payload.get("contract_version") != 1:
        raise SpeechHandoffError("Unsupported or invalid Speech contract")

    if payload.get("source_id") != expected_source_id or payload.get("item_id") != expected_item_id:
        raise SpeechHandoffError("Speech decision identity does not match Studio item")

    try:
        workspace = Path(workspace_root).resolve(strict=True)
        original = Path(payload["original_path"]).resolve(strict=True)
        wav_path = Path(payload["audio_artifact_path"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError("Original or WAV path is unavailable") from exc

    if not original.is_file() or not _is_within(original, workspace):
        raise SpeechHandoffError("Original must be a file inside the lab workspace")
    if not wav_path.is_file() or not _is_within(wav_path, workspace):
        raise SpeechHandoffError("WAV must be a file inside the lab workspace")
    if expected_original_path is not None and original != Path(expected_original_path).resolve(strict=True):
        raise SpeechHandoffError("Original path does not match Studio item")
    if original.stat().st_size <= 0:
        raise SpeechHandoffError("Original media is empty")
    if wav_path.stat().st_size <= 44 or wav_path.stem.casefold() != original.stem.casefold():
        raise SpeechHandoffError("WAV must be a nonempty sidecar matching the original basename")

    try:
        with wave.open(str(wav_path), "rb") as audio:
            if audio.getnchannels() < 1 or audio.getframerate() < 1 or audio.getnframes() < 1:
                raise SpeechHandoffError("WAV has no usable audio frames")
            if not audio.readframes(1):
                raise SpeechHandoffError("WAV data is unreadable")
    except (wave.Error, EOFError, OSError) as exc:
        raise SpeechHandoffError("Artifact is not a readable WAV") from exc

    original_hash = sha256_file(original)
    wav_hash = sha256_file(wav_path)
    before, after = payload.get("original_sha256_before"), payload.get("original_sha256_after")
    if not isinstance(before, str) or not isinstance(after, str):
        raise SpeechHandoffError("Speech must persist before/after original SHA-256")
    if before.lower() != after.lower() or before.lower() != original_hash:
        raise SpeechHandoffError("Original SHA-256 does not match Speech evidence")

    audio_class = payload.get("audio_class")
    supported = {
        "PT_BR_NARRATION", "PT_BR_NARRATION_WITH_MUSIC", "MUSIC_ONLY",
        "NO_AUDIO", "NOISE", "NO_USABLE_NARRATION", "FOREIGN_SPEECH",
    }
    if audio_class not in supported:
        raise SpeechHandoffError("Unknown/unsupported audio class")

    language = str(payload.get("language") or "").strip().lower().replace("_", "-")
    declared_rvc = payload.get("rvc_eligible") is True
    rvc_eligible = (
        audio_class == "PT_BR_NARRATION"
        and language in {"pt", "pt-br"}
        and payload.get("speech_detected") is True
        and payload.get("music_detected") is not True
        and declared_rvc
    )
    if declared_rvc and not rvc_eligible:
        raise SpeechHandoffError("Unsafe RVC eligibility claim")

    return ValidatedHandoff(
        source_id=expected_source_id,
        item_id=expected_item_id,
        original_path=original,
        wav_path=wav_path,
        audio_class=audio_class,
        rvc_eligible=rvc_eligible,
        original_sha256=original_hash,
        wav_sha256=wav_hash,
    )


@dataclass(frozen=True, slots=True)
class ValidatedAudioPackage:
    """Minimal validated v3 handoff: one completed Speech-owned soundtrack."""
    source_id: str
    item_id: str
    original_path: Path
    final_audio_track_path: Path
    audio_class: str
    route: str
    original_sha256: str
    final_audio_sha256: str
    visual_plan_sha256: str
    timeline: dict[str, Any]


def validate_audio_package(
    payload: dict[str, Any],
    *,
    workspace_root: Path,
    expected_source_id: str,
    expected_item_id: str,
    expected_original_path: Path | None = None,
    track_hash_cache: dict[tuple[str, int, int, int, int], str] | None = None,
) -> ValidatedAudioPackage:
    """Validate the final Speech soundtrack without duplicating audio decisions.

    Studio checks identity, containment, original integrity, WAV format/hash and
    timeline-plan correspondence. It deliberately does not classify audio, make
    an RVC decision, inspect RVC assets, or receive separate voice/effect tracks.
    """
    if not isinstance(payload, dict) or payload.get("contract_version") != 3:
        raise SpeechHandoffError("Studio requires ArmoredSpeech final-audio contract v3")
    if payload.get("status") != "READY":
        raise SpeechHandoffError("Speech final-audio package is not READY")
    if (
        str(payload.get("source_id")) != expected_source_id
        or str(payload.get("item_id")) != expected_item_id
    ):
        raise SpeechHandoffError("Speech package identity does not match Studio item")

    try:
        workspace = Path(workspace_root).resolve(strict=True)
        original = Path(payload["original_path"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError("Original media unavailable in Speech package") from exc
    if not original.is_file() or not _is_within(original, workspace):
        raise SpeechHandoffError("Original must be a file inside the lab workspace")
    if expected_original_path is not None and original != Path(expected_original_path).resolve(strict=True):
        raise SpeechHandoffError("Original path does not match Studio item")

    before = payload.get("original_sha256_before")
    after = payload.get("original_sha256_after")
    actual_original_hash = sha256_file(original)
    if (
        not isinstance(before, str)
        or not isinstance(after, str)
        or before.lower() != after.lower()
        or before.lower() != actual_original_hash
    ):
        raise SpeechHandoffError("Speech original SHA-256 evidence is missing or inconsistent")

    track, track_hash, track_data = _validate_audio_track(
        payload.get("final_audio_track"),
        workspace=workspace,
        label="Final Speech soundtrack",
        track_hash_cache=track_hash_cache,
    )
    if track_data.get("composition_owner") != "ArmoredSpeech":
        raise SpeechHandoffError("Final soundtrack must be composed by ArmoredSpeech")

    timeline = payload.get("timeline")
    if not isinstance(timeline, dict):
        raise SpeechHandoffError("Speech final-audio timeline metadata missing")
    plan_hash = timeline.get("visual_plan_sha256")
    if not isinstance(plan_hash, str) or len(plan_hash) != 64:
        raise SpeechHandoffError("Visual plan SHA-256 is missing or invalid")
    try:
        int(plan_hash, 16)
        audio_start = float(timeline.get("audio_start_seconds"))
        intro_enabled = timeline.get("intro_enabled")
        intro_duration = float(timeline.get("intro_duration_seconds"))
    except (TypeError, ValueError) as exc:
        raise SpeechHandoffError("Speech audio timeline metadata is invalid") from exc
    if audio_start < 0 or not isinstance(intro_enabled, bool):
        raise SpeechHandoffError("Speech audio timeline fields are invalid")
    position = timeline.get("intro_position")
    if intro_enabled:
        if position not in {"inicio", "final"} or abs(intro_duration - 2.0) > 0.01:
            raise SpeechHandoffError("Speech intro timeline policy is invalid")
    elif position is not None or abs(intro_duration) > 0.01:
        raise SpeechHandoffError("Speech no-intro timeline metadata is inconsistent")

    audio_class = str(payload.get("audio_class") or "")
    route = str(payload.get("route") or "")
    if not audio_class or not route:
        raise SpeechHandoffError("Speech audio decision metadata is missing")

    return ValidatedAudioPackage(
        source_id=expected_source_id,
        item_id=expected_item_id,
        original_path=original,
        final_audio_track_path=track,
        audio_class=audio_class,
        route=route,
        original_sha256=actual_original_hash,
        final_audio_sha256=track_hash,
        visual_plan_sha256=plan_hash.lower(),
        timeline=dict(timeline),
    )
