from __future__ import annotations

"""ArmoredSpeech-owned audio timeline policy and filter-graph builder.

Speech prepares and validates the voice/effect tracks and builds the audio
subgraph. The visual renderer combines this subgraph with its visual graph for
one final audiovisual FFmpeg render; Studio does not create audio policy.
"""

import hashlib
import json
import math
import os
import wave
from pathlib import Path
from typing import Any

import numpy as np


class AudioTimelineError(ValueError):
    """Invalid audio timeline request."""


def visual_plan_digest(plan: dict[str, Any] | None) -> str:
    """Digest the visual/timeline facts Speech used to prepare its audio graph."""
    raw = json.dumps(
        plan or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=os.fspath
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def detect_voice_onset_seconds(
    wav_path: Path,
    *,
    threshold_db: float = -20.0,
    block_milliseconds: int = 20,
) -> float:
    """Find the first audible speech-like block in Speech's existing PCM WAV.

    This replaces the old Studio banner-audio detector without running FFmpeg:
    it examines 20 ms mono RMS windows and returns the first block above -20 dB.
    A genuinely silent/unusable-audio sample returns zero; an invalid WAV fails
    loudly so Speech cannot silently invent timing from unreadable media.
    """
    path = Path(wav_path).resolve(strict=True)
    try:
        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            rate = stream.getframerate()
            width = stream.getsampwidth()
            frames_total = stream.getnframes()
            if channels < 1 or rate < 1 or width != 2:
                raise AudioTimelineError("Banner timing requires readable 16-bit PCM WAV")
            frames_per_block = max(1, int(round(rate * block_milliseconds / 1000.0)))
            frame_offset = 0
            while frame_offset < frames_total:
                raw = stream.readframes(frames_per_block)
                samples = np.frombuffer(raw, dtype="<i2")
                if samples.size == 0:
                    break
                usable_frames = samples.size // channels
                if usable_frames < frames_per_block:
                    break
                if channels > 1:
                    samples = samples[:usable_frames * channels].reshape(-1, channels).mean(axis=1)
                samples = samples.astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(samples * samples)))
                if rms >= 1e-5:
                    db = 20.0 * math.log10(rms)
                    if db > threshold_db:
                        return round(frame_offset / rate, 6)
                frame_offset += usable_frames
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioTimelineError(f"Could not read Speech banner-timing WAV: {path}") from exc
    return 0.0


def build_audio_filter_graph(
    *,
    visual_plan: dict[str, Any] | None,
    audio_start_seconds: float = 0.0,
    main_duration_seconds: float,
    intro: bool,
    intro_position: str,
) -> dict[str, Any]:
    """Build the Speech-owned audio graph for the final FFmpeg render.

    Fixed input contract for the final render:
      [1:a] = RVC-prepared voice WAV, or a generated silence input for EFFECT_ONLY
      [2:a] = Speech-prepared project effect WAV (normalized/gain applied once)

    The visual renderer only concatenates this graph with its visual graph; it
    does not choose routes, filters, gain, trim policy, fades, or mix behavior.
    """
    plan = visual_plan or {}
    try:
        audio_start = float(audio_start_seconds)
        video_start = float(plan.get("video_start", 0) or 0)
        main_duration = float(main_duration_seconds)
    except (TypeError, ValueError) as exc:
        raise AudioTimelineError("Speech audio/video timeline is invalid") from exc
    if (
        not math.isfinite(audio_start) or audio_start < 0
        or not math.isfinite(video_start) or video_start < 0
        or not math.isfinite(main_duration) or main_duration <= 0
    ):
        raise AudioTimelineError("Audio/video start and main duration must be finite and valid")
    if intro and intro_position not in {"inicio", "final"}:
        raise AudioTimelineError("intro_position must be 'inicio' or 'final'")

    # Canonicalize the voice stream first. Trimming is done here (Speech's
    # policy) rather than by Studio, and the project effect always starts at 0.
    voice_filters = [
        "aresample=44100",
        "aformat=sample_fmts=s16:channel_layouts=stereo",
    ]
    if audio_start > 0:
        voice_filters.append(f"atrim=start={audio_start:g}:duration={main_duration:g}")
    else:
        voice_filters.append(f"atrim=duration={main_duration:g}")
    # RVC output can be slightly shorter than the visual timeline, especially
    # after an audio-start trim. Pad then trim to the exact main-video duration.
    voice_filters.extend([
        f"apad=whole_dur={main_duration:g}",
        f"atrim=duration={main_duration:g}",
        "asetpts=PTS-STARTPTS",
    ])
    graph = "[1:a]" + ",".join(voice_filters) + "[voice_main]"

    if intro:
        graph += ";[2:a]asplit=2[effect_main][effect_intro]"
    else:
        graph += ";[2:a]anull[effect_main]"

    graph += (
        ";[voice_main][effect_main]"
        "amix=inputs=2:duration=first:dropout_transition=2[main_a]"
    )
    output_label = "[main_a]"
    if intro:
        graph += (
            ";[effect_intro]afade=t=in:st=0:d=0.4,"
            "afade=t=out:st=1.5:d=0.5,"
            "atrim=duration=2.0,asetpts=PTS-STARTPTS[intro_a]"
        )
        order = (
            "[intro_a][main_a]"
            if intro_position == "inicio"
            else "[main_a][intro_a]"
        )
        graph += f";{order}concat=n=2:v=0:a=1[final_a]"
        output_label = "[final_a]"

    return {
        "filter_graph": graph,
        "filter_graph_sha256": hashlib.sha256(graph.encode("utf-8")).hexdigest(),
        "output_label": output_label,
        "visual_plan_sha256": visual_plan_digest({"video_start": float(video_start)}),
        "audio_start_seconds": round(audio_start, 6),
        "video_start_seconds": round(video_start, 6),
        "main_duration_seconds": round(main_duration, 6),
        "intro_enabled": bool(intro),
        "intro_position": intro_position if intro else None,
        "intro_duration_seconds": 2.0 if intro else 0.0,
        "composition_owner": "ArmoredSpeech",
        "effect_gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        # Speech authors these final audio encoding/input options. Studio only
        # translates them into FFmpeg argv during the single final render.
        "render_options": {
            "codec": "aac",
            "bitrate": "192k",
            "sample_rate": 44100,
            "channels": 2,
            "effect_input_loop": True,
        },
    }


__all__ = [
    "AudioTimelineError",
    "build_audio_filter_graph",
    "detect_voice_onset_seconds",
    "visual_plan_digest",
]
