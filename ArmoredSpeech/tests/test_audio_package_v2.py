from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from ArmoredStudio.speech_handoff import SpeechHandoffError, validate_audio_package
from ArmoredSpeech import audio_package


@dataclass
class Item:
    source_id: str
    telegram_message_id: str
    content_id: str
    original_path: Path


def _write_wav(path: Path, *, sample_rate: int = 44100, channels: int = 2, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\x01\x00" * frames * channels)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package(tmp_path: Path, **overrides):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic, immutable original bytes")
    voice = tmp_path / "package" / "voice.wav"
    effect = tmp_path / "package" / "effect.wav"
    _write_wav(voice)
    _write_wav(effect)
    digest = _sha(original)
    payload = {
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
            "source_sha256": "a" * 64,
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
            "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        },
    }
    payload.update(overrides)
    return original, voice, effect, payload


def _validate(root: Path, original: Path, payload: dict):
    return validate_audio_package(
        payload,
        workspace_root=root,
        expected_source_id="source-1",
        expected_item_id="telegram-1",
        expected_original_path=original,
    )


def test_valid_v2_audio_package_validates_track_hashes_and_identity(tmp_path):
    original, voice, effect, payload = _package(tmp_path)

    result = _validate(tmp_path, original, payload)

    assert result.route == "RVC_MELODY"
    assert result.rvc_eligible is True
    assert result.voice_track_path == voice
    assert result.effect_track_path == effect
    assert result.voice_sha256 == _sha(voice)
    assert result.effect_sha256 == _sha(effect)


@pytest.mark.parametrize("field,value", [
    ("status", "PROCESSING"),
    ("contract_version", 1),
    ("route", "UNKNOWN"),
    ("rvc_model", "another-model"),
    ("source_id", "wrong-source"),
    ("item_id", "wrong-item"),
])
def test_v2_package_rejects_wrong_status_contract_route_or_identity(tmp_path, field, value):
    original, _, _, payload = _package(tmp_path, **{field: value})

    with pytest.raises(SpeechHandoffError):
        _validate(tmp_path, original, payload)


def test_v2_package_rejects_mutated_track_hash(tmp_path):
    original, voice, _, payload = _package(tmp_path)
    voice.write_bytes(voice.read_bytes() + b"tampered")

    with pytest.raises(SpeechHandoffError, match="SHA-256 mismatch"):
        _validate(tmp_path, original, payload)


def test_v2_package_rejects_track_outside_workspace(tmp_path):
    original, _, _, payload = _package(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.wav"
    _write_wav(outside)
    payload["voice_track"]["path"] = str(outside)
    payload["voice_track"]["sha256"] = _sha(outside)

    with pytest.raises(SpeechHandoffError, match="inside the lab workspace"):
        _validate(tmp_path, original, payload)


def test_v2_package_requires_explicit_no_music_evidence_for_melody(tmp_path):
    original, _, _, payload = _package(tmp_path, music_detected=None)

    with pytest.raises(SpeechHandoffError, match="Unsafe or inconsistent RVC_MELODY"):
        _validate(tmp_path, original, payload)


def test_audio_package_builder_prepares_tracks_without_changing_original(tmp_path, monkeypatch):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    original_hash = _sha(original)
    item = Item("source-1", "telegram-1", "source1_telegram1", original)
    directive = {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "rvc_eligible": True,
        "original_sha256_before": original_hash,
        "original_sha256_after": original_hash,
    }

    def fake_write_wave(path: str | Path):
        _write_wav(Path(path), sample_rate=44100, channels=2)

    monkeypatch.setattr(audio_package, "_tool", lambda name, env_name: f"mock-{name}")
    monkeypatch.setattr(audio_package, "_media_duration", lambda path, tool: 1.0)

    def fake_run(command: list[str], label: str):
        fake_write_wave(command[-1])

    monkeypatch.setattr(audio_package, "_run", fake_run)

    from armored_core.audio import rvc
    def fake_convert(source, destination, voice, item_id=None):
        fake_write_wave(destination)
        return Path(destination)
    monkeypatch.setattr(rvc, "converter_voz", fake_convert)

    output_dir = tmp_path / "storage" / "validation" / "batch1" / "source1" / "telegram1"
    package = audio_package.prepare_audio_package(
        directive,
        item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        output_dir=output_dir,
    )

    assert package["route"] == "RVC_MELODY"
    assert package["voice_track"]["path"] == str(output_dir / "voice.wav")
    assert package["effect_track"]["path"] == str(output_dir / "effect.wav")
    assert (output_dir / "package.json").is_file()
    assert not (output_dir / "rvc_input.wav").exists()
    assert not (output_dir / "rvc_raw.wav").exists()
    assert _sha(original) == original_hash
    assert _validate(tmp_path, original, package).route == "RVC_MELODY"


def test_audio_package_builder_routes_unapproved_audio_to_silence_plus_effect(tmp_path, monkeypatch):
    original = tmp_path / "music.mp4"
    original.write_bytes(b"synthetic original")
    source_wav = tmp_path / "music.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    original_hash = _sha(original)
    item = Item("source-1", "telegram-2", "source1_telegram2", original)
    directive = {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": "MUSIC_ONLY",
        "language": "unknown",
        "speech_detected": False,
        "music_detected": True,
        "confidence": 0.96,
        "rvc_eligible": False,
        "original_sha256_before": original_hash,
        "original_sha256_after": original_hash,
    }

    monkeypatch.setattr(audio_package, "_tool", lambda name, env_name: f"mock-{name}")
    monkeypatch.setattr(audio_package, "_media_duration", lambda path, tool: 1.0)
    monkeypatch.setattr(
        audio_package,
        "_run",
        lambda command, label: _write_wav(Path(command[-1]), sample_rate=44100, channels=2),
    )

    from armored_core.audio import rvc
    def forbidden_rvc(*args, **kwargs):
        raise AssertionError("RVC must not run for MUSIC_ONLY")
    monkeypatch.setattr(rvc, "converter_voz", forbidden_rvc)

    package = audio_package.prepare_audio_package(
        directive,
        item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        output_dir=tmp_path / "storage" / "validation" / "batch2" / "source1" / "telegram2",
    )

    assert package["route"] == "EFFECT_ONLY"
    assert package["rvc_model"] is None
    assert package["rvc_eligible"] is False
    assert _validate(tmp_path, original, package).route == "EFFECT_ONLY"


def test_audio_package_builder_fails_closed_when_music_evidence_is_unknown(tmp_path):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    digest = _sha(original)
    item = Item("source-1", "telegram-3", "source1_telegram3", original)
    directive = {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": None,
        "confidence": 0.99,
        "rvc_eligible": True,
        "original_sha256_before": digest,
        "original_sha256_after": digest,
    }

    with pytest.raises(audio_package.AudioPackageError, match="unsafe RVC"):
        audio_package.prepare_audio_package(
            directive,
            item,
            workspace_root=tmp_path,
            effect_source_path=effect_source,
            output_dir=tmp_path / "storage" / "validation" / "batch3" / "source1" / "telegram3",
        )
