from __future__ import annotations

"""Validated, lab-confined vocal separation used by ArmoredSpeech."""

import json
import os
from pathlib import Path

from armored_core.path_utils import path_is_within
import shutil
import subprocess
import sys
import tempfile
import wave
from typing import Any

import numpy as np
from armored_core.integrity import sha256_file as _shared_sha256_file


class VocalSeparationError(RuntimeError):
    """Vocal separation was unavailable or did not produce a valid stem."""


def _sha256(path: Path) -> str:
    return _shared_sha256_file(path)


def _inside(path: Path, root: Path) -> bool:
    """Keep the module's established API while sharing root-boundary checks."""
    return path_is_within(path, root, strict_path=False)


def _resolve_executable(name: str, env_var: str) -> str:
    configured = os.getenv(env_var, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        discovered = shutil.which(configured)
        if discovered:
            return discovered
        raise VocalSeparationError(f"{env_var} points to an unavailable executable: {configured}")
    discovered = shutil.which(name)
    if discovered:
        return discovered
    # Direct execution via .venv\\Scripts\\python.exe does not necessarily
    # activate that environment on PATH. Look beside the active interpreter.
    sibling_name = f"{name}.exe" if os.name == "nt" else name
    sibling = Path(sys.executable).resolve().parent / sibling_name
    if sibling.is_file():
        return str(sibling)
    raise VocalSeparationError(
        f"{name} is unavailable. Configure {env_var} or install the optional "
        "ArmoredSpeech separation dependency."
    )


def _wav_metrics(path: Path) -> dict[str, float | int]:
    """Read PCM WAV metadata and calculate non-silence evidence."""
    try:
        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            sample_width = stream.getsampwidth()
            sample_rate = stream.getframerate()
            frame_count = stream.getnframes()
            if channels < 1 or sample_rate < 1 or sample_width != 2 or frame_count <= 0:
                raise VocalSeparationError(f"Expected non-empty 16-bit PCM WAV: {path}")
            sum_squares = 0.0
            sample_count = 0
            peak = 0
            while True:
                raw = stream.readframes(max(1, sample_rate))
                if not raw:
                    break
                samples = np.frombuffer(raw, dtype="<i2")
                if samples.size == 0:
                    continue
                values = samples.astype(np.float64)
                sum_squares += float(np.dot(values, values))
                sample_count += int(samples.size)
                peak = max(peak, int(np.max(np.abs(samples.astype(np.int32)))))
    except (wave.Error, EOFError, OSError) as exc:
        raise VocalSeparationError(f"Could not read PCM WAV: {path}") from exc
    rms = (sum_squares / sample_count) ** 0.5 / 32768.0 if sample_count else 0.0
    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "duration_seconds": frame_count / sample_rate,
        "rms": float(rms),
        "peak": float(peak / 32768.0),
    }


def _receipt_path(vocal_wav: Path) -> Path:
    return Path(vocal_wav).with_suffix(".separation.json")


def validate_vocal_separation_receipt(
    source_video: Path,
    vocal_wav: Path,
    *,
    workspace_root: Path,
) -> dict[str, Any] | None:
    """Return verified receipt metadata, or None if it is absent, stale, or invalid."""
    try:
        root = Path(workspace_root).resolve(strict=True)
        source = Path(source_video).resolve(strict=True)
        vocal = Path(vocal_wav).resolve(strict=True)
        receipt = _receipt_path(vocal)
        if (
            not _inside(source, root)
            or not _inside(vocal, root)
            or not _inside(receipt, root)
            or not source.is_file()
            or not vocal.is_file()
        ):
            return None
        data = json.loads(receipt.read_text(encoding="utf-8-sig"))
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or data.get("status") != "READY"
            or data.get("source_video_path") != str(source)
            or data.get("vocal_path") != str(vocal)
            or data.get("source_video_sha256") != _sha256(source)
            or data.get("vocal_sha256") != _sha256(vocal)
            or not isinstance(data.get("model_filename"), str)
            or not data["model_filename"].strip()
        ):
            return None
        metrics = _wav_metrics(vocal)
        if (
            metrics["peak"] <= 0
            or metrics["rms"] < 1e-5
            or abs(float(data.get("duration_seconds", -1)) - float(metrics["duration_seconds"])) > 0.02
        ):
            return None
        return {**data, "verified": True, "vocal_metrics": metrics}
    except (OSError, ValueError, TypeError, json.JSONDecodeError, VocalSeparationError):
        return None


