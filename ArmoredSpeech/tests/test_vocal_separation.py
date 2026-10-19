from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from ArmoredSpeech import studio_integration
from ArmoredSpeech.vocal_separation import (
    VocalSeparationError,
    separate_vocal_stem,
    validate_vocal_separation_receipt,
)


def _write_wav(path: Path, *, rate: int = 44100, channels: int = 2, seconds: float = 1.0, silent: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = b"\x00\x00" if silent else b"\x20\x10"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(sample * int(rate * seconds) * channels)


def test_vocal_separation_writes_receipt_bound_to_source_and_output(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable video")
    output = tmp_path / "storage" / "derived" / "vocals.wav"
    calls = []

    monkeypatch.setattr(
        "ArmoredSpeech.vocal_separation._resolve_executable",
        lambda name, env: "ffmpeg.exe" if name == "ffmpeg" else "audio-separator.exe",
    )

    def fake_run(command, **kwargs):
        calls.append(command)
        executable = Path(command[0]).name.casefold()
        if "audio-separator" in executable:
            output_dir = Path(command[command.index("--output_dir") + 1])
            _write_wav(output_dir / "source_(Vocals).wav")
            _write_wav(output_dir / "source_(Instrumental).wav")
        else:
            input_path = Path(command[command.index("-i") + 1])
            output_path = Path(command[-1])
            if input_path == source:
                _write_wav(output_path)
            else:
                _write_wav(output_path, rate=16000, channels=1)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("ArmoredSpeech.vocal_separation.subprocess.run", fake_run)
    returned, receipt = separate_vocal_stem(
        source,
        output_wav=output,
        workspace_root=tmp_path,
        model_filename="fake-vocals.onnx",
    )

    assert returned == output
    assert output.is_file()
    assert receipt["status"] == "READY"
    assert receipt["verified"] is True
    assert receipt["model_filename"] == "fake-vocals.onnx"
    assert validate_vocal_separation_receipt(source, output, workspace_root=tmp_path) is not None
    assert len(calls) == 3
    assert source.read_bytes() == b"immutable video"


def test_stale_or_missing_separation_receipt_is_not_trusted(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable video")
    vocal = tmp_path / "vocals.wav"
    _write_wav(vocal, rate=16000, channels=1)
    assert validate_vocal_separation_receipt(source, vocal, workspace_root=tmp_path) is None


def test_mixed_directive_uses_verified_stem_and_authorizes_rvc(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable video")
    vocal = tmp_path / "separated" / "vocals.wav"
    _write_wav(vocal, rate=16000, channels=1)
    receipt = {
        "schema_version": 1,
        "status": "READY",
        "source_video_path": str(source),
        "source_video_sha256": "a" * 64,
        "vocal_path": str(vocal),
        "vocal_sha256": "b" * 64,
        "model_filename": "fake-vocals.onnx",
        "duration_seconds": 1.0,
    }
    monkeypatch.setattr(
        studio_integration,
        "validate_vocal_separation_receipt",
        lambda *_args, **_kwargs: receipt,
    )
    item = SimpleNamespace(source_id="source-test", telegram_message_id="004", original_path=source)
    payload = {
        "audio_class": "PT_BR_NARRATION_WITH_MUSIC",
        "audio_artifact_path": str(vocal),
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": True,
        "rvc_eligible": False,
        "rvc_candidate_eligible": True,
        "requires_vocal_separation": True,
    }

    prepared = studio_integration.prepare_directive_payload(
        item,
        payload,
        workspace_root=tmp_path,
        package_root=tmp_path / "new-run",
    )

    assert prepared["audio_class"] == "PT_BR_NARRATION"
    assert prepared["source_audio_class"] == "PT_BR_NARRATION_WITH_MUSIC"
    assert prepared["audio_artifact_path"] == str(vocal)
    assert prepared["music_detected"] is False
    assert prepared["rvc_eligible"] is True
    assert prepared["vocal_separation"]["rvc_authorized_after_validation"] is True


def test_mixed_directive_without_pt_br_rvc_candidate_fails_closed(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable video")
    item = SimpleNamespace(source_id="s", telegram_message_id="010", original_path=source)
    payload = {
        "audio_class": "PT_BR_NARRATION_WITH_MUSIC",
        "audio_artifact_path": str(tmp_path / "source.wav"),
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": True,
        "rvc_candidate_eligible": False,
    }
    with pytest.raises(VocalSeparationError, match="MIXED_NARRATION_NOT_RVC_CANDIDATE"):
        studio_integration.prepare_directive_payload(
            item,
            payload,
            workspace_root=tmp_path,
            package_root=tmp_path / "new-run",
        )
