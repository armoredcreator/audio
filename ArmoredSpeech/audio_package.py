from __future__ import annotations

"""Create a complete, fail-closed Speech-owned audio package for one item.

This module is used only by the isolated lab's whole-batch runner. It does not
publish, mutate the original video, or invoke ArmoredStudio.
"""

import hashlib
import json
import os
import shutil
import subprocess
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AudioPackageError(RuntimeError):
    """Audio package could not be prepared or validated safely."""


@dataclass(frozen=True, slots=True)
class WaveMetadata:
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve(strict=True).relative_to(Path(root).resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _tool(name: str, env_name: str) -> str:
    configured = os.getenv(env_name, "").strip()
    candidate = configured or shutil.which(name)
    if not candidate:
        raise AudioPackageError(f"{name.upper()}_NOT_FOUND")
    path = Path(candidate).expanduser()
    if path.is_absolute() and not path.is_file():
        raise AudioPackageError(f"{name.upper()}_NOT_FOUND: {path}")
    return str(path)


def _run(command: list[str], label: str) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(os.getenv("ARMORED_SPEECH_AUDIO_TIMEOUT", "7200")),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioPackageError(f"{label}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-1800:]
        raise AudioPackageError(f"{label} (exit={result.returncode}): {detail}")


def _wave_metadata(path: Path) -> WaveMetadata:
    try:
        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            rate = stream.getframerate()
            frames = stream.getnframes()
            width = stream.getsampwidth()
            if channels < 1 or rate < 1 or frames < 1 or width < 1:
                raise AudioPackageError(f"Invalid WAV stream: {path}")
            if not stream.readframes(1):
                raise AudioPackageError(f"Unreadable WAV data: {path}")
            return WaveMetadata(
                sample_rate=rate,
                channels=channels,
                frames=frames,
                duration_seconds=frames / rate,
            )
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioPackageError(f"Expected a readable PCM WAV: {path}") from exc


def _media_duration(path: Path, ffprobe: str) -> float:
    command = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=int(os.getenv("ARMORED_SPEECH_AUDIO_TIMEOUT", "7200")),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioPackageError(f"FFPROBE_DURATION_FAILED: {exc}") from exc
    if result.returncode:
        raise AudioPackageError(
            "FFPROBE_DURATION_FAILED: " + (result.stderr or "").strip()[-1200:]
        )
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise AudioPackageError("Invalid video duration from ffprobe") from exc
    if not (duration > 0.0):
        raise AudioPackageError("Original video duration must be positive")
    return duration


def _validate_v1_directive(payload: dict[str, Any], *, root: Path, item) -> tuple[Path, Path, str, bool]:
    if not isinstance(payload, dict) or payload.get("contract_version") != 1:
        raise AudioPackageError("Expected validated Speech handoff v1 directive")
    if payload.get("status") not in (None, "READY"):
        raise AudioPackageError("Speech directive is not READY")
    if (
        str(payload.get("source_id")) != str(item.source_id)
        or str(payload.get("item_id")) != str(item.telegram_message_id)
    ):
        raise AudioPackageError("Speech directive identity does not match batch item")
    try:
        original = Path(payload["original_path"]).resolve(strict=True)
        source_wav = Path(payload["audio_artifact_path"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise AudioPackageError("Speech original/WAV path is unavailable") from exc
    expected_original = Path(item.original_path).resolve(strict=True)
    if original != expected_original or not original.is_file():
        raise AudioPackageError("Speech original does not match the canonical batch original")
    if not _inside(original, root) or not _inside(source_wav, root):
        raise AudioPackageError("Speech original and WAV must remain inside the lab root")
    if not source_wav.is_file() or source_wav.stat().st_size <= 44:
        raise AudioPackageError("Speech reference WAV is missing or empty")
    declared_before = payload.get("original_sha256_before")
    declared_after = payload.get("original_sha256_after")
    actual = sha256_file(original)
    if (
        not isinstance(declared_before, str)
        or not isinstance(declared_after, str)
        or declared_before.lower() != declared_after.lower()
        or declared_before.lower() != actual
    ):
        raise AudioPackageError("Original SHA-256 evidence is missing or inconsistent")
    _wave_metadata(source_wav)
    audio_class = str(payload.get("audio_class") or "")
    supported = {
        "PT_BR_NARRATION", "PT_BR_NARRATION_WITH_MUSIC", "MUSIC_ONLY",
        "NO_AUDIO", "NOISE", "NO_USABLE_NARRATION", "FOREIGN_SPEECH",
    }
    if audio_class not in supported:
        raise AudioPackageError(f"Unsupported audio class: {audio_class!r}")
    language = str(payload.get("language") or "").strip().lower().replace("_", "-")
    declared_rvc = payload.get("rvc_eligible") is True
    rvc_allowed = (
        audio_class == "PT_BR_NARRATION"
        and language in {"pt", "pt-br"}
        and payload.get("speech_detected") is True
        and payload.get("music_detected") is not True
        and declared_rvc
    )
    if declared_rvc and not rvc_allowed:
        raise AudioPackageError("Speech declared unsafe RVC eligibility")
    return original, source_wav, actual, rvc_allowed


def prepare_audio_package(
    payload: dict[str, Any],
    item,
    *,
    workspace_root: Path,
    effect_source_path: Path,
    output_dir: Path,
    voice: str = "melody",
) -> dict[str, Any]:
    """Prepare and validate all audio tracks before the Studio phase begins.

    The output directory must be new. A failed attempt leaves no partially
    published package; a successfully returned package is retained through
    Studio completion for safe retry/audit.
    """
    root = Path(workspace_root).resolve(strict=True)
    final_dir = Path(output_dir).resolve()
    if not _inside(final_dir.parent, root):
        raise AudioPackageError("Package output must be inside the lab root")
    if final_dir.exists():
        raise AudioPackageError(f"Package output already exists: {final_dir}")
    effect_source = Path(effect_source_path).resolve(strict=True)
    if not effect_source.is_file() or effect_source.stat().st_size <= 44:
        raise AudioPackageError("Project sound-effect asset is missing or empty")
    if not _inside(effect_source, root):
        raise AudioPackageError("Project sound-effect asset must be inside the lab root")

    original, source_wav, original_hash, rvc_allowed = _validate_v1_directive(
        payload, root=root, item=item
    )
    ffmpeg = _tool("ffmpeg", "ARMORED_FFMPEG")
    ffprobe = _tool("ffprobe", "ARMORED_FFPROBE")
    video_duration = _media_duration(original, ffprobe)
    effect_source_hash = sha256_file(effect_source)

    final_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = final_dir.with_name(f".{final_dir.name}.tmp-{uuid.uuid4().hex}")
    stage.mkdir(parents=False, exist_ok=False)
    try:
        voice_output = stage / "voice.wav"
        effect_output = stage / "effect.wav"
        route = "RVC_MELODY" if rvc_allowed else "EFFECT_ONLY"
        rvc_model = voice if rvc_allowed else None
        rvc_input = stage / "rvc_input.wav"
        rvc_raw = stage / "rvc_raw.wav"

        if rvc_allowed:
            # Preserve the pre-RVC conditioning used by the existing Studio path.
            _run([
                ffmpeg, "-nostdin", "-y", "-i", str(source_wav), "-vn",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(rvc_input),
            ], "RVC_INPUT_PREPARATION_FAILED")
            from armored_core.audio.rvc import converter_voz

            converter_voz(rvc_input, rvc_raw, voice, item_id=item.content_id)
            _run([
                ffmpeg, "-nostdin", "-y", "-i", str(rvc_raw), "-vn",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(voice_output),
            ], "RVC_OUTPUT_NORMALIZATION_FAILED")
        else:
            # Never fall back to the source narration when the Speech decision
            # disallows Melody; create deterministic silence for the video span.
            _run([
                ffmpeg, "-nostdin", "-y", "-f", "lavfi", "-i",
                "anullsrc=r=44100:cl=stereo", "-t", f"{video_duration:.6f}",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(voice_output),
            ], "SILENT_TRACK_CREATION_FAILED")

        # Speech owns effect preparation and gain. Studio v2 must not loudnorm
        # or apply volume again; it may only loop, fade and align the track.
        _run([
            ffmpeg, "-nostdin", "-y", "-i", str(effect_source), "-vn",
            "-af", "loudnorm=I=-12:LRA=7:TP=-1,volume=0.8",
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(effect_output),
        ], "EFFECT_TRACK_PREPARATION_FAILED")

        if sha256_file(original) != original_hash:
            raise AudioPackageError("Original SHA-256 changed while preparing audio")
        if sha256_file(effect_source) != effect_source_hash:
            raise AudioPackageError("Project sound-effect asset changed during preparation")

        voice_meta = _wave_metadata(voice_output)
        effect_meta = _wave_metadata(effect_output)
        if voice_meta.sample_rate != 44100 or voice_meta.channels != 2:
            raise AudioPackageError("Voice track must be canonical PCM WAV 44100 Hz stereo")
        if effect_meta.sample_rate != 44100 or effect_meta.channels != 2:
            raise AudioPackageError("Effect track must be canonical PCM WAV 44100 Hz stereo")

        package = {
            "contract_version": 2,
            "status": "READY",
            "source_id": str(item.source_id),
            "item_id": str(item.telegram_message_id),
            "content_id": str(item.content_id),
            "original_path": str(original),
            "original_sha256_before": original_hash,
            "original_sha256_after": original_hash,
            "audio_class": payload["audio_class"],
            "language": payload.get("language"),
            "speech_detected": payload.get("speech_detected") is True,
            "music_detected": payload.get("music_detected"),
            "confidence": payload.get("confidence"),
            "route": route,
            "rvc_eligible": rvc_allowed,
            "rvc_model": rvc_model,
            "voice_track": {
                "path": str(final_dir / "voice.wav"),
                "sha256": sha256_file(voice_output),
                "format": "wav-pcm-s16le",
                "sample_rate": voice_meta.sample_rate,
                "channels": voice_meta.channels,
                "duration_seconds": round(voice_meta.duration_seconds, 6),
            },
            "effect_track": {
                "path": str(final_dir / "effect.wav"),
                "sha256": sha256_file(effect_output),
                "source_sha256": effect_source_hash,
                "format": "wav-pcm-s16le",
                "sample_rate": effect_meta.sample_rate,
                "channels": effect_meta.channels,
                "duration_seconds": round(effect_meta.duration_seconds, 6),
                "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
            },
            "prepared_video_duration_seconds": round(video_duration, 6),
            "package_id": final_dir.name,
        }
        (stage / "package.json").write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # The package declares final_dir paths, so the directory is atomically
        # promoted only after all tracks, hashes and metadata validate.
        stage.replace(final_dir)
        return package
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
