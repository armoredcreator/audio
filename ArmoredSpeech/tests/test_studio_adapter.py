from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import pytest

from ArmoredSpeech.studio_adapter import SpeechHandoffError, validate_handoff


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, **overrides):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic disposable video bytes")
    wav_path = tmp_path / "item-1.wav"
    with wave.open(str(wav_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 160)

    payload = {
        "contract_version": 1,
        "source_id": "source-1",
        "item_id": "item-1",
        "original_path": str(original),
        "audio_artifact_path": str(wav_path),
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "rvc_eligible": True,
        "original_sha256_before": _sha(original),
        "original_sha256_after": _sha(original),
    }
    payload.update(overrides)
    return original, wav_path, payload


def test_valid_handoff_checks_real_files_and_hashes(tmp_path):
    original, wav_path, payload = _fixture(tmp_path)
    result = validate_handoff(
        payload,
        workspace_root=tmp_path,
        expected_source_id="source-1",
        expected_item_id="item-1",
        expected_original_path=original,
    )
    assert result.rvc_eligible is True
    assert result.original_sha256 == _sha(original)
    assert result.wav_sha256 == _sha(wav_path)


@pytest.mark.parametrize("field,value", [
    ("contract_version", 99),
    ("source_id", "another-source"),
    ("item_id", "another-item"),
])
def test_wrong_contract_or_identity_fails_closed(tmp_path, field, value):
    _, _, payload = _fixture(tmp_path, **{field: value})
    with pytest.raises(SpeechHandoffError):
        validate_handoff(
            payload, workspace_root=tmp_path,
            expected_source_id="source-1", expected_item_id="item-1",
        )


def test_original_hash_mismatch_fails_closed(tmp_path):
    _, _, payload = _fixture(tmp_path, original_sha256_before="bad-hash")
    with pytest.raises(SpeechHandoffError, match="SHA-256"):
        validate_handoff(
            payload, workspace_root=tmp_path,
            expected_source_id="source-1", expected_item_id="item-1",
        )


def test_wav_outside_workspace_fails_closed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "item-1.wav"
    with wave.open(str(outside), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 160)
    original = workspace / "source.mp4"
    original.write_bytes(b"synthetic video")
    digest = _sha(original)
    payload = {
        "contract_version": 1, "source_id": "source-1", "item_id": "item-1",
        "original_path": str(original), "audio_artifact_path": str(outside),
        "audio_class": "MUSIC_ONLY", "language": "unknown", "speech_detected": False,
        "music_detected": True, "rvc_eligible": False,
        "original_sha256_before": digest, "original_sha256_after": digest,
    }
    with pytest.raises(SpeechHandoffError, match="inside the lab workspace"):
        validate_handoff(
            payload, workspace_root=workspace,
            expected_source_id="source-1", expected_item_id="item-1",
        )


def test_unknown_audio_class_is_rejected(tmp_path):
    _, _, payload = _fixture(tmp_path, audio_class="UNKNOWN", rvc_eligible=False)
    with pytest.raises(SpeechHandoffError, match="Unknown"):
        validate_handoff(
            payload, workspace_root=tmp_path,
            expected_source_id="source-1", expected_item_id="item-1",
        )


def test_rvc_claim_with_music_is_rejected(tmp_path):
    _, _, payload = _fixture(tmp_path, music_detected=True)
    with pytest.raises(SpeechHandoffError, match="RVC"):
        validate_handoff(
            payload, workspace_root=tmp_path,
            expected_source_id="source-1", expected_item_id="item-1",
        )
