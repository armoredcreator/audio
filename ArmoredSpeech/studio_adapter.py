from __future__ import annotations

"""Fail-closed validation for the isolated ArmoredSpeech -> Studio handoff."""

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

    language = str(payload.get("language") or "").strip().lower()
    declared_rvc = payload.get("rvc_eligible") is True
    rvc_eligible = (
        audio_class == "PT_BR_NARRATION"
        and language in {"pt", "pt-br", "pt_br"}
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
