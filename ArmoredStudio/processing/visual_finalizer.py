from __future__ import annotations

"""ArmoredStudio visual render + mux of the final ArmoredSpeech soundtrack.

No audio classification, extraction, RVC, track mixing, fades, gain, or timing
filters live in this module. Speech supplies one complete timeline-aligned WAV.
Studio transforms only video frames and maps the supplied audio stream unchanged
into the final MP4 container.
"""

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from .tool_paths import resolve_executable

VIDEO_PRESET = "medium"
VIDEO_CRF = "18"
VIDEO_PROFILE = "high"
VIDEO_LEVEL = "4.0"
VIDEO_PIX_FMT = "yuv420p"
AUDIO_BITRATE = "192k"

INTRO_DURATION = 2.0
INTRO_WIDTH = 1080
INTRO_HEIGHT = 1920
STORY_WIDTH = 1080
STORY_HEIGHT = 1920
BLUR = "boxblur=6:2"
FADE_IN = "fade=t=in:st=0:d=0.6:color=white"
FADE_OUT = "fade=t=out:st=1.7:d=0.6:color=white"
BRIGHTNESS = "0.00"
CONTRAST = "1.10"
SATURATION = "1.12"
GAMMA = "1.00"
UNSHARP = "5:5:0.5:5:5:0.0"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _windows_hidden_kwargs():
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}


def _validate_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{description} não encontrado: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"{description} está vazio: {path}")


def _probe_video(video: Path) -> tuple[int, int, str]:
    ffprobe = resolve_executable("ffprobe", env_var="ARMORED_FFPROBE", project_root=PROJECT_ROOT)
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", str(video)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", **_windows_hidden_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError("Não foi possível obter resolução/FPS do vídeo: " + result.stderr.strip())
    streams = json.loads(result.stdout).get("streams") or []
    if not streams:
        raise RuntimeError("Vídeo sem stream de vídeo válido.")
    stream = streams[0]
    return int(stream["width"]), int(stream["height"]), stream.get("r_frame_rate", "30/1")


def _video_plan_filters(plan: dict[str, Any] | None) -> list[str]:
    plan = plan or {}
    filters: list[str] = []
    start = float(plan.get("video_start", 0) or 0)
    if start > 0:
        filters += [f"trim=start={start:g}", "setpts=PTS-STARTPTS"]
    crop = plan.get("crop_final")
    if crop:
        x, y = int(crop.get("x", 0)), int(crop.get("y", 0))
        width, height = int(crop.get("width", 0)), int(crop.get("height", 0))
        if width <= 0 or height <= 0:
            raise ValueError("crop_final inválido.")
        filters.append(f"crop={width}:{height}:{x}:{y}")
    return filters


def _story_filters(width: int, height: int) -> list[str]:
    if width <= 0 or height <= 0:
        raise ValueError("Dimensões do vídeo inválidas.")
    return [
        f"scale={STORY_WIDTH}:{STORY_HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={STORY_WIDTH}:{STORY_HEIGHT}:(iw-{STORY_WIDTH})/2:(ih-{STORY_HEIGHT})/2",
        "setsar=1",
    ]


def _plan_dimensions(plan: dict[str, Any] | None, width: int, height: int) -> tuple[int, int]:
    crop = (plan or {}).get("crop_final")
    return (int(crop.get("width", width)), int(crop.get("height", height))) if crop else (width, height)


def _video_filter_graph(
    video: Path, banner: Path, width: int, height: int, fps: str,
    plan: dict[str, Any] | None, *, intro: bool, position: str,
) -> tuple[str, str]:
    main_filters = _video_plan_filters(plan) + _story_filters(width, height) + [
        f"eq=brightness={BRIGHTNESS}:contrast={CONTRAST}:saturation={SATURATION}:gamma={GAMMA}",
        f"unsharp={UNSHARP}", "setpts=PTS-STARTPTS",
    ]
    main = "[0:v]" + ",".join(main_filters) + "[main_v]"
    if not intro:
        return main, "[main_v]"

    intro_source = _video_plan_filters(plan) + [
        f"trim=duration={INTRO_DURATION}", "setpts=PTS-STARTPTS",
    ]
    intro_graph = (
        ";[0:v]" + ",".join(intro_source) + ","
        f"scale={INTRO_WIDTH}:2133,"
        f"crop={INTRO_WIDTH}:{INTRO_HEIGHT}:x=(iw-{INTRO_WIDTH})/2:y=(ih-{INTRO_HEIGHT})/2,"
        f"{BLUR},{FADE_IN},{FADE_OUT}[intro_bg];"
        f"[2:v]scale={INTRO_WIDTH}:{INTRO_HEIGHT}[banner];"
        f"[intro_bg][banner]overlay=0:0,fps={fps},setpts=PTS-STARTPTS[intro_v0];"
        f"[intro_v0]trim=duration={INTRO_DURATION},setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
        "setsar=1,setpts=PTS-STARTPTS[intro_v]"
    )
    pieces = "[intro_v][main_v]" if position == "inicio" else "[main_v][intro_v]"
    return main + intro_graph + f";{pieces}concat=n=2:v=1:a=0[vout]", "[vout]"


def render_with_speech_audio(
    video,
    final_audio_track,
    banner,
    output,
    *,
    position: str = "final",
    intro: bool = True,
    plan: dict[str, Any] | None = None,
) -> Path:
    """Render the visual timeline and mux one completed Speech soundtrack."""
    video, final_audio_track, banner, output = map(Path, (video, final_audio_track, banner, output))
    _validate_file(video, "Vídeo")
    _validate_file(final_audio_track, "Trilha final preparada pelo ArmoredSpeech")
    if intro:
        _validate_file(banner, "Banner")
        if position not in {"inicio", "final"}:
            raise RuntimeError(f"Posição de intro inválida: {position}")
    if output.exists():
        raise FileExistsError(f"Studio nunca deve sobrescrever um resultado existente: {output}")

    width, height, fps = _probe_video(video)
    width, height = _plan_dimensions(plan, width, height)
    output.parent.mkdir(parents=True, exist_ok=True)
    graph, video_map = _video_filter_graph(
        video, banner, width, height, fps, plan, intro=intro, position=position
    )
    ffmpeg = resolve_executable("ffmpeg", env_var="ARMORED_FFMPEG", project_root=PROJECT_ROOT)
    command = [str(ffmpeg), "-nostdin", "-n", "-i", str(video), "-i", str(final_audio_track)]
    if intro:
        command += ["-loop", "1", "-i", str(banner)]
    command += [
        "-filter_complex", graph,
        "-map", video_map,
        # All audio processing is completed by ArmoredSpeech. Studio only muxes
        # this stream into the MP4 and encodes the container's AAC audio stream.
        "-map", "1:a:0",
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
        "-profile:v", VIDEO_PROFILE, "-level", VIDEO_LEVEL, "-pix_fmt", VIDEO_PIX_FMT,
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(command, **_windows_hidden_kwargs())
    if result.returncode != 0:
        raise RuntimeError("Erro na renderização visual/mux do ArmoredStudio.")
    _validate_file(output, "Resultado final")
    return output
