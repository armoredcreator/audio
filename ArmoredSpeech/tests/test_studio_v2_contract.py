from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("cv2", reason="Studio visual contract requires Studio dependencies")

import ArmoredStudio.unified as unified_module
from ArmoredStudio.processing import visual_finalizer
from ArmoredStudio.speech_handoff import sha256_file, validate_audio_package
from ArmoredStudio.unified import UnifiedStudio
from ArmoredSpeech.timeline_mixer import build_audio_filter_graph


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
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(bytes(int(44100 * seconds) * 2 * 2))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(root: Path, original: Path, voice: Path | None, effect: Path, plan: dict, *, intro=True, audio_start_seconds=0.0):
    digest = sha256_file(original)
    audio_timeline = build_audio_filter_graph(
        visual_plan=plan,
        audio_start_seconds=audio_start_seconds,
        main_duration_seconds=3.0,
        intro=intro,
        intro_position="final" if intro else "final",
    )
    rvc = voice is not None
    audio_class = "PT_BR_NARRATION" if rvc else "MUSIC_ONLY"
    payload = {
        "contract_version": 3,
        "status": "READY",
        "source_id": "source-1",
        "item_id": "telegram-1",
        "content_id": "source1_telegram1",
        "original_path": str(original),
        "original_sha256_before": digest,
        "original_sha256_after": digest,
        "audio_class": audio_class,
        "language": "pt-br" if rvc else "unknown",
        "speech_detected": rvc,
        "music_detected": False if rvc else True,
        "confidence": 0.99,
        "route": "RVC_MELODY" if rvc else "EFFECT_ONLY",
        "rvc_eligible": rvc,
        "rvc_model": "melody" if rvc else None,
        "rvc_model_sha256": "c" * 64 if rvc else None,
        "rvc_index_sha256": "d" * 64 if rvc else None,
        "voice_track": ({
            "path": str(voice),
            "sha256": _sha(voice),
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 3.0,
        } if rvc else None),
        "effect_track": {
            "path": str(effect),
            "sha256": _sha(effect),
            "source_sha256": "e" * 64,
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 3.0,
            "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        },
        "audio_timeline": audio_timeline,
        "prepared_video_duration_seconds": 3.0,
    }
    return payload


def test_studio_combines_one_speech_audio_graph_with_one_visual_render(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    voice = tmp_path / "audio_package" / "voice.wav"
    effect = tmp_path / "audio_package" / "effect.wav"
    _write_wav(voice)
    _write_wav(effect)
    banner = tmp_path / "ArmoredStudio" / "assets" / "banner.png"
    banner.parent.mkdir(parents=True, exist_ok=True)
    banner.write_bytes(b"synthetic banner")
    output = tmp_path / "final.mp4"
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    plan = {"video_start": 0.0}
    visual = SimpleNamespace(plan=plan, video={"sucesso": True, "duracao": 3.0})
    payload = _payload(tmp_path, original, voice, effect, plan, audio_start_seconds=0.25)
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
            "route": kwargs.get("route"),
            "audio_filter_graph": kwargs.get("audio_filter_graph"),
            "audio_output_label": kwargs.get("audio_output_label"),
            "plan": kwargs.get("plan"),
        })
        Path(output_path).write_bytes(b"synthetic rendered output")

    monkeypatch.setattr(visual_finalizer, "render_with_speech_audio", fake_render)
    studio = UnifiedStudio(tmp_path, FakeStorage(output), analysis=SimpleNamespace(
        analyze=lambda source: (_ for _ in ()).throw(AssertionError("visual plan should be reused"))
    ))

    result, details = studio.process(item, speech_payload=payload, visual_analysis=visual)

    assert result == output
    assert output.read_bytes() == b"synthetic rendered output"
    assert captured["voice"] == voice
    assert captured["effect"] == effect
    assert captured["route"] == "RVC_MELODY"
    assert captured["audio_filter_graph"] == payload["audio_timeline"]["filter_graph"]
    assert captured["audio_output_label"] == "[final_a]"
    assert captured["plan"] == plan
    assert details is visual
    assert _sha(original) == payload["original_sha256_after"]


def test_studio_rejects_voice_hash_mismatch_before_render(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    voice = tmp_path / "audio_package" / "voice.wav"
    effect = tmp_path / "audio_package" / "effect.wav"
    _write_wav(voice)
    _write_wav(effect)
    output = tmp_path / "final.mp4"
    plan = {"video_start": 0.0}
    payload = _payload(tmp_path, original, voice, effect, plan)
    payload["voice_track"]["sha256"] = "0" * 64
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    def forbidden_render(*args, **kwargs):
        raise AssertionError("Invalid voice audio must be rejected before rendering")

    monkeypatch.setattr(visual_finalizer, "render_with_speech_audio", forbidden_render)

    with pytest.raises(RuntimeError, match="handoff inválido"):
        studio.process(item, speech_payload=payload, visual_analysis=SimpleNamespace(plan=plan))

    assert not output.exists()


def test_studio_rejects_audio_graph_composed_for_different_visual_plan(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    voice = tmp_path / "audio_package" / "voice.wav"
    effect = tmp_path / "audio_package" / "effect.wav"
    _write_wav(voice)
    _write_wav(effect)
    output = tmp_path / "final.mp4"
    package_plan = {"video_start": 0.0}
    payload = _payload(tmp_path, original, voice, effect, package_plan)
    different_visual_plan = {"video_start": 0.25}
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    def forbidden_render(*args, **kwargs):
        raise AssertionError("Mismatched visual/audio timelines must not render")

    monkeypatch.setattr(visual_finalizer, "render_with_speech_audio", forbidden_render)

    with pytest.raises(RuntimeError, match="plano visual difere"):
        studio.process(
            item,
            speech_payload=payload,
            visual_analysis=SimpleNamespace(plan=different_visual_plan),
        )
    assert not output.exists()


def test_effect_only_contract_has_no_voice_file_or_rvc_metadata(tmp_path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"immutable original")
    effect = tmp_path / "effect.wav"
    _write_wav(effect)
    plan = {"audio_start": 0.0}
    payload = _payload(tmp_path, original, None, effect, plan)

    validated = validate_audio_package(
        payload,
        workspace_root=tmp_path,
        expected_source_id="source-1",
        expected_item_id="telegram-1",
        expected_original_path=original,
    )

    assert validated.route == "EFFECT_ONLY"
    assert validated.voice_track_path is None
    assert validated.effect_track_path == effect
    assert validated.audio_timeline["output_label"] == "[final_a]"
