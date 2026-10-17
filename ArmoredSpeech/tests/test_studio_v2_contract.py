from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("cv2", reason="Studio v2 contract requires Studio visual dependencies")

import ArmoredStudio.unified as unified_module
from ArmoredStudio.processing import finalizer
from ArmoredStudio.speech_handoff import sha256_file
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


class FakeAnalysis:
    def __init__(self):
        self.received_override = None

    def analyze(self, source, audio_override=None):
        self.received_override = audio_override
        return SimpleNamespace(
            video={"sucesso": True, "duracao": 1.0},
            plan={"audio_start": 0.25},
            audio=audio_override,
        )


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"\x01\x00" * 44100 * 2)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(root: Path, original: Path, voice: Path, effect: Path) -> dict:
    digest = sha256_file(original)
    return {
        "contract_version": 2,
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
        "voice_track": {
            "path": str(voice),
            "sha256": _sha(voice),
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
        },
        "effect_track": {
            "path": str(effect),
            "sha256": _sha(effect),
            "source_sha256": "b" * 64,
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
            "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        },
    }


def test_studio_v2_uses_speech_tracks_without_reclassifying_or_running_rvc(tmp_path, monkeypatch):
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
    analysis = FakeAnalysis()
    studio = UnifiedStudio(tmp_path, FakeStorage(output), analysis=analysis)
    payload = _payload(tmp_path, original, voice, effect)
    captured = {}

    monkeypatch.delenv("ARMORED_STUDIO_FORCE_COPY", raising=False)
    monkeypatch.setenv("ARMORED_STUDIO_BANNER", str(banner))
    monkeypatch.setenv("ARMORED_FFMPEG", "ffmpeg")
    monkeypatch.setattr(unified_module.shutil, "which", lambda _: "ffmpeg")

    def forbidden_rvc(*args, **kwargs):
        raise AssertionError("Studio v2 must never execute RVC")
    import ArmoredStudio.processing.rvc as rvc_compat
    monkeypatch.setattr(rvc_compat, "converter_voz", forbidden_rvc)

    def fake_finalize(video, voice_path, effect_path, banner_path, output_path, **kwargs):
        captured.update({
            "video": Path(video),
            "voice": Path(voice_path),
            "effect": Path(effect_path),
            "banner": Path(banner_path),
            "audio_tracks_prepared": kwargs.get("audio_tracks_prepared"),
            "plan": kwargs.get("plan"),
        })
        Path(output_path).write_bytes(b"synthetic rendered output")

    monkeypatch.setattr(finalizer, "finalizar", fake_finalize)

    result, details = studio.process(item, speech_payload=payload)

    assert result == output
    assert output.read_bytes() == b"synthetic rendered output"
    assert captured["voice"] == voice
    assert captured["effect"] == effect
    assert captured["audio_tracks_prepared"] is True
    assert captured["plan"] == {"audio_start": 0.25}
    assert analysis.received_override["method"] == "armoredspeech-handoff-v2"
    assert details.audio["method"] == "armoredspeech-handoff-v2"
    assert _sha(original) == payload["original_sha256_after"]


def test_studio_v2_rejects_package_hash_mismatch_before_render(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    voice = tmp_path / "audio_package" / "voice.wav"
    effect = tmp_path / "audio_package" / "effect.wav"
    _write_wav(voice)
    _write_wav(effect)
    payload = _payload(tmp_path, original, voice, effect)
    payload["voice_track"]["sha256"] = "0" * 64
    output = tmp_path / "final.mp4"
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output), analysis=FakeAnalysis())

    def forbidden_finalize(*args, **kwargs):
        raise AssertionError("Invalid v2 package must be rejected before rendering")

    monkeypatch.setattr(finalizer, "finalizar", forbidden_finalize)

    with pytest.raises(RuntimeError, match="handoff inválido"):
        studio.process(item, speech_payload=payload)
    assert not output.exists()
