from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path

from .contracts import AudioClass, SpeechDecision
from .vocal_separation import separate_vocal_stem


def _resolve_ffmpeg() -> str:
    configured = os.getenv("ARMORED_FFMPEG", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        discovered = shutil.which(configured)
        if discovered:
            return discovered
        raise RuntimeError(f"Configured FFmpeg was not found: {configured}")
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("FFMPEG_NOT_FOUND")
    return executable


def _workspace_root_for(video_path: Path) -> Path:
    configured = os.getenv("ARMORED_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    video = Path(video_path).expanduser().resolve()
    return next(
        (
            parent for parent in video.parents
            if (parent / "ArmoredSpeech").is_dir()
            and (parent / "ArmoredStudio").is_dir()
        ),
        Path(__file__).resolve().parents[1],
    )


def prepare_wav_asset(
    video_path: Path,
    wav_path: Path,
    decision: SpeechDecision,
    *,
    model: str = "UVR-MDX-NET-Voc_FT.onnx",
    timeout_seconds: int = 7200,
) -> None:
    """Create a derived WAV and, for mixed speech, a verified vocal-stem receipt.

    The MP4 is never modified. A READY mixed item has a non-silent WAV sidecar
    and a receipt bound to the source hash and separation model.
    """
    video_path = Path(video_path).expanduser().resolve(strict=True)
    wav_path = Path(wav_path).expanduser().resolve()
    wav_path.parent.mkdir(parents=True, exist_ok=True)

    if decision.needs_vocal_separation:
        separate_vocal_stem(
            video_path,
            output_wav=wav_path,
            workspace_root=_workspace_root_for(video_path),
            model_filename=model,
            timeout_seconds=timeout_seconds,
        )
        return

    ffmpeg = _resolve_ffmpeg()
    extracted = subprocess.run(
        [
            ffmpeg, "-nostdin", "-y", "-i", str(video_path), "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    if extracted.returncode == 0 and wav_path.is_file() and wav_path.stat().st_size > 44:
        return
    if decision.audio_class is not AudioClass.NO_AUDIO:
        raise RuntimeError(f"FFMPEG_EXTRACT_FAILED: {(extracted.stderr or '')[-1200:]}")

    # A no-audio item still receives a deterministic silent WAV sidecar.
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
