from pathlib import Path

import numpy as np

from ArmoredSpeech.audio_intelligence import AudioIntelligence, AudioMode


def analyzer_for(
    tmp_path: Path,
    *,
    speech_activity: float,
    speech_probability: float,
    music_probability: float,
    noise_probability: float = 0.0,
    language: str = "unknown",
    language_confidence: float = 0.0,
) -> AudioIntelligence:
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"fixture")
    analyzer = AudioIntelligence(ffmpeg="ffmpeg")
    samples = np.full(32_000, 0.1, dtype=np.float32)
    analyzer._decode = lambda _source: (samples, 2.0)
    analyzer._detect_speech_activity = lambda _samples: speech_activity
    analyzer._detect_audio_content_probabilities = (
        lambda _samples: (speech_probability, music_probability, noise_probability)
    )
    analyzer._detect_language = lambda _samples: (language, language_confidence)
    return analyzer


def test_audio_set_logits_use_independent_sigmoid_scores():
    scores = AudioIntelligence._multilabel_probabilities(
        np.array([2.0, 2.0, -2.0], dtype=np.float32)
    )
    assert scores[0] > 0.85
    assert scores[1] > 0.85
    assert scores[2] < 0.15
    # Unlike softmax, two labels can both have high confidence.
    assert scores[0] + scores[1] > 1.5


def test_portuguese_narration_with_music_is_mixed_mode(tmp_path):
    analyzer = analyzer_for(
        tmp_path, speech_activity=0.95, speech_probability=0.98,
        music_probability=0.78, language="pt", language_confidence=0.99,
    )
    result = analyzer.analyze(tmp_path / "sample.mp4")
    assert result.mode == AudioMode.SPEECH_PLUS_MUSIC
    assert result.language == "pt"
    assert result.speech_probability == 0.98
    assert result.music_probability == 0.78


def test_portuguese_narration_without_music_is_speech_mode(tmp_path):
    analyzer = analyzer_for(
        tmp_path, speech_activity=0.90, speech_probability=0.95,
        music_probability=0.08, language="pt", language_confidence=0.98,
    )
    result = analyzer.analyze(tmp_path / "sample.mp4")
    assert result.mode == AudioMode.SPEECH


def test_music_without_speech_is_music_only(tmp_path):
    analyzer = analyzer_for(
        tmp_path, speech_activity=0.0, speech_probability=0.02,
        music_probability=0.91,
    )
    result = analyzer.analyze(tmp_path / "sample.mp4")
    assert result.mode == AudioMode.MUSIC_ONLY
    assert result.language == "unknown"


def test_audio_set_speech_can_recover_short_foreign_speech_missed_by_vad(tmp_path):
    analyzer = analyzer_for(
        tmp_path, speech_activity=0.041, speech_probability=0.91,
        music_probability=0.84, language="en", language_confidence=0.96,
    )
    result = analyzer.analyze(tmp_path / "sample.mp4")
    assert result.mode == AudioMode.FOREIGN_SPEECH
    assert result.language == "en"


def test_noise_without_voice_or_music_is_classified_as_noise(tmp_path):
    analyzer = analyzer_for(
        tmp_path, speech_activity=0.0, speech_probability=0.019,
        music_probability=0.009, noise_probability=0.076,
    )
    result = analyzer.analyze(tmp_path / "sample.mp4")
    assert result.mode == AudioMode.NOISE
    assert result.noise_probability == 0.076


def test_inconclusive_voice_and_music_evidence_stays_unverified(tmp_path):
    analyzer = analyzer_for(
        tmp_path, speech_activity=0.01, speech_probability=0.08,
        music_probability=0.10,
    )
    result = analyzer.analyze(tmp_path / "sample.mp4")
    assert result.mode == AudioMode.SPEECH_UNVERIFIED


def test_silent_audio_short_circuits_to_no_audio(tmp_path):
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"fixture")
    analyzer = AudioIntelligence(ffmpeg="ffmpeg")
    analyzer._decode = lambda _source: (np.zeros(32_000, dtype=np.float32), 2.0)
    analyzer._detect_speech_activity = lambda _samples: (_ for _ in ()).throw(
        AssertionError("VAD must not run for silent audio")
    )
    result = analyzer.analyze(video)
    assert result.mode == AudioMode.NO_AUDIO
