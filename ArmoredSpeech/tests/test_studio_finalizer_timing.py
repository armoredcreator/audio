from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ArmoredStudio.processing import visual_finalizer
from ArmoredSpeech.timeline_mixer import build_audio_filter_graph


def test_visual_graph_crops_once_before_splitting_main_and_intro():
    graph, label = visual_finalizer._video_filter_graph(
        1080,
        1920,
        "30/1",
        {"video_start": 0.0},
        intro=True,
        position="final",
    )

    assert label == "[vout]"
    assert graph.count("crop=") == 1
    assert "split=2[main_src][intro_src]" in graph
    assert "concat=n=2:v=1:a=0[vout]" in graph
    assert "atrim=" not in graph
    assert "amix=" not in graph
    assert "afade=" not in graph


def test_visual_plan_video_start_and_crop_are_applied_once_before_the_branch():
    graph, _ = visual_finalizer._video_filter_graph(
        1920,
        1080,
        "30/1",
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
    assert "atrim=start=1.25" not in graph
    assert "concat=n=2:v=1:a=0[vout]" in graph


def test_no_intro_visual_graph_has_one_crop_and_no_banner_input():
    graph, label = visual_finalizer._video_filter_graph(
        1080,
        1920,
        "30/1",
        {"video_start": 0.0},
        intro=False,
        position="final",
    )

    assert label == "[main_v]"
    assert graph.count("crop=") == 1
    assert "[3:v]" not in graph
    assert "split=2" not in graph
    assert "concat=" not in graph
    assert "amix=" not in graph


def test_final_output_uses_one_ffmpeg_call_with_speech_graph_and_visual_graph(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    voice = tmp_path / "voice.wav"
    effect = tmp_path / "effect.wav"
    banner = tmp_path / "banner.png"
    output = tmp_path / "result.mp4"
    for path in (video, voice, effect, banner):
        path.write_bytes(b"test-input")

    captured = {"calls": 0}
    plan = {"video_start": 0.0}
    audio = build_audio_filter_graph(visual_plan=plan, audio_start_seconds=0.5, main_duration_seconds=3.0, intro=True, intro_position="final")

    monkeypatch.setattr(visual_finalizer, "_probe_video", lambda path: (1080, 1920, "30/1"))
    monkeypatch.setattr(visual_finalizer, "resolve_executable", lambda *args, **kwargs: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        captured["calls"] += 1
        captured["command"] = list(command)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visual_finalizer.subprocess, "run", fake_run)

    audio_inputs = [
        {"kind": "file", "role": "voice", "path": str(voice), "sha256": "a" * 64},
        {"kind": "loop_file", "role": "effect", "path": str(effect), "sha256": "b" * 64},
    ]
    result = visual_finalizer.render_with_speech_audio(
        video,
        audio_inputs,
        banner,
        output,
        audio_filter_graph=audio["filter_graph"],
        audio_output_label=audio["output_label"],
        position="final",
        intro=True,
        plan=plan,
    )

    command = captured["command"]
    graph = command[command.index("-filter_complex") + 1]
    assert result == output
    assert captured["calls"] == 1
    assert command[command.index("-map") + 1] == "[vout]"
    second_map = command.index("-map", command.index("-map") + 1)
    assert command[second_map + 1] == "[final_a]"
    assert graph.count("crop=") == 1
    assert "split=2[main_src][intro_src]" in graph
    assert "[voice_main][effect_main]amix=" in graph
    assert "afade=t=in:st=0:d=0.4" in graph
    assert "concat=n=2:v=0:a=1[final_a]" in graph
    assert "-stream_loop" in command
    assert command[-1] == str(output)


def test_effect_only_render_materializes_speech_declared_silence_input_without_route_logic(
    tmp_path, monkeypatch
):
    video = tmp_path / "input.mp4"
    effect = tmp_path / "effect.wav"
    banner = tmp_path / "banner.png"
    output = tmp_path / "result.mp4"
    for path in (video, effect, banner):
        path.write_bytes(b"test-input")

    audio_inputs = [
        {
            "kind": "lavfi",
            "role": "silence",
            "source": "anullsrc=r=44100:cl=stereo",
            "duration_seconds": 3.0,
        },
        {
            "kind": "loop_file",
            "role": "effect",
            "path": str(effect),
            "sha256": "b" * 64,
        },
    ]
    plan = {"video_start": 0.0}
    audio = build_audio_filter_graph(
        visual_plan=plan,
        audio_start_seconds=0.0,
        main_duration_seconds=3.0,
        intro=True,
        intro_position="final",
    )
    captured = {"calls": 0}

    monkeypatch.setattr(
        visual_finalizer, "_probe_video", lambda path: (1080, 1920, "30/1")
    )
    monkeypatch.setattr(
        visual_finalizer, "resolve_executable", lambda *args, **kwargs: Path("ffmpeg")
    )

    def fake_run(command, **kwargs):
        captured["calls"] += 1
        captured["command"] = list(command)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visual_finalizer.subprocess, "run", fake_run)

    result = visual_finalizer.render_with_speech_audio(
        video,
        audio_inputs,
        banner,
        output,
        audio_filter_graph=audio["filter_graph"],
        audio_output_label=audio["output_label"],
        position="final",
        intro=True,
        plan=plan,
    )

    command = captured["command"]
    graph = command[command.index("-filter_complex") + 1]
    assert result == output
    assert captured["calls"] == 1
    assert command.count("-f") == 1
    lavfi = command.index("lavfi")
    assert command[lavfi + 1] == "-t"
    assert command[lavfi + 2] == "3.000000"
    assert command[lavfi + 3] == "-i"
    assert command[lavfi + 4] == "anullsrc=r=44100:cl=stereo"
    assert command.count("-stream_loop") == 1
    assert "amix=inputs=2:duration=first" in graph
    assert "audio_inputs" not in command
    assert "route" not in command

