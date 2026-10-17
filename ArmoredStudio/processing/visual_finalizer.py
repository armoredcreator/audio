from __future__ import annotations

"""Single-pass audiovisual renderer using separate visual/audio filter owners.

ArmoredStudio builds the one-crop visual graph. ArmoredSpeech builds the audio
graph and prepares voice/effect inputs. This module joins the two graphs and
invokes FFmpeg once for the final audiovisual render.
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

    # Use exact 9:16 integer multiples and even dimensions/origins for yuv420p.
    # A safe region can itself be a tight 9:16 crop with an odd x/y origin. In
    # that edge case, shifting the crop by one pixel would leave the planner-
    # approved region. Shrink the crop by two units until an even origin fits;
    # never expand beyond the watermark-safe region to force alignment.
    units = min(sw // 9, sh // 16)
    units -= units % 2
    while units >= 2:
        width, height = 9 * units, 16 * units
        min_x, max_x = sx, sx + sw - width
        min_y, max_y = sy, sy + sh - height

        first_even_x = min_x if min_x % 2 == 0 else min_x + 1
        last_even_x = max_x if max_x % 2 == 0 else max_x - 1
        first_even_y = min_y if min_y % 2 == 0 else min_y + 1
        last_even_y = max_y if max_y % 2 == 0 else max_y - 1

        if first_even_x <= last_even_x and first_even_y <= last_even_y:
            center_x_twice = 2 * sx + sw - width
            center_y_twice = 2 * sy + sh - height
            x_candidates = {
                first_even_x,
                last_even_x,
                (center_x_twice // 4) * 2,
                (center_x_twice // 4) * 2 + 2,
            }
            y_candidates = {
                first_even_y,
                last_even_y,
                (center_y_twice // 4) * 2,
                (center_y_twice // 4) * 2 + 2,
            }
            valid_x = [v for v in x_candidates if first_even_x <= v <= last_even_x]
            valid_y = [v for v in y_candidates if first_even_y <= v <= last_even_y]
            x = min(valid_x, key=lambda v: (abs(2 * v - center_x_twice), v))
            y = min(valid_y, key=lambda v: (abs(2 * v - center_y_twice), v))

            if (
                x >= sx and y >= sy
                and x + width <= sx + sw
                and y + height <= sy + sh
                and x % 2 == 0 and y % 2 == 0
            ):
                return x, y, width, height

        units -= 2

    raise ValueError("Área segura pequena demais para alinhar um crop 9:16 dentro da região segura")


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
        f"[3:v]scale={STORY_WIDTH}:{STORY_HEIGHT}[banner];"
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


def render_video_with_speech_tracks(
    video,
    voice_track,
    effect_track,
    banner,
    output,
    *,
    audio_filter_graph: str,
    audio_output_label: str,
    audio_render_options: dict[str, Any],
    position: str = "final",
    intro: bool = True,
    plan: dict[str, Any] | None = None,
) -> Path:
    """Render visuals and Speech's audio graph in one final FFmpeg invocation.

    Studio builds only the visual graph/crop. Speech supplies voice/effect inputs
    and the filter subgraph that decides how those inputs are combined/timed.
    The graphs are joined mechanically; no Studio audio policies are introduced.
    """
    video, voice_track, effect_track, banner, output = map(
        Path, (video, voice_track, effect_track, banner, output)
    )
    _validate_file(video, "Vídeo")
    _validate_file(voice_track, "Trilha de voz/silêncio do ArmoredSpeech")
    _validate_file(effect_track, "Efeito sonoro preparado pelo ArmoredSpeech")
    if not isinstance(audio_filter_graph, str) or not audio_filter_graph:
        raise RuntimeError("ArmoredSpeech não forneceu o grafo de áudio")
    if audio_output_label not in {"[main_a]", "[final_a]"}:
        raise RuntimeError("Rótulo de saída de áudio do ArmoredSpeech inválido")
    if (
        not isinstance(audio_render_options, dict)
        or audio_render_options.get("codec") != "aac"
        or not isinstance(audio_render_options.get("bitrate"), str)
        or not audio_render_options["bitrate"].endswith("k")
        or audio_render_options.get("sample_rate") != 44100
        or audio_render_options.get("channels") != 2
        or audio_render_options.get("effect_input_loop") is not True
    ):
        raise RuntimeError("Opções de renderização de áudio do Speech inválidas")
    if intro:
        _validate_file(banner, "Banner")
        if position not in {"inicio", "final"}:
            raise RuntimeError(f"Posição de intro inválida: {position}")
    if output.exists():
        raise FileExistsError(f"Studio nunca deve sobrescrever um resultado existente: {output}")

    width, height, fps = _probe_video(video)
    output.parent.mkdir(parents=True, exist_ok=True)
    visual_graph, video_map = _video_filter_graph(
        width, height, fps, plan, intro=intro, position=position
    )
    # Each domain builds its own graph. This module joins them for one render.
    graph = visual_graph + ";" + audio_filter_graph

    ffmpeg = resolve_executable(
        "ffmpeg", env_var="ARMORED_FFMPEG", project_root=PROJECT_ROOT
    )
    command = [
        str(ffmpeg), "-nostdin", "-n",
        "-i", str(video),
        "-i", str(voice_track),
    ]
    if audio_render_options["effect_input_loop"]:
        command += ["-stream_loop", "-1"]
    command += ["-i", str(effect_track)]
    # Input order: video 0, Speech voice/silence 1, Speech effect 2,
    # optional visual banner 3. The Speech graph references [1:a] and [2:a].
    if intro:
        command += ["-loop", "1", "-i", str(banner)]
    command += [
        "-filter_complex", graph,
        "-map", video_map,
        "-map", audio_output_label,
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
        "-profile:v", VIDEO_PROFILE, "-level:v", VIDEO_LEVEL, "-pix_fmt", VIDEO_PIX_FMT,
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        # These final audio encoding/input settings are specified by Speech.
        "-c:a", str(audio_render_options["codec"]),
        "-b:a", str(audio_render_options["bitrate"]),
        "-ar", str(audio_render_options["sample_rate"]),
        "-ac", str(audio_render_options["channels"]),
        "-shortest", "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(command, **_windows_hidden_kwargs())
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("Erro na renderização audiovisual única do ArmoredStudio.")
    _validate_file(output, "Resultado final")
    return output
