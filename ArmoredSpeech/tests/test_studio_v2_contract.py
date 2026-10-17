from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

pytest.importorskip("cv2", reason="Studio visual contract requires Studio dependencies")

import ArmoredStudio.unified as unified_module
from ArmoredStudio.processing import visual_finalizer
from ArmoredStudio.speech_handoff import sha256_file, validate_audio_package
from ArmoredStudio.unified import UnifiedStudio
from ArmoredSpeech.timeline_mixer import build_audio_filter_graph, visual_plan_digest


@dataclass
class FakeItem:
    source_id: str
    content_id: str
    telegram_message_id: str
    original_path: Path
    working_path: Path | None = None
    affiliate_url: str | None = None
    affiliate_name: str | None = None


class FakeStorage:
    def __init__(self, output: Path):
        self.output = output

    def result(self, *args, **kwargs):
        return self.output


def _write_wav(path: Path, *, seconds: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(44100 * seconds)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(bytes(frames * 4))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(root: Path, original: Path, voice: Path, effect: Path, plan: dict, *, intro: bool = True) -> dict:
    original_hash = sha256_file(original)
    voice_duration = 3.0
    voice_meta = {
        "path": str(voice),
        "sha256": _sha(voice),
        "format": "wav-pcm-s16le",
        "sample_rate": 44100,
        "channels": 2,
        "duration_seconds": voice_duration,
        "size_bytes": voice.stat().st_size,
        "role": "voice",
    }
    effect_meta = {
        "path": str(effect),
        "sha256": _sha(effect),
        "format": "wav-pcm-s16le",
        "sample_rate": 44100,
        "channels": 2,
        "duration_seconds": 3.0,
        "size_bytes": effect.stat().st_size,
        "source_sha256": "a" * 64,
        "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
    }
    timeline = build_audio_filter_graph(
        visual_plan=plan,
        audio_start_seconds=0.0,
        main_duration_seconds=3.0,
        intro=intro,
        intro_position="final",
    )
    graph = timeline["filter_graph"]
    timeline = {
        key: value for key, value in timeline.items()
        if key not in {"filter_graph", "output_label"}
    }
    timeline["final_audio_duration_seconds"] = 3.0 + (2.0 if intro else 0.0)
    return {
        "contract_version": 5,
        "status": "READY",
        "source_id": "source-1",
        "item_id": "telegram-1",
        "content_id": "source1_telegram1",
        "original_path": str(original),
        "original_sha256_before": original_hash,
        "original_sha256_after": original_hash,
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "route": "RVC_MELODY",
        "rvc_eligible": True,
        "voice_track": voice_meta,
        "effect_track": effect_meta,
        "audio_filter_graph": graph,
        "audio_output_label": "[final_a]" if intro else "[main_a]",
        "audio_timeline": timeline,
        "prepared_video_duration_seconds": 3.0,
    }


def _tracks(tmp_path: Path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    voice = tmp_path / "speech_package" / "voice.wav"
    effect = tmp_path / "speech_package" / "effect.wav"
    _write_wav(voice, seconds=3.0)
    _write_wav(effect, seconds=3.0)
    return original, voice, effect


def test_studio_uses_speech_graph_and_tracks_in_single_visual_render(tmp_path, monkeypatch):
    original, voice, effect = _tracks(tmp_path)
    banner = tmp_path / "ArmoredStudio" / "assets" / "banner.png"
    banner.parent.mkdir(parents=True, exist_ok=True)
    banner.write_bytes(b"synthetic banner")
    output = tmp_path / "final.mp4"
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    plan = {"video_start": 0.0}
    visual = SimpleNamespace(plan=plan, video={"sucesso": True, "duracao": 3.0})
    payload = _payload(tmp_path, original, voice, effect, plan)
    captured = {}

    monkeypatch.delenv("ARMORED_STUDIO_FORCE_COPY", raising=False)
    monkeypatch.setenv("ARMORED_STUDIO_BANNER", str(banner))
    monkeypatch.setenv("ARMORED_FFMPEG", "ffmpeg")
    monkeypatch.setattr(unified_module.shutil, "which", lambda _: "ffmpeg")

    def fake_render(video, voice_path, effect_path, banner_path, output_path, **kwargs):
        captured.update({
            "video": Path(video),
            "voice": Path(voice_path),
            "effect": Path(effect_path),
            "banner": Path(banner_path),
            "output": Path(output_path),
            "intro": kwargs.get("intro"),
            "position": kwargs.get("position"),
            "plan": kwargs.get("plan"),
            "graph": kwargs.get("audio_filter_graph"),
            "audio_label": kwargs.get("audio_output_label"),
        })
        Path(output_path).write_bytes(b"synthetic rendered output")

    monkeypatch.setattr(visual_finalizer, "render_video_with_speech_tracks", fake_render)
    studio = UnifiedStudio(
        tmp_path,
        FakeStorage(output),
        analysis=SimpleNamespace(
            analyze=lambda source: (_ for _ in ()).throw(
                AssertionError("the already validated visual plan should be reused")
            )
        ),
    )

    result, details = studio.process(item, speech_payload=payload, visual_analysis=visual)

    assert result == output
    assert output.read_bytes() == b"synthetic rendered output"
    assert captured["voice"] == voice
    assert captured["effect"] == effect
    assert captured["graph"] == payload["audio_filter_graph"]
    assert captured["audio_label"] == "[final_a]"
    assert captured["intro"] is True
    assert captured["position"] == "final"
    assert captured["plan"] == plan
    assert details is visual
    assert _sha(original) == payload["original_sha256_before"]


def test_studio_rejects_voice_hash_mismatch_before_render(tmp_path, monkeypatch):
    original, voice, effect = _tracks(tmp_path)
    output = tmp_path / "final.mp4"
    plan = {"video_start": 0.0}
    payload = _payload(tmp_path, original, voice, effect, plan)
    payload["voice_track"]["sha256"] = "0" * 64
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    monkeypatch.setattr(
        visual_finalizer, "render_video_with_speech_tracks",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Invalid audio inputs must be rejected before rendering")
        ),
    )
    with pytest.raises(RuntimeError, match="handoff inválido"):
        studio.process(item, speech_payload=payload, visual_analysis=SimpleNamespace(plan=plan))
    assert not output.exists()


def test_studio_rejects_package_composed_for_different_visual_plan(tmp_path, monkeypatch):
    original, voice, effect = _tracks(tmp_path)
    output = tmp_path / "final.mp4"
    package_plan = {"video_start": 0.0}
    payload = _payload(tmp_path, original, voice, effect, package_plan)
    different_plan = {"video_start": 0.25}
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    monkeypatch.setattr(
        visual_finalizer, "render_video_with_speech_tracks",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Mismatched visual/audio timelines must not render")
        ),
    )
    with pytest.raises(RuntimeError, match="plano visual difere"):
        studio.process(
            item,
            speech_payload=payload,
            visual_analysis=SimpleNamespace(plan=different_plan),
        )
    assert not output.exists()


