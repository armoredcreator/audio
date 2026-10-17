from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ArmoredStudio.processing import visual_finalizer


def _audio_graph(intro: bool = True) -> tuple[str, str]:
    graph = (
        "[1:a]aresample=44100,aformat=sample_fmts=s16:channel_layouts=stereo,"
        "atrim=duration=3,apad=whole_dur=3,atrim=duration=3,asetpts=PTS-STARTPTS[voice];"
        "[2:a]anull[effect_main];"
        "[voice][effect_main]amix=inputs=2:duration=first:dropout_transition=2[main_a]"
    )
    if intro:
        graph += (
            ";[2:a]asplit=2[effect_main_unused][effect_intro];"
            "[effect_intro]afade=t=in:st=0:d=0.4,afade=t=out:st=1.5:d=0.5,"
            "atrim=duration=2.0,asetpts=PTS-STARTPTS[intro_a];"
            "[main_a][intro_a]concat=n=2:v=0:a=1[final_a]"
        )
        # Use a distinct graph label in a real Speech package. The simplified
        # helper only supports the no-intro test graph to avoid duplicate labels.
    return graph, "[main_a]"


def test_visual_graph_crops_once_before_splitting_main_and_intro():
    graph, label = visual_finalizer._video_filter_graph(
        1920, 1080, "30/1", {"video_start": 0.0},
        intro=True, position="final",
    )
    assert label == "[vout]"
    assert graph.count("crop=") == 1
    assert "split=2[main_src][intro_src]" in graph
    assert "concat=n=2:v=1:a=0[vout]" in graph
    assert "atrim=" not in graph
    assert "amix=" not in graph
    assert "afade=" not in graph
    assert "volume=" not in graph
    assert "loudnorm=" not in graph


def test_visual_plan_video_start_and_crop_are_applied_once_before_the_branch():
    graph, _ = visual_finalizer._video_filter_graph(
        1920, 1080, "30/1",
        {
            "video_start": 0.75,
            "crop_final": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        },
        intro=True,
        position="inicio",
    )
    assert graph.count("trim=start=0.75") == 1
    assert graph.count("crop=") == 1
    assert graph.count("split=2[main_src][intro_src]") == 1
    assert "concat=n=2:v=1:a=0[vout]" in graph


def test_no_intro_visual_graph_has_one_crop_and_no_banner_input():
    graph, label = visual_finalizer._video_filter_graph(
        1080, 1920, "30/1", {"video_start": 0.0},
        intro=False, position="final",
    )
    assert label == "[main_v]"
    assert graph.count("crop=") == 1
    assert "[3:v]" not in graph
    assert "split=2" not in graph
    assert "concat=" not in graph
    assert "amix=" not in graph


def test_final_output_combines_speech_audio_and_visual_graphs_in_one_ffmpeg_call(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    voice = tmp_path / "voice.wav"
    effect = tmp_path / "effect.wav"
    banner = tmp_path / "banner.png"
    output = tmp_path / "result.mp4"
    for path in (video, voice, effect, banner):
        path.write_bytes(b"test-input")
    captured = {"calls": 0}
    plan = {"video_start": 0.0}
    monkeypatch.setattr(visual_finalizer, "_probe_video", lambda path: (1920, 1080, "30/1"))
    monkeypatch.setattr(visual_finalizer, "resolve_executable", lambda *args, **kwargs: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        captured["calls"] += 1
        captured["command"] = list(command)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visual_finalizer.subprocess, "run", fake_run)
    audio_graph = (
        "[1:a]aresample=44100,aformat=sample_fmts=s16:channel_layouts=stereo,"
        "atrim=duration=3,apad=whole_dur=3,atrim=duration=3,asetpts=PTS-STARTPTS[voice];"
        "[2:a]anull[effect_main];"
        "[voice][effect_main]amix=inputs=2:duration=first:dropout_transition=2[main_a]"
    )

    result = visual_finalizer.render_video_with_speech_tracks(
        video, voice, effect, banner, output,
        audio_filter_graph=audio_graph,
        audio_output_label="[main_a]",
        audio_render_options={
            "codec": "aac",
            "bitrate": "192k",
            "sample_rate": 44100,
            "channels": 2,
            "effect_input_loop": True,
        },
        position="final", intro=True, plan=plan,
    )

    command = captured["command"]
    graph = command[command.index("-filter_complex") + 1]
    assert result == output
    assert captured["calls"] == 1
    assert command[:command.index("-filter_complex")].count("-i") == 4
    assert command[command.index("-map") + 1] == "[vout]"
    second_map = command.index("-map", command.index("-map") + 1)
    assert command[second_map + 1] == "[main_a]"
    assert graph.count("crop=") == 1
    assert "amix=" in graph
    assert "atrim=duration=3" in graph
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[-1] == str(output)


def test_no_intro_render_still_uses_one_ffmpeg_invocation_and_no_banner(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    voice = tmp_path / "voice.wav"
    effect = tmp_path / "effect.wav"
    output = tmp_path / "result.mp4"
    for path in (video, voice, effect):
        path.write_bytes(b"test-input")
    captured = {}
    monkeypatch.setattr(visual_finalizer, "_probe_video", lambda path: (1080, 1920, "30/1"))
    monkeypatch.setattr(visual_finalizer, "resolve_executable", lambda *args, **kwargs: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visual_finalizer.subprocess, "run", fake_run)
    audio_graph = (
        "[1:a]aresample=44100,aformat=sample_fmts=s16:channel_layouts=stereo,"
        "atrim=duration=3,apad=whole_dur=3,atrim=duration=3,asetpts=PTS-STARTPTS[voice];"
        "[2:a]anull[effect_main];"
        "[voice][effect_main]amix=inputs=2:duration=first:dropout_transition=2[main_a]"
    )
    result = visual_finalizer.render_video_with_speech_tracks(
        video, voice, effect, tmp_path / "unused-banner.png", output,
        audio_filter_graph=audio_graph,
        audio_output_label="[main_a]",
        audio_render_options={
            "codec": "aac",
            "bitrate": "192k",
            "sample_rate": 44100,
            "channels": 2,
            "effect_input_loop": True,
        },
        position="final", intro=False, plan={"video_start": 0.0},
    )
    command = captured["command"]
    graph = command[command.index("-filter_complex") + 1]
    assert result == output
    assert command[:command.index("-filter_complex")].count("-i") == 3
    assert command[command.index("-map") + 1] == "[main_v]"
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "[main_a]"
    assert graph.count("crop=") == 1
    assert "amix=" in graph
    assert "-loop" not in command


def test_portrait_crop_shrinks_tight_odd_origin_safe_region_to_even_alignment():
    crop = visual_finalizer._single_portrait_crop(
        {
            "crop_final": {"x": 1, "y": 1, "width": 900, "height": 1600},
        },
        source_width=1000,
        source_height=1700,
    )

    x, y, width, height = crop
    assert x % 2 == 0
    assert y % 2 == 0
    assert width % 2 == 0
    assert height % 2 == 0
    assert width * 16 == height * 9
    assert x >= 1 and y >= 1
    assert x + width <= 901
    assert y + height <= 1601


def test_portrait_crop_keeps_the_largest_centered_crop_when_aligned_region_allows_it():
    crop = visual_finalizer._single_portrait_crop(
        {
            "crop_final": {"x": 2, "y": 4, "width": 902, "height": 1604},
        },
        source_width=1000,
        source_height=1700,
    )

    x, y, width, height = crop
    assert (width, height) == (900, 1600)
    assert (x, y) == (2, 4)
