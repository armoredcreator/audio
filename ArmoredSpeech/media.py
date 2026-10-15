from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from .contracts import AudioClass, SpeechDecision


def prepare_wav_asset(
    video_path: Path,
    wav_path: Path,
    decision: SpeechDecision,
    *,
    model: str = "UVR-MDX-NET-Voc_FT.onnx",
    timeout_seconds: int = 7200,
) -> None:
    """Create the WAV sidecar without touching the source video."""
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFMPEG_NOT_FOUND")

    with tempfile.TemporaryDirectory(prefix="armoredspeech-") as temporary:
        temp_dir = Path(temporary)
        if decision.needs_vocal_separation:
            separator = shutil.which("audio-separator")
            if separator is None:
                raise RuntimeError("AUDIO_SEPARATOR_NOT_FOUND")
            raw_wav = temp_dir / "source.wav"
            output_dir = temp_dir / "separated"
            output_dir.mkdir()
            extracted = subprocess.run(
                [ffmpeg, "-nostdin", "-y", "-i", str(video_path), "-vn",
                 "-ac", "2", "-ar", "44100", str(raw_wav)],
                capture_output=True, text=True, timeout=timeout_seconds,
            )
            if extracted.returncode != 0:
                raise RuntimeError(f"FFMPEG_EXTRACT_FAILED: {extracted.stderr[-1200:]}")
            separated = subprocess.run(
                [separator, str(raw_wav), "--model_filename", model,
                 "--output_dir", str(output_dir), "--output_format", "WAV"],
                capture_output=True, text=True, timeout=timeout_seconds,
            )
            if separated.returncode != 0:
                raise RuntimeError(f"SEPARATION_FAILED: {separated.stderr[-1200:]}")
            candidates = [
                path for path in output_dir.rglob("*.wav")
                if "vocal" in path.name.lower()
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    f"EXPECTED_ONE_VOCALS_STEM_FOUND_{len(candidates)}; refusing to guess"
                )
            normalized = subprocess.run(
                [ffmpeg, "-nostdin", "-y", "-i", str(candidates[0]),
                 "-vn", "-ac", "1", "-ar", "16000", str(wav_path)],
                capture_output=True, text=True, timeout=timeout_seconds,
            )
            if normalized.returncode != 0:
                raise RuntimeError(f"WAV_NORMALIZATION_FAILED: {normalized.stderr[-1200:]}")
            return

        # Non-RVC classes retain a reference WAV for Studio/debugging. Studio
        # must use the SQLite decision, not infer RVC eligibility from file existence.
        extracted = subprocess.run(
            [ffmpeg, "-nostdin", "-y", "-i", str(video_path), "-vn",
             "-ac", "1", "-ar", "16000", str(wav_path)],
            capture_output=True, text=True, timeout=timeout_seconds,
        )
        if extracted.returncode == 0 and wav_path.is_file() and wav_path.stat().st_size:
            return
        if decision.audio_class is not AudioClass.NO_AUDIO:
            raise RuntimeError(f"FFMPEG_EXTRACT_FAILED: {extracted.stderr[-1200:]}")

        # A no-audio item still receives a deterministic silent WAV sidecar.
        with wave.open(str(wav_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 16000)
