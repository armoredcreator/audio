from __future__ import annotations

"""Visual-only renderer that muxes Speech's completed AAC track by stream copy.

ArmoredStudio owns video crop/framing, VEO/Gemini/bar/banner corrections and
video encoding. It accepts one final AAC/M4A file from ArmoredSpeech, maps that
track directly, and never extracts, mixes, filters, normalizes, or re-encodes audio.
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

INTRO_DURATION = 2.0
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
        [
            str(ffprobe), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", str(video),
        ],
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


def _single_portrait_crop(
    plan: dict[str, Any] | None,
    source_width: int,
    source_height: int,
) -> tuple[int, int, int, int]:
    """Combine planner safety crop + 9:16 framing into one source-frame crop."""
    if source_width <= 0 or source_height <= 0:
        raise ValueError("Dimensões do vídeo inválidas")
    crop = (plan or {}).get("crop_final")
    if crop:
        sx, sy = int(crop["x"]), int(crop["y"])
        sw, sh = int(crop["width"]), int(crop["height"])
    else:
        sx, sy, sw, sh = 0, 0, source_width, source_height

    if sx < 0 or sy < 0 or sw <= 0 or sh <= 0 or sx + sw > source_width or sy + sh > source_height:
        raise ValueError("crop_final inválido ou fora do vídeo")

    # Exact integer multiples of 9x16 yield a portrait source crop that can be
    # scaled to 1080x1920 without a second crop. Even multiples also satisfy
    # common yuv420p chroma alignment constraints.
    units = min(sw // 9, sh // 16)
    units -= units % 2
    if units < 2:
        raise ValueError("Área segura pequena demais para formar um crop 9:16")
    width, height = 9 * units, 16 * units
    x = sx + (sw - width) // 2
    y = sy + (sh - height) // 2

    # Prefer even origins while remaining inside the planner-approved safe area.
    if x % 2:
        x = x + 1 if x + width <= sx + sw else x - 1
    if y % 2:
        y = y + 1 if y + height <= sy + sh else y - 1
    if x < sx or y < sy or x + width > sx + sw or y + height > sy + sh:
        raise ValueError("Não foi possível alinhar o crop final dentro da região segura")
    return x, y, width, height


def _plan_dimensions(plan: dict[str, Any] | None, width: int, height: int) -> tuple[int, int]:
    crop = (plan or {}).get("crop_final")
    if not crop:
        return width, height
    return int(crop.get("width", width)), int(crop.get("height", height))


def _video_filter_graph(
    source_width: int,
    source_height: int,
    fps: str,
    plan: dict[str, Any] | None,
    *,
    intro: bool,
    position: str,
) -> tuple[str, str]:
    """Build one crop on the source, then branch the cropped frames for intro/main."""
    plan = plan or {}
    x, y, crop_width, crop_height = _single_portrait_crop(
        plan, source_width, source_height
    )
    video_start = float(plan.get("video_start", 0) or 0)
    if video_start < 0:
        raise ValueError("video_start cannot be negative")

    source_filters = []
    if video_start > 0:
        source_filters.extend([f"trim=start={video_start:g}", "setpts=PTS-STARTPTS"])
    source_filters.append(f"crop={crop_width}:{crop_height}:{x}:{y}")

    common = "[0:v]" + ",".join(source_filters)
    if intro:
        common += ",split=2[main_src][intro_src]"
    else:
        common += "[main_src]"

    main = (
        ";[main_src]"
        f"scale={STORY_WIDTH}:{STORY_HEIGHT},setsar=1,"
        f"eq=brightness={BRIGHTNESS}:contrast={CONTRAST}:saturation={SATURATION}:gamma={GAMMA},"
        f"unsharp={UNSHARP},setpts=PTS-STARTPTS[main_v]"
    )
    if not intro:
        return common + main, "[main_v]"
    if position not in {"inicio", "final"}:
        raise ValueError("Intro position must be 'inicio' or 'final'")

    intro_graph = (
        f";[intro_src]trim=duration={INTRO_DURATION},setpts=PTS-STARTPTS,"
        f"scale={STORY_WIDTH}:{STORY_HEIGHT},setsar=1,"
        f"{BLUR},{FADE_IN},{FADE_OUT}[intro_bg];"
        f"[2:v]scale={STORY_WIDTH}:{STORY_HEIGHT}[banner];"
        "[intro_bg][banner]overlay=0:0,"
        f"fps={fps},setpts=PTS-STARTPTS[intro_v0];"
        f"[intro_v0]trim=duration={INTRO_DURATION},setpts=PTS-STARTPTS[intro_v]"
    )
    concat_inputs = (
        "[intro_v][main_v]" if position == "inicio" else "[main_v][intro_v]"
    )
    return (
        common + main + intro_graph
        + f";{concat_inputs}concat=n=2:v=1:a=0[vout]",
        "[vout]",
    )


def render_video_with_speech_track(
    video,
    final_audio_track,
    banner,
    output,
    *,
    position: str = "final",
    intro: bool = True,
    plan: dict[str, Any] | None = None,
) -> Path:
    """Render the visual graph and stream-copy the completed Speech AAC track."""
    video, final_audio_track, banner, output = map(
        Path, (video, final_audio_track, banner, output)
    )
    _validate_file(video, "Vídeo")
    _validate_file(final_audio_track, "Trilha final preparada pelo ArmoredSpeech")
    if final_audio_track.suffix.lower() != ".m4a":
        raise RuntimeError("Studio aceita somente a trilha final AAC/M4A do ArmoredSpeech")
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
        width, height, fps, plan, intro=intro, position=position
    )

    ffmpeg = resolve_executable(
        "ffmpeg", env_var="ARMORED_FFMPEG", project_root=PROJECT_ROOT
    )
    command = [
        str(ffmpeg), "-nostdin", "-n",
        "-i", str(video),
        "-i", str(final_audio_track),
    ]
    # Video is input 0, Speech's final AAC track is input 1, and the optional
    # visual-only banner is input 2. There are no component tracks or audio graph.
    if intro:
        command += ["-loop", "1", "-i", str(banner)]
    command += [
        "-filter_complex", graph,
        "-map", video_map,
        "-map", "1:a:0",
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
        "-profile:v", VIDEO_PROFILE, "-level:v", VIDEO_LEVEL, "-pix_fmt", VIDEO_PIX_FMT,
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        # AAC is already completely prepared by Speech: copy without re-encoding.
        "-c:a", "copy",
        "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(command, **_windows_hidden_kwargs())
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("Erro na renderização visual/mux do ArmoredStudio.")
    _validate_file(output, "Resultado final")
    return output
