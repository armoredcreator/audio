from __future__ import annotations

import json
from pathlib import Path

import pytest

from ArmoredSpeech.contracts import AudioClass, SpeechDecision
from ArmoredSpeech.manifest_processor import ManifestProcessor


@pytest.mark.parametrize(
    "decision",
    [
        SpeechDecision(
            AudioClass.PT_BR_NARRATION, "pt-br", True, False, 0.99, "test"
        ),
        SpeechDecision(
            AudioClass.PT_BR_NARRATION_WITH_MUSIC,
            "pt-br", True, True, 0.99, "test",
        ),
        SpeechDecision(
            AudioClass.NO_AUDIO, None, False, False, 0.99, "test"
        ),
    ],
    ids=["pt-br-narration", "pt-br-with-music", "no-audio"],
)
def test_manifest_processor_uses_shared_wav_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision: SpeechDecision,
) -> None:
    manifest = tmp_path / "decisions.json"
    manifest.write_text(json.dumps([]), encoding="utf-8")
    processor = ManifestProcessor(
        manifest, model="fixture-model.onnx", timeout_seconds=123
    )

    source = tmp_path / "source.mp4"
    wav = tmp_path / "derived" / "source.wav"
    calls = []

    def fake_prepare(
        video_path: Path,
        wav_path: Path,
        received_decision: SpeechDecision,
        *,
        model: str,
        timeout_seconds: int,
    ) -> None:
        calls.append(
            (video_path, wav_path, received_decision, model, timeout_seconds)
        )

    monkeypatch.setattr(
        "ArmoredSpeech.manifest_processor.prepare_wav_asset",
        fake_prepare,
    )
    processor.prepare_wav(source, wav, decision)

    assert calls == [(source, wav, decision, "fixture-model.onnx", 123)]