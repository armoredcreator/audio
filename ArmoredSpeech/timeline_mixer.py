from __future__ import annotations

"""ArmoredSpeech-owned final audio timeline composition.

Studio provides a validated visual export plan and later muxes this single
completed WAV with the rendered video. RVC, effect gain, fades, audio trimming,
and audio concatenation happen here, not in ArmoredStudio.
"""

import hashlib
import json
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any


class AudioTimelineError(RuntimeError):
    """The final Speech-owned audio timeline could not be prepared safely."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def visual_plan_digest(plan: dict[str, Any] | None) -> str:
    raw = json.dumps(
        plan or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _run(command: list[str]) -> None:
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
        raise AudioTimelineError(f"FINAL_AUDIO_TIMELINE_COMPOSITION_FAILED: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-1800:]
        raise AudioTimelineError(
            "FINAL_AUDIO_TIMELINE_COMPOSITION_FAILED: "
            + (detail or f"ffmpeg exit={result.returncode}")
        )


def _wave_metadata(path: Path) -> tuple[int, int, float]:
    try:
        with wave.open(str(path), "rb") as stream:
            rate = stream.getframerate()
            channels = stream.getnchannels()
            frames = stream.getnframes()
            if (
                rate != 44100 or channels != 2 or stream.getsampwidth() != 2
                or frames < 1 or not stream.readframes(1)
            ):
                raise AudioTimelineError("Final soundtrack must be readable 44.1 kHz stereo PCM WAV")
            return rate, channels, frames / rate
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioTimelineError("Final soundtrack is not a readable PCM WAV") from exc


def prepare_final_audio_track(
    voice_path: Path,
    effect_path: Path,
    output_path: Path,
    *,
    workspace_root: Path,
    visual_plan: dict[str, Any] | None,
    intro: bool,
    intro_position: str,
) -> dict[str, Any]:
    """Produce one complete soundtrack from Speech-prepared voice/effect tracks.

    Preserve the validated v2 rules: trim only voice by audio_start; start the
    main effect at zero; fade a dedicated two-second intro effect; concatenate
    main/intro audio in the selected order. Studio does not receive the component
    tracks and has no need to mix, trim, normalize, fade, or invoke RVC.
    """
    root = Path(workspace_root).resolve(strict=True)
    voice = Path(voice_path).resolve(strict=True)
    effect = Path(effect_path).resolve(strict=True)
    output = Path(output_path).expanduser().resolve()
    for source in (voice, effect):
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise AudioTimelineError("Soundtrack inputs must remain inside the lab root") from exc
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise AudioTimelineError("Final soundtrack must remain inside the lab root") from exc
    if output.exists():
        raise AudioTimelineError(f"Final soundtrack already exists; refusing to overwrite: {output}")
    if intro and intro_position not in {"inicio", "final"}:
        raise AudioTimelineError("Intro position must be 'inicio' or 'final'")

    plan = visual_plan or {}
    try:
        audio_start = float(plan.get("audio_start", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise AudioTimelineError("Visual plan audio_start is invalid") from exc
    if audio_start < 0:
        raise AudioTimelineError("Visual plan audio_start cannot be negative")

    output.parent.mkdir(parents=True, exist_ok=True)
    voice_filters = []
    if audio_start > 0:
        voice_filters = [f"atrim=start={audio_start:g}", "asetpts=PTS-STARTPTS"]
    graph = (
        "[0:a]" + (",".join(voice_filters) + "," if voice_filters else "")
        + "anull[voice];[1:a]anull[music];"
        + "[voice][music]amix=inputs=2:duration=first:dropout_transition=2[main_a]"
    )
    label = "[main_a]"
    if intro:
        graph += (
            ";[1:a]afade=t=in:st=0:d=0.4,afade=t=out:st=1.5:d=0.5,"
            "atrim=duration=2.0,asetpts=PTS-STARTPTS[intro_a]"
        )
        pieces = "[intro_a][main_a]" if intro_position == "inicio" else "[main_a][intro_a]"
        graph += f";{pieces}concat=n=2:v=0:a=1[final_a]"
        label = "[final_a]"

    ffmpeg = os.getenv("ARMORED_FFMPEG", "").strip() or shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioTimelineError("FFMPEG_NOT_FOUND")
    _run([
        ffmpeg, "-nostdin", "-y",
        "-i", str(voice),
        "-stream_loop", "-1", "-i", str(effect),
        "-filter_complex", graph,
        "-map", label, "-vn",
        "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(output),
    ])

    rate, channels, duration = _wave_metadata(output)
    return {
        "path": str(output),
        "sha256": sha256_file(output),
        "format": "wav-pcm-s16le",
        "sample_rate": rate,
        "channels": channels,
        "duration_seconds": round(duration, 6),
        "composition_owner": "ArmoredSpeech",
        "visual_plan_sha256": visual_plan_digest(plan),
        "audio_start_seconds": round(audio_start, 6),
        "intro_enabled": bool(intro),
        "intro_position": intro_position if intro else None,
        "intro_duration_seconds": 2.0 if intro else 0.0,
    }
