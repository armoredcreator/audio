from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ArmoredStudio.processing import visual_finalizer


def test_studio_visual_graph_has_no_audio_processing():
    graph, label = visual_finalizer._video_filter_graph(
        Path("input.mp4"),
        Path("banner.png"),
        1080,
        1920,
        "30/1",
        {"audio_start": 1.25, "video_start": 0.0},
        intro=True,
        position="final",
    )

    assert label == "[vout]"
    assert "concat=n=2:v=1:a=0[vout]" in graph
    assert "atrim=" not in graph
    assert "amix=" not in graph
    assert "loudnorm=" not in graph
    assert "volume=" not in graph
    assert "afade=" not in graph


def test_visual_plan_trims_video_only():
    graph, _ = visual_finalizer._video_filter_graph(
        Path("input.mp4"),
        Path("banner.png"),
        1080,
        1920,
        "30/1",
        {"audio_start": 1.25, "video_start": 0.75},
        intro=True,
        position="inicio",
    )

    assert graph.count("trim=start=0.75") == 2
    assert "atrim=start=1.25" not in graph
    assert "audio_start" not in graph
    assert "concat=n=2:v=1:a=0[vout]" in graph


def test_no_intro_visual_graph_does_not_reference_banner_or_audio(tmp_path):
    graph, label = visual_finalizer._video_filter_graph(
        Path("input.mp4"),
        Path("unused-banner.png"),
        1080,
        1920,
        "30/1",
        {"video_start": 0.0},
        intro=False,
        position="final",
    )

    assert label == "[main_v]"
    assert "[2:v]" not in graph
    assert "concat=" not in graph
    assert "amix=" not in graph
    assert "atrim=" not in graph


def test_final_render_maps_one_speech_soundtrack_without_audio_filters(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    audio = tmp_path / "final_audio.wav"
    banner = tmp_path / "banner.png"
    output = tmp_path / "result.mp4"
    for path in (video, audio, banner):
        path.write_bytes(b"test-input")

    captured = {}

    monkeypatch.setattr(visual_finalizer, "_probe_video", lambda path: (1080, 1920, "30/1"))
    monkeypatch.setattr(visual_finalizer, "resolve_executable", lambda *args, **kwargs: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visual_finalizer.subprocess, "run", fake_run)

    result = visual_finalizer.render_with_speech_audio(
        video, audio, banner, output,
        position="final",
        intro=True,
        plan={"audio_start": 0.5, "video_start": 0.0},
    )

    command = captured["command"]
    graph = command[command.index("-filter_complex") + 1]
    assert result == output
    assert command[command.index("-map") + 1] == "[vout]"
    second_map_index = command.index("-map", command.index("-map") + 1)
    assert command[second_map_index + 1] == "1:a:0"
    assert "atrim=" not in graph
    assert "amix=" not in graph
    assert "loudnorm=" not in graph
    assert "volume=" not in graph
    assert "afade=" not in graph
    assert "-stream_loop" not in command
