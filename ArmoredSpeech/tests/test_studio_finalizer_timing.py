from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ArmoredStudio.processing import visual_finalizer


def test_visual_graph_crops_once_before_splitting_main_and_intro():
    graph, label = visual_finalizer._video_filter_graph(
        1080, 1920, "30/1", {"video_start": 0.0},
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
    assert "[2:v]" not in graph
    assert "split=2" not in graph
    assert "concat=" not in graph
    assert "amix=" not in graph


def test_final_output_uses_one_ffmpeg_call_and_stream_copies_speech_audio(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    final_audio = tmp_path / "final_audio.m4a"
    banner = tmp_path / "banner.png"
    output = tmp_path / "result.mp4"
    video.write_bytes(b"video bytes")
    final_audio.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"audio" * 30)
    banner.write_bytes(b"banner bytes")

    captured = {"calls": 0}
    plan = {"video_start": 0.0}
    monkeypatch.setattr(visual_finalizer, "_probe_video", lambda path: (1080, 1920, "30/1"))
    monkeypatch.setattr(visual_finalizer, "resolve_executable", lambda *args, **kwargs: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        captured["calls"] += 1
        captured["command"] = list(command)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visual_finalizer.subprocess, "run", fake_run)
    result = visual_finalizer.render_video_with_speech_track(
        video, final_audio, banner, output,
        position="final", intro=True, plan=plan,
    )

    command = captured["command"]
    graph = command[command.index("-filter_complex") + 1]
    assert result == output
    assert captured["calls"] == 1
    assert command[command.index("-map") + 1] == "[vout]"
    second_map = command.index("-map", command.index("-map") + 1)
    assert command[second_map + 1] == "1:a:0"
    assert command[:command.index("-filter_complex")].count("-i") == 3
    assert graph.count("crop=") == 1
    assert "split=2[main_src][intro_src]" in graph
    assert "amix=" not in graph
    assert "atrim=" not in graph
    assert "afade=" not in graph
    assert "volume=" not in graph
    assert "loudnorm=" not in graph
    assert "-stream_loop" not in command
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[-1] == str(output)


def test_no_intro_render_maps_ready_track_without_reencoding_audio(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    final_audio = tmp_path / "final_audio.m4a"
    output = tmp_path / "result.mp4"
    video.write_bytes(b"video bytes")
    final_audio.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"audio" * 30)
    captured = {}

    monkeypatch.setattr(visual_finalizer, "_probe_video", lambda path: (1080, 1920, "30/1"))
    monkeypatch.setattr(visual_finalizer, "resolve_executable", lambda *args, **kwargs: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visual_finalizer.subprocess, "run", fake_run)
    result = visual_finalizer.render_video_with_speech_track(
        video, final_audio, tmp_path / "unused-banner.png", output,
        position="final", intro=False, plan={"video_start": 0.0},
    )
    command = captured["command"]
    graph = command[command.index("-filter_complex") + 1]
    assert result == output
    assert command[command.index("-map") + 1] == "[main_v]"
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "1:a:0"
    assert command[:command.index("-filter_complex")].count("-i") == 2
    assert graph.count("crop=") == 1
    assert "amix=" not in graph
    assert command[command.index("-c:a") + 1] == "copy"
