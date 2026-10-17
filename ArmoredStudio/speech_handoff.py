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
    """A fully validated v2 package produced by ArmoredSpeech."""
    source_id: str
    item_id: str
    original_path: Path
    voice_track_path: Path
    effect_track_path: Path
    audio_class: str
    language: str
    route: str
    rvc_eligible: bool
    original_sha256: str
    voice_sha256: str
    effect_sha256: str


def _validate_audio_track(
    raw: object,
    *,
    workspace: Path,
    label: str,
) -> tuple[Path, str, dict[str, object]]:
    if not isinstance(raw, dict):
        raise SpeechHandoffError(f"{label} metadata missing or invalid")
    try:
        path = Path(raw["path"]).resolve(strict=True)
        expected_hash = raw["sha256"]
        sample_rate = raw["sample_rate"]
        channels = raw["channels"]
        duration = float(raw["duration_seconds"])
        format_name = raw["format"]
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError(f"{label} metadata or path unavailable") from exc

    if not path.is_file() or not _is_within(path, workspace):
        raise SpeechHandoffError(f"{label} must be a file inside the lab workspace")
    if path.stat().st_size <= 44:
        raise SpeechHandoffError(f"{label} file is empty or too small")
    if format_name != "wav-pcm-s16le":
        raise SpeechHandoffError(f"{label} must declare wav-pcm-s16le")
    if isinstance(sample_rate, bool) or sample_rate != 44100:
        raise SpeechHandoffError(f"{label} must use 44100 Hz")
    if isinstance(channels, bool) or channels != 2:
        raise SpeechHandoffError(f"{label} must use stereo audio")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise SpeechHandoffError(f"{label} SHA-256 metadata invalid")
    try:
        int(expected_hash, 16)
    except ValueError as exc:
        raise SpeechHandoffError(f"{label} SHA-256 metadata invalid") from exc
    if not (duration > 0.0):
        raise SpeechHandoffError(f"{label} duration must be positive")

    try:
        with wave.open(str(path), "rb") as audio:
            actual_rate = audio.getframerate()
            actual_channels = audio.getnchannels()
            frames = audio.getnframes()
            if frames < 1 or not audio.readframes(1):
                raise SpeechHandoffError(f"{label} contains no readable samples")
            actual_duration = frames / actual_rate
    except (wave.Error, EOFError, OSError, ZeroDivisionError) as exc:
        raise SpeechHandoffError(f"{label} is not a readable PCM WAV") from exc

    if actual_rate != sample_rate or actual_channels != channels:
        raise SpeechHandoffError(f"{label} technical metadata mismatch")
    if abs(actual_duration - duration) > 0.02:
        raise SpeechHandoffError(f"{label} duration metadata mismatch")
    digest = sha256_file(path)
    if digest.lower() != expected_hash.lower():
        raise SpeechHandoffError(f"{label} SHA-256 mismatch")
    return path, digest, raw


def validate_audio_package(
    payload: dict[str, Any],
    *,
    workspace_root: Path,
    expected_source_id: str,
    expected_item_id: str,
    expected_original_path: Path | None = None,
) -> ValidatedAudioPackage:
    """Validate Speech-owned audio package v2 before any Studio render begins."""
    if not isinstance(payload, dict) or payload.get("contract_version") != 2:
        raise SpeechHandoffError("Unsupported or invalid Speech audio package contract")
    if payload.get("status") != "READY":
        raise SpeechHandoffError("Speech audio package is not READY")
    if (
        str(payload.get("source_id")) != expected_source_id
        or str(payload.get("item_id")) != expected_item_id
    ):
        raise SpeechHandoffError("Speech audio package identity does not match Studio item")

    try:
        workspace = Path(workspace_root).resolve(strict=True)
        original = Path(payload["original_path"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError("Original media unavailable in Speech audio package") from exc
    if not original.is_file() or not _is_within(original, workspace):
        raise SpeechHandoffError("Original must be a file inside the lab workspace")
    if expected_original_path is not None:
        if original != Path(expected_original_path).resolve(strict=True):
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

    audio_class = payload.get("audio_class")
    supported_classes = {
        "PT_BR_NARRATION", "PT_BR_NARRATION_WITH_MUSIC", "MUSIC_ONLY",
        "NO_AUDIO", "NOISE", "NO_USABLE_NARRATION", "FOREIGN_SPEECH",
    }
    if audio_class not in supported_classes:
        raise SpeechHandoffError("Unknown/unsupported v2 audio class")
    language = str(payload.get("language") or "").strip().lower().replace("_", "-")
    route = payload.get("route")
    rvc_eligible = payload.get("rvc_eligible") is True
    model = payload.get("rvc_model")
    safe_rvc_decision = (
        audio_class == "PT_BR_NARRATION"
        and language in {"pt", "pt-br"}
        and payload.get("speech_detected") is True
        and payload.get("music_detected") is False
        and rvc_eligible
    )
    if route == "RVC_MELODY":
        if not safe_rvc_decision or str(model).strip().lower() != "melody":
            raise SpeechHandoffError("Unsafe or inconsistent RVC_MELODY route")
    elif route == "EFFECT_ONLY":
        if rvc_eligible or model is not None or safe_rvc_decision:
            raise SpeechHandoffError("EFFECT_ONLY route conflicts with RVC eligibility")
    else:
        raise SpeechHandoffError("Unsupported audio route")

    voice_path, voice_hash, _ = _validate_audio_track(
        payload.get("voice_track"), workspace=workspace, label="Voice track"
    )
    effect_path, effect_hash, effect_data = _validate_audio_track(
        payload.get("effect_track"), workspace=workspace, label="Effect track"
    )
    if effect_data.get("gain_policy") != "loudnorm-I-12-LRA7-TP-1-then-volume-0.8":
        raise SpeechHandoffError("Effect gain policy is absent or not approved")

    return ValidatedAudioPackage(
        source_id=expected_source_id,
        item_id=expected_item_id,
        original_path=original,
        voice_track_path=voice_path,
        effect_track_path=effect_path,
        audio_class=str(audio_class),
        language=language,
        route=str(route),
        rvc_eligible=rvc_eligible,
        original_sha256=actual_original_hash,
        voice_sha256=voice_hash,
        effect_sha256=effect_hash,
    )
