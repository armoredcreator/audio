from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("cv2", reason="Studio v3 contract requires Studio visual dependencies")

import ArmoredStudio.unified as unified_module
from ArmoredStudio.processing import visual_finalizer
from ArmoredStudio.speech_handoff import sha256_file, SpeechHandoffError, validate_audio_package
from ArmoredStudio.unified import UnifiedStudio


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


def _plan_digest(plan: dict) -> str:
    return hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _payload(root: Path, original: Path, final_audio: Path, plan: dict) -> dict:
    digest = sha256_file(original)
    timeline = {
        "visual_plan_sha256": _plan_digest(plan),
        "audio_start_seconds": float(plan.get("audio_start", 0.0)),
        "intro_enabled": True,
        "intro_position": "final",
        "intro_duration_seconds": 2.0,
    }
    return {
        "contract_version": 3,
        "status": "READY",
        "source_id": "source-1",
        "item_id": "telegram-1",
        "content_id": "source1_telegram1",
        "original_path": str(original),
        "original_sha256_before": digest,
        "original_sha256_after": digest,
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "route": "RVC_MELODY",
        "rvc_eligible": True,
        "rvc_model": "melody",
        "rvc_model_sha256": "c" * 64,
        "rvc_index_sha256": "d" * 64,
        "timeline": timeline,
        "final_audio_track": {
            "path": str(final_audio),
            "sha256": _sha(final_audio),
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 3.0,
            "composition_owner": "ArmoredSpeech",
        },
    }


def test_studio_v3_muxes_one_speech_owned_final_soundtrack_without_audio_processing(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    final_audio = tmp_path / "audio_package" / "final_audio.wav"
    _write_wav(final_audio, seconds=3.0)
    banner = tmp_path / "ArmoredStudio" / "assets" / "banner.png"
    banner.parent.mkdir(parents=True, exist_ok=True)
    banner.write_bytes(b"synthetic banner")
    output = tmp_path / "final.mp4"
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    plan = {"audio_start": 0.25, "video_start": 0.0}
    visual = SimpleNamespace(plan=plan, video={"sucesso": True, "duracao": 1.0})
    payload = _payload(tmp_path, original, final_audio, plan)
    captured = {}

    monkeypatch.delenv("ARMORED_STUDIO_FORCE_COPY", raising=False)
    monkeypatch.setenv("ARMORED_STUDIO_BANNER", str(banner))
    monkeypatch.setenv("ARMORED_FFMPEG", "ffmpeg")
    monkeypatch.setattr(unified_module.shutil, "which", lambda _: "ffmpeg")

    def fake_render(video, audio_track, banner_path, output_path, **kwargs):
        captured.update({
            "video": Path(video),
            "audio_track": Path(audio_track),
            "banner": Path(banner_path),
            "output": Path(output_path),
            "intro": kwargs.get("intro"),
            "position": kwargs.get("position"),
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
    assert captured["audio_track"] == final_audio
    assert captured["intro"] is True
    assert captured["position"] == "final"
    assert captured["plan"] == plan
    assert details is visual
    assert _sha(original) == payload["original_sha256_after"]


def test_studio_v3_rejects_final_audio_hash_mismatch_before_render(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    final_audio = tmp_path / "audio_package" / "final_audio.wav"
    _write_wav(final_audio)
    output = tmp_path / "final.mp4"
    plan = {"audio_start": 0.0}
    payload = _payload(tmp_path, original, final_audio, plan)
    payload["final_audio_track"]["sha256"] = "0" * 64
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    def forbidden_render(*args, **kwargs):
        raise AssertionError("Invalid final audio must be rejected before rendering")

    monkeypatch.setattr(visual_finalizer, "render_with_speech_audio", forbidden_render)

    with pytest.raises(RuntimeError, match="handoff inválido"):
        studio.process(item, speech_payload=payload, visual_analysis=SimpleNamespace(plan=plan))

    assert not output.exists()


def test_studio_v3_rejects_package_composed_for_different_visual_plan(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    final_audio = tmp_path / "audio_package" / "final_audio.wav"
    _write_wav(final_audio)
    output = tmp_path / "final.mp4"
    package_plan = {"audio_start": 0.0}
    payload = _payload(tmp_path, original, final_audio, package_plan)
    different_visual_plan = {"audio_start": 0.25}
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


def test_validator_accepts_only_one_final_audio_track(tmp_path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"immutable original")
    final_audio = tmp_path / "final_audio.wav"
    _write_wav(final_audio)
    plan = {"audio_start": 0.0}
    payload = _payload(tmp_path, original, final_audio, plan)

    validated = validate_audio_package(
        payload,
        workspace_root=tmp_path,
        expected_source_id="source-1",
        expected_item_id="telegram-1",
        expected_original_path=original,
    )

    assert validated.final_audio_track_path == final_audio
    assert validated.final_audio_sha256 == _sha(final_audio)
    assert validated.route == "RVC_MELODY"
    assert "voice_track" not in payload
    assert "effect_track" not in payload
