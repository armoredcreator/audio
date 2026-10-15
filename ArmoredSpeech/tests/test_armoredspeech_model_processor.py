from pathlib import Path
from types import SimpleNamespace

from ArmoredSpeech.audio_intelligence import AudioMode
from ArmoredSpeech.contracts import AudioClass
from ArmoredSpeech.model_processor import ModelProcessor


def fake_result(
    mode, *, language="unknown", speech_activity=0.0,
    speech_probability=None, music_probability=0.0,
):
    return SimpleNamespace(
        mode=mode, language=language, confidence=0.92, duration_seconds=8.0,
        speech_ratio=0.4, music_ratio=0.3,
        language_confidence=0.91 if language != "unknown" else 0.0,
        speech_activity_ratio=speech_activity,
        speech_probability=speech_probability,
        music_probability=music_probability,
        as_dict=lambda: {"mode": mode, "language": language,
                         "speech_activity_ratio": speech_activity,
                         "speech_probability": speech_probability,
                         "music_probability": music_probability},
    )


def test_mixed_portuguese_speech_is_rvc_eligible_and_requires_separation():
    processor = ModelProcessor()
    processor.intelligence.analyze = lambda _path: fake_result(
        AudioMode.SPEECH_PLUS_MUSIC, language="pt", speech_activity=0.6, music_probability=0.8
    )
    result = processor.analyze(Path("sample.mp4"))
    assert result.audio_class is AudioClass.PT_BR_NARRATION_WITH_MUSIC
    assert result.rvc_eligible
    assert result.needs_vocal_separation


def test_unverified_voice_is_unknown_and_never_rvc_eligible():
    processor = ModelProcessor()
    processor.intelligence.analyze = lambda _path: fake_result(
        AudioMode.SPEECH_UNVERIFIED, language="unknown", speech_activity=0.55, music_probability=0.1
    )
    result = processor.analyze(Path("sample.mp4"))
    assert result.audio_class is AudioClass.UNKNOWN
    assert not result.rvc_eligible


def test_unusable_noise_without_detected_voice_is_not_rvc_eligible():
    processor = ModelProcessor()
    processor.intelligence.analyze = lambda _path: fake_result(
        AudioMode.SPEECH_UNVERIFIED, language="unknown", speech_activity=0.01, music_probability=0.05
    )
    result = processor.analyze(Path("sample.mp4"))
    assert result.audio_class is AudioClass.NO_USABLE_NARRATION
    assert not result.rvc_eligible



def test_low_vad_but_independent_speech_evidence_stays_unknown_not_no_usable():
    processor = ModelProcessor()
    processor.intelligence.analyze = lambda _path: fake_result(
        AudioMode.SPEECH_UNVERIFIED,
        language="unknown",
        speech_activity=0.02,
        speech_probability=0.82,
        music_probability=0.15,
    )
    result = processor.analyze(Path("sample.mp4"))
    assert result.audio_class is AudioClass.UNKNOWN
    assert result.speech_detected
    assert not result.rvc_eligible
