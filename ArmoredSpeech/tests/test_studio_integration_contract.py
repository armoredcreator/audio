from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from ArmoredSpeech.studio_adapter import sha256_file
# This module tests the real Studio contract, whose video-analysis dependencies
# belong to the Studio environment, not ArmoredSpeech's isolated runtime.
pytest.importorskip("cv2", reason="Studio integration contract requires the Studio video dependencies")

from ArmoredStudio.unified import UnifiedStudio


class FakeStorage:
    def __init__(self, result_path: Path):
        self.result_path = result_path

    def result(self, *args, **kwargs):
        return self.result_path


@dataclass
class FakeItem:
    source_id: str
    content_id: str
    telegram_message_id: str
    original_path: Path
    working_path: Path | None = None
    affiliate_url: str | None = None
    affiliate_name: str | None = None


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 160)


def _payload(original: Path, wav_path: Path) -> dict:
    digest = sha256_file(original)
    return {
        "contract_version": 1,
        "source_id": "source-1",
        "item_id": "telegram-1",
        "original_path": str(original),
        "audio_artifact_path": str(wav_path),
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "rvc_eligible": True,
        "original_sha256_before": digest,
        "original_sha256_after": digest,
    }


def test_studio_consumes_validated_speech_handoff_without_touching_original(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic-disposable-video")
    wav_path = tmp_path / "original.wav"
    _wav(wav_path)
    output = tmp_path / "output.mp4"
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    monkeypatch.setenv("ARMORED_STUDIO_FORCE_COPY", "1")
    monkeypatch.setenv("ARMORED_STUDIO_ALLOW_COPY", "1")
    result, details = studio.process(item, speech_payload=_payload(original, wav_path))

    assert result == output
    assert details["mode"] == "test-copy"
    assert output.read_bytes() == original.read_bytes()
    assert sha256_file(original) == _payload(original, wav_path)["original_sha256_after"]


def test_invalid_handoff_does_not_replace_existing_output(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic-disposable-video")
    wav_path = tmp_path / "original.wav"
    _wav(wav_path)
    output = tmp_path / "output.mp4"
    output.write_bytes(b"previous-good-output")
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    payload = _payload(original, wav_path)
    payload["original_sha256_after"] = "wrong-hash"
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    monkeypatch.setenv("ARMORED_STUDIO_FORCE_COPY", "1")
    monkeypatch.setenv("ARMORED_STUDIO_ALLOW_COPY", "1")
    with pytest.raises(RuntimeError, match="handoff inválido"):
        studio.process(item, speech_payload=payload)

    assert output.read_bytes() == b"previous-good-output"
    assert original.read_bytes() == b"synthetic-disposable-video"


@pytest.mark.parametrize(
    ("audio_class", "expected_mode", "expected_music_ratio"),
    [
        ("PT_BR_NARRATION", "SPEECH", 0.0),
        ("PT_BR_NARRATION_WITH_MUSIC", "SPEECH_PLUS_MUSIC", 1.0),
        ("MUSIC_ONLY", "MUSIC_ONLY", 1.0),
        ("NO_AUDIO", "NO_AUDIO", 0.0),
        ("FOREIGN_SPEECH", "FOREIGN_SPEECH", 0.0),
    ],
)
def test_speech_class_maps_to_conservative_studio_route(audio_class, expected_mode, expected_music_ratio):
    payload = {
        "audio_class": audio_class,
        "language": "pt-br" if audio_class == "PT_BR_NARRATION" else "unknown",
        "confidence": 0.95,
    }
    route = UnifiedStudio._speech_audio_override(payload, rvc_eligible=(audio_class == "PT_BR_NARRATION"))
    assert route["mode"] == expected_mode
    assert route["music_ratio"] == expected_music_ratio
    assert route["method"] == "armoredspeech-handoff-v1"



def test_speech_narration_without_explicit_rvc_eligibility_stays_closed():
    payload = {
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "confidence": 0.99,
    }
    route = UnifiedStudio._speech_audio_override(payload, rvc_eligible=False)
    assert route["mode"] == "SPEECH"
    assert route["language_confidence"] == 0.0
    assert route["music_ratio"] == 1.0
    assert route["speech_rvc_eligible"] is False


def test_analysis_engine_uses_speech_override_without_second_audio_classification(tmp_path, monkeypatch):
    import ArmoredStudio.unified as unified

    class NeverClassify:
        def analyze(self, source):
            raise AssertionError("Audio Intelligence must not reclassify a supplied Speech decision")

    monkeypatch.setattr(unified, "obter_informacoes_video", lambda source: {"sucesso": True, "duracao": 1.0})
    monkeypatch.setattr(unified, "analisar_bordas_video", lambda source: {})
    monkeypatch.setattr(unified, "analisar_banner", lambda source: {"banner_detectado": False})
    monkeypatch.setattr(unified, "analisar_corte_banner", lambda source: {})
    monkeypatch.setattr(unified, "detectar_veo", lambda source: {})
    monkeypatch.setattr(unified, "detectar_gemini", lambda source: {})
    monkeypatch.setattr(unified, "criar_plano_exportacao", lambda *args: {"ok": True})
    monkeypatch.setattr(unified, "validar_plano_exportacao", lambda plan: {"valido": True, "erros": []})

    override = {
        "mode": "SPEECH",
        "language": "pt-br",
        "language_confidence": 1.0,
        "music_ratio": 0.0,
        "speech_rvc_eligible": True,
        "method": "armoredspeech-handoff-v1",
    }
    engine = unified.AnalysisEngine(audio_intelligence=NeverClassify())
    result = engine.analyze(tmp_path / "synthetic.mp4", audio_override=override)
    assert result.audio == override