def test_studio_handoff_contains_component_tracks_and_speech_graph(tmp_path):
    original, voice, effect = _tracks(tmp_path)
    plan = {"video_start": 0.0}
    payload = _payload(tmp_path, original, voice, effect, plan)

    validated = validate_audio_package(
        payload,
        workspace_root=tmp_path,
        expected_source_id="source-1",
        expected_item_id="telegram-1",
        expected_original_path=original,
    )

    assert validated.voice_track_path == voice
    assert validated.effect_track_path == effect
    assert validated.audio_filter_graph == payload["audio_filter_graph"]
    assert validated.audio_output_label == "[final_a]"
    assert validated.visual_plan_sha256 == hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_visual_renderer_combines_audio_and_video_graphs_in_one_ffmpeg_call(tmp_path, monkeypatch):
    video = tmp_path / "input.mp4"
    voice = tmp_path / "voice.wav"
    effect = tmp_path / "effect.wav"
    banner = tmp_path / "banner.png"
    output = tmp_path / "result.mp4"
    for path in (video, banner):
        path.write_bytes(b"test-input")
    _write_wav(voice)
    _write_wav(effect)
    calls = []

    monkeypatch.setattr(visual_finalizer, "_probe_video", lambda path: (1920, 1080, "30/1"))
    monkeypatch.setattr(visual_finalizer, "resolve_executable", lambda *args, **kwargs: Path("ffmpeg"))

    def fake_run(command, **kwargs):
        calls.append(list(command))
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(visual_finalizer.subprocess, "run", fake_run)
    audio_graph = (
        "[1:a]aresample=44100,aformat=sample_fmts=s16:channel_layouts=stereo,"
        "atrim=duration=3,apad=whole_dur=3,atrim=duration=3,asetpts=PTS-STARTPTS[voice];"
        "[2:a]anull[effect_main];[voice][effect_main]"
        "amix=inputs=2:duration=first:dropout_transition=2[main_a]"
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
        position="final",
        intro=False,
        plan={"video_start": 0.0},
    )

    assert result == output
    assert len(calls) == 1
    command = calls[0]
    graph = command[command.index("-filter_complex") + 1]
    assert graph.count("crop=") == 1
    assert "amix=" in graph
    assert command[command.index("-map") + 1] == "[main_v]"
    second_map = command.index("-map", command.index("-map") + 1)
    assert command[second_map + 1] == "[main_a]"
    assert command[-1] == str(output)


def test_visual_plan_digests_handle_pathlike_detector_metadata_consistently():
    detector_path = PureWindowsPath("C:/ArmoredAudioLab/item5.mp4")
    plan = {
        "video_info": {"path": detector_path},
        "detectores": {"gemini": {"video": detector_path}},
        "video_start": 0.0,
    }
    normalized = {
        "video_info": {"path": str(detector_path)},
        "detectores": {"gemini": {"video": str(detector_path)}},
        "video_start": 0.0,
    }

    expected = visual_plan_digest(normalized)
    assert visual_plan_digest(plan) == expected
    assert unified_module._visual_plan_digest(plan) == expected
