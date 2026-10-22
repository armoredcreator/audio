import os

import pytest

from ArmoredSpeech.contracts import AudioClass, SpeechDecision
from ArmoredSpeech.media import prepare_wav_asset


@pytest.mark.parametrize("alias", ["same-path", "hard-link"])
def test_no_audio_fallback_cannot_replace_original(tmp_path, monkeypatch, alias):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"immutable original video")
    output = original
    if alias == "hard-link":
        output = tmp_path / "derived.wav"
        os.link(original, output)
    monkeypatch.setattr("ArmoredSpeech.media.subprocess.run", lambda *a, **k: pytest.fail("must reject before ffmpeg"))
    decision = SpeechDecision(AudioClass.NO_AUDIO, None, False, False, 1.0, "test")
    with pytest.raises(ValueError, match="immutable original"):
        prepare_wav_asset(original, output, decision)
    assert original.read_bytes() == b"immutable original video"
