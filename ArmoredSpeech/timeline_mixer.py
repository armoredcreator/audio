from __future__ import annotations

"""ArmoredSpeech-owned audio timeline policy.

This module builds and executes the audio-only FFmpeg graph in ArmoredSpeech.
It never opens video streams. Studio receives only the fully rendered AAC/M4A
track and performs a final visual render with audio stream-copy.
"""

import hashlib
import json
import math
import os
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np


class AudioTimelineError(ValueError):
    """Invalid audio timeline request."""


def visual_plan_digest(plan: dict[str, Any] | None) -> str:
    """Digest the visual/timeline facts Speech used to prepare its audio graph."""
    raw = json.dumps(
        plan or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
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
    voice_filters.append("asetpts=PTS-STARTPTS")
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
        "visual_plan_sha256": visual_plan_digest(plan),
        "audio_start_seconds": round(audio_start, 6),
        "video_start_seconds": round(video_start, 6),
        "main_duration_seconds": round(main_duration, 6),
        "intro_enabled": bool(intro),
        "intro_position": intro_position if intro else None,
        "intro_duration_seconds": 2.0 if intro else 0.0,
        "composition_owner": "ArmoredSpeech",
        "effect_gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
    }


def render_final_audio_track(
    *,
    audio_inputs: list[dict[str, Any]],
    filter_graph: str,
    output_label: str,
    output_path: Path,
    workspace_root: Path,
    ffmpeg: str,
    ffprobe: str,
    expected_duration_seconds: float,
) -> dict[str, Any]:
    """Materialize the complete Speech-owned soundtrack as AAC in an M4A file.

    The graph contract built above uses [1:a] for the first audio input and
    [2:a] for the effect. In this audio-only FFmpeg invocation those become
    [0:a] and [1:a]. No video input is opened or transformed here; the finished
    track is later stream-copied by Studio into its visual-only MP4 render.
    """
    root = Path(workspace_root).resolve(strict=True)
    output = Path(output_path).expanduser().resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise AudioTimelineError("Speech final audio output must remain inside the lab root") from exc
    if output.exists():
        raise AudioTimelineError(f"Speech final audio already exists; refusing to overwrite: {output}")
    if not isinstance(audio_inputs, list) or len(audio_inputs) != 2:
        raise AudioTimelineError("Speech timeline requires exactly two validated input tracks")
    if output_label not in {"[main_a]", "[final_a]"}:
        raise AudioTimelineError("Speech timeline output label is invalid")
    if not filter_graph or len(filter_graph) > 10000:
        raise AudioTimelineError("Speech audio filter graph is missing or too large")
    if not (math.isfinite(expected_duration_seconds) and expected_duration_seconds > 0):
        raise AudioTimelineError("Expected final audio duration must be positive")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(ffmpeg), "-nostdin", "-n"]
    for spec in audio_inputs:
        if not isinstance(spec, dict):
            raise AudioTimelineError("Speech audio input descriptor is invalid")
        kind = spec.get("kind")
        if kind in {"file", "loop_file"}:
            try:
                path = Path(spec["path"]).resolve(strict=True)
            except (KeyError, TypeError, OSError, RuntimeError) as exc:
                raise AudioTimelineError("Speech audio input path is unavailable") from exc
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise AudioTimelineError("Speech audio input escaped the lab root") from exc
            if not path.is_file() or path.stat().st_size <= 44:
                raise AudioTimelineError(f"Speech audio input is empty: {path}")
            if kind == "loop_file":
                command += ["-stream_loop", "-1"]
            command += ["-i", str(path)]
        elif kind == "lavfi":
            source = spec.get("source")
            if source != "anullsrc=r=44100:cl=stereo":
                raise AudioTimelineError("Unsupported Speech silence source")
            try:
                duration = float(spec.get("duration_seconds"))
            except (TypeError, ValueError) as exc:
                raise AudioTimelineError("Speech silence duration is invalid") from exc
            if not (math.isfinite(duration) and duration > 0):
                raise AudioTimelineError("Speech silence duration must be positive")
            command += ["-f", "lavfi", "-t", f"{duration:.6f}", "-i", source]
        else:
            raise AudioTimelineError(f"Unsupported Speech audio input kind: {kind!r}")

    # Replace exact audio input labels without accidentally replacing the
    # newly assigned [1:a] effect label.
    graph = filter_graph.replace("[2:a]", "[__speech_effect_input__]")
    graph = graph.replace("[1:a]", "[0:a]")
    graph = graph.replace("[__speech_effect_input__]", "[1:a]")

    command += [
        "-filter_complex", graph,
        "-map", output_label,
        "-vn", "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart", str(output),
    ]
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
        output.unlink(missing_ok=True)
        raise AudioTimelineError(f"Speech final-audio render failed: {exc}") from exc
    if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 128:
        output.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "").strip()[-1600:]
        raise AudioTimelineError(
            "Speech final-audio render failed" + (f": {detail}" if detail else "")
        )

    probe_command = [
        str(ffprobe), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,duration",
        "-show_entries", "format=format_name,duration", "-of", "json", str(output),
    ]
    try:
        probed = subprocess.run(
            probe_command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        data = json.loads(probed.stdout) if probed.returncode == 0 else {}
        streams = data.get("streams") or []
        fmt = data.get("format") or {}
        stream = streams[0] if streams else {}
        actual_duration = float(stream.get("duration") or fmt.get("duration") or 0)
        sample_rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, json.JSONDecodeError) as exc:
        output.unlink(missing_ok=True)
        raise AudioTimelineError("Could not validate Speech final AAC audio track") from exc

    if (
        probed.returncode != 0
        or stream.get("codec_name") != "aac"
        or sample_rate != 44100
        or channels != 2
        or actual_duration <= 0
        or abs(actual_duration - expected_duration_seconds) > 0.18
    ):
        output.unlink(missing_ok=True)
        raise AudioTimelineError(
            "Speech final AAC track metadata/duration is incompatible: "
            f"codec={stream.get('codec_name')!r}, rate={sample_rate}, channels={channels}, "
            f"duration={actual_duration:.3f}, expected={expected_duration_seconds:.3f}"
        )

    return {
        "path": str(output),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "format": "aac-m4a",
        "codec": "aac",
        "sample_rate": sample_rate,
        "channels": channels,
        "duration_seconds": round(actual_duration, 6),
        "composition_owner": "ArmoredSpeech",
        "size_bytes": output.stat().st_size,
    }

__all__ = [
    "AudioTimelineError",
    "build_audio_filter_graph",
    "detect_voice_onset_seconds",
    "render_final_audio_track",
    "visual_plan_digest",
]