def separate_vocal_stem(
    source_video: Path,
    *,
    output_wav: Path,
    workspace_root: Path,
    model_filename: str | None = None,
    timeout_seconds: int = 7200,
) -> tuple[Path, dict[str, Any]]:
    """Separate vocals from source video and publish a verified mono 16 kHz PCM WAV."""
    root = Path(workspace_root).resolve(strict=True)
    source = Path(source_video).resolve(strict=True)
    target = Path(output_wav).expanduser().resolve()
    if not source.is_file() or not _inside(source, root):
        raise VocalSeparationError(f"Source video must exist inside the lab root: {source}")
    if not _inside(target, root) or target == source or target.suffix.lower() != ".wav":
        raise VocalSeparationError(f"Vocal output must be a WAV inside the lab root: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not _inside(target.parent, root):
        raise VocalSeparationError("Vocal output parent escaped the lab root")

    ffmpeg = _resolve_executable("ffmpeg", "ARMORED_FFMPEG")
    separator = _resolve_executable("audio-separator", "ARMORED_AUDIO_SEPARATOR")
    model = (model_filename or os.getenv(
        "ARMORED_SPEECH_SEPARATION_MODEL", "UVR-MDX-NET-Voc_FT.onnx"
    )).strip()
    if not model:
        raise VocalSeparationError("Separation model filename cannot be empty")

    source_hash_before = _sha256(source)
    # Prefer the separator's existing model cache. A custom cache is opt-in so
    # an older Speech run does not trigger an unnecessary multi-GB model download.
    configured_model_dir = os.getenv("ARMORED_SPEECH_SEPARATION_MODEL_DIR", "").strip()
    model_dir = Path(configured_model_dir).expanduser().resolve() if configured_model_dir else None
    if model_dir is not None and not model_dir.is_dir():
        raise VocalSeparationError(
            "ARMORED_SPEECH_SEPARATION_MODEL_DIR must point to an existing model cache directory"
        )

    def run(command: list[str], label: str) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VocalSeparationError(f"{label}: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "")[-1600:]
            raise VocalSeparationError(f"{label}: {detail}")
        return result

    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{target.stem}.separation-",
            dir=str(target.parent),
        ) as temporary:
            stage = Path(temporary)
            raw_wav = stage / "source-audio.wav"
            stems_dir = stage / "stems"
            stems_dir.mkdir()
            normalized = stage / "vocal-16k-mono.wav"

            run([
                ffmpeg, "-nostdin", "-y", "-i", str(source), "-vn",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(raw_wav),
            ], "FFMPEG_AUDIO_EXTRACTION_FAILED")
            raw_metrics = _wav_metrics(raw_wav)
            raw_hash = _sha256(raw_wav)

            separation_command = [separator, str(raw_wav), "--model_filename", model]
            if model_dir is not None:
                separation_command.extend(["--model_file_dir", str(model_dir)])
            separation_command.extend(["--output_dir", str(stems_dir), "--output_format", "WAV"])
            run(separation_command, "VOCAL_SEPARATION_FAILED")

            vocal_candidates = sorted(
                p for p in stems_dir.rglob("*")
                if p.is_file() and p.suffix.lower() == ".wav"
                and "vocal" in p.name.casefold() and p.stat().st_size > 44
            )
            if len(vocal_candidates) != 1:
                raise VocalSeparationError(
                    f"EXPECTED_ONE_VOCALS_STEM_FOUND_{len(vocal_candidates)}; refusing to guess"
                )
            selected_stem = vocal_candidates[0]
            stem_metrics = _wav_metrics(selected_stem)
            allowed_delta = max(0.75, float(raw_metrics["duration_seconds"]) * 0.05)
            if abs(float(stem_metrics["duration_seconds"]) - float(raw_metrics["duration_seconds"])) > allowed_delta:
                raise VocalSeparationError("VOCAL_STEM_DURATION_MISMATCH")

            run([
                ffmpeg, "-nostdin", "-y", "-i", str(selected_stem), "-vn",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(normalized),
            ], "VOCAL_STEM_NORMALIZATION_FAILED")
            final_metrics = _wav_metrics(normalized)
            if final_metrics["peak"] <= 0 or final_metrics["rms"] < 1e-5:
                raise VocalSeparationError("VOCAL_STEM_IS_SILENT_OR_NEAR_SILENT")
            if abs(float(final_metrics["duration_seconds"]) - float(raw_metrics["duration_seconds"])) > allowed_delta:
                raise VocalSeparationError("NORMALIZED_VOCAL_DURATION_MISMATCH")
            if _sha256(source) != source_hash_before:
                raise VocalSeparationError("SOURCE_VIDEO_HASH_CHANGED_DURING_SEPARATION")
            normalized.replace(target)

        receipt = {
            "schema_version": 1,
            "status": "READY",
            "source_video_path": str(source),
            "source_video_sha256": source_hash_before,
            "source_audio_sha256": raw_hash,
            "vocal_path": str(target),
            "vocal_sha256": _sha256(target),
            "model_filename": model,
            "duration_seconds": round(float(final_metrics["duration_seconds"]), 6),
            "sample_rate": int(final_metrics["sample_rate"]),
            "channels": int(final_metrics["channels"]),
            "rms": round(float(final_metrics["rms"]), 8),
            "peak": round(float(final_metrics["peak"]), 8),
        }
        receipt_path = _receipt_path(target)
        temporary_receipt = receipt_path.with_name(f".{receipt_path.name}.tmp")
        temporary_receipt.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_receipt, receipt_path)
        verified = validate_vocal_separation_receipt(
            source, target, workspace_root=root
        )
        if verified is None:
            raise VocalSeparationError("VOCAL_SEPARATION_RECEIPT_VALIDATION_FAILED")
        return target, verified
    except VocalSeparationError:
        raise
    except Exception as exc:
        raise VocalSeparationError(f"VOCAL_SEPARATION_FAILED: {exc}") from exc


__all__ = [
    "VocalSeparationError",
    "separate_vocal_stem",
    "validate_vocal_separation_receipt",
]
