from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

from .contracts import AudioClass, SpeechDecision


class ManifestProcessor:
    """Experimental processor driven by explicit reviewed labels.

    This is deliberately not advertised as automatic classification. It lets
    the bulk/storage/SQLite contract be tested without inventing model outputs.
    Replace this provider only after a model-backed classifier is validated.
    """

    def __init__(
        self,
        manifest_path: Path,
        *,
        model: str = "UVR-MDX-NET-Voc_FT.onnx",
        timeout_seconds: int = 7200,
    ) -> None:
        self.manifest_path = manifest_path.resolve()
        self.model = model
        self.timeout_seconds = timeout_seconds
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Manifest must be a JSON array of item decisions")
        self.decisions: dict[tuple[str, str], dict[str, Any]] = {}
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError("Each manifest entry must be a JSON object")
            key = (str(row.get("source_id", "")), str(row.get("item_id", "")))
            if not all(key):
                raise ValueError("Every manifest entry needs source_id and item_id")
            if key in self.decisions:
                raise ValueError(f"Duplicate manifest entry: {key}")
            self.decisions[key] = row

    def analyze(self, video_path: Path, source_id: str | None = None, item_id: str | None = None) -> SpeechDecision:
        # The runner's protocol currently supplies only video_path; resolve the
        # row by its normalized path to avoid guessing by filename alone.
        matches = [
            row for row in self.decisions.values()
            if Path(str(row.get("video_path", ""))).expanduser().resolve() == video_path.resolve()
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one manifest decision for {video_path}; found {len(matches)}"
            )
        row = matches[0]
        decision = SpeechDecision(
            audio_class=AudioClass(str(row["audio_class"])),
            language=row.get("language"),
            speech_detected=bool(row.get("speech_detected", False)),
            music_detected=row.get("music_detected"),
            confidence=None if row.get("confidence") is None else float(row["confidence"]),
            rationale=str(row.get("rationale", "Reviewed local manifest label")),
        )
        decision.validate()
        return decision

    def prepare_wav(self, video_path: Path, wav_path: Path, decision: SpeechDecision) -> None:
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("FFMPEG_NOT_FOUND")

        with tempfile.TemporaryDirectory(prefix="armoredspeech-") as temporary:
            temp_dir = Path(temporary)
            raw_wav = temp_dir / "source.wav"
            if decision.needs_vocal_separation:
                separator = shutil.which("audio-separator")
                if separator is None:
                    raise RuntimeError("AUDIO_SEPARATOR_NOT_FOUND")
                output_dir = temp_dir / "separated"
                output_dir.mkdir()
                extracted = subprocess.run(
                    [ffmpeg, "-nostdin", "-y", "-i", str(video_path), "-vn",
                     "-ac", "2", "-ar", "44100", str(raw_wav)],
                    capture_output=True, text=True, timeout=self.timeout_seconds,
                )
                if extracted.returncode != 0:
                    raise RuntimeError(f"FFMPEG_EXTRACT_FAILED: {extracted.stderr[-1200:]}")
                separated = subprocess.run(
                    [separator, str(raw_wav), "--model_filename", self.model,
                     "--output_dir", str(output_dir), "--output_format", "WAV"],
                    capture_output=True, text=True, timeout=self.timeout_seconds,
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
                vocal_source = candidates[0]
                normalized = subprocess.run(
                    [ffmpeg, "-nostdin", "-y", "-i", str(vocal_source),
                     "-vn", "-ac", "1", "-ar", "16000", str(wav_path)],
                    capture_output=True, text=True, timeout=self.timeout_seconds,
                )
                if normalized.returncode != 0:
                    raise RuntimeError(f"WAV_NORMALIZATION_FAILED: {normalized.stderr[-1200:]}")
                return

            # For all non-RVC classes, keep a local extracted track for diagnosis
            # and Studio metadata; Studio must ignore it for RVC and mute the MP4.
            extracted = subprocess.run(
                [ffmpeg, "-nostdin", "-y", "-i", str(video_path), "-vn",
                 "-ac", "1", "-ar", "16000", str(wav_path)],
                capture_output=True, text=True, timeout=self.timeout_seconds,
            )
            if extracted.returncode == 0 and wav_path.is_file() and wav_path.stat().st_size:
                return
            if decision.audio_class is not AudioClass.NO_AUDIO:
                raise RuntimeError(f"FFMPEG_EXTRACT_FAILED: {extracted.stderr[-1200:]}")
            # A no-audio item still gets a valid silent WAV sidecar so the
            # downstream contract has a deterministic artifact to inspect.
            with wave.open(str(wav_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\x00\x00" * 16000)
