from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from ArmoredSpeech.timeline_mixer import (
    AudioTimelineError,
    build_audio_filter_graph,
    detect_voice_onset_seconds,
)


def _write_pcm(
    path: Path,
    frames: list[int | tuple[int, ...]],
    *,
    sample_rate: int = 1000,
    channels: int = 1,
    sample_width: int = 2,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(sample_width)
        stream.setframerate(sample_rate)
        if sample_width == 2:
            packed = []
            for frame in frames:
                values = frame if isinstance(frame, tuple) else (frame,)
                assert len(values) == channels
                packed.append(struct.pack("<" + "h" * channels, *values))
            stream.writeframes(b"".join(packed))
        else:
            stream.writeframes(b"\x80" * len(frames) * channels)
    return path


def test_detect_voice_onset_returns_first_block_above_threshold(tmp_path: Path) -> None:
    wav_path = _write_pcm(tmp_path / "delayed-voice.wav", [0] * 100 + [6000] * 100)

    assert detect_voice_onset_seconds(wav_path) == pytest.approx(0.1)


def test_detect_voice_onset_averages_stereo_before_measuring_rms(tmp_path: Path) -> None:
    frames = [(0, 0)] * 40 + [(0, 12000)] * 80
    wav_path = _write_pcm(
        tmp_path / "stereo-voice.wav",
        frames,
        channels=2,
    )

    assert detect_voice_onset_seconds(wav_path) == pytest.approx(0.04)


def test_detect_voice_onset_returns_zero_for_silence(tmp_path: Path) -> None:
    wav_path = _write_pcm(tmp_path / "silence.wav", [0] * 200)

    assert detect_voice_onset_seconds(wav_path) == 0.0


def test_detect_voice_onset_rejects_non_16_bit_pcm(tmp_path: Path) -> None:
    wav_path = _write_pcm(
        tmp_path / "unsupported.wav",
        [128] * 100,
        sample_width=1,
    )

    with pytest.raises(AudioTimelineError, match="16-bit PCM WAV"):
        detect_voice_onset_seconds(wav_path)


def test_speech_audio_graph_trims_voice_only_and_preserves_effect_start(tmp_path: Path) -> None:
    timeline = build_audio_filter_graph(
        visual_plan={"video_start": 1.95, "crop_final": None},
        audio_start_seconds=0.12,
        main_duration_seconds=6.0,
        intro=True,
        intro_position="final",
    )
    graph = timeline["filter_graph"]

    assert timeline["composition_owner"] == "ArmoredSpeech"
    assert timeline["audio_start_seconds"] == pytest.approx(0.12)
    assert timeline["video_start_seconds"] == pytest.approx(1.95)
    assert timeline["output_label"] == "[final_a]"
    assert graph.count("atrim=start=0.12:duration=6") == 1
    assert "[2:a]asplit=2[effect_main][effect_intro]" in graph
    assert graph.count("amix=inputs=2:duration=first") == 1
    assert "[main_a][intro_a]concat=n=2:v=0:a=1[final_a]" in graph
    assert "effect_main]atrim=start=" not in graph
    assert "effect_intro]atrim=start=" not in graph


def test_speech_audio_graph_without_intro_uses_main_label_and_no_intro_mix(
    tmp_path: Path,
) -> None:
    timeline = build_audio_filter_graph(
        visual_plan={"video_start": 0.0},
        audio_start_seconds=0.0,
        main_duration_seconds=4.0,
        intro=False,
        intro_position="final",
    )
    graph = timeline["filter_graph"]

    assert timeline["output_label"] == "[main_a]"
    assert timeline["intro_enabled"] is False
    assert "[2:a]anull[effect_main]" in graph
    assert graph.count("amix=inputs=2:duration=first") == 1
    assert "concat=n=2" not in graph
    assert "afade=" not in graph


@pytest.mark.parametrize(
    ("audio_start", "video_start", "duration"),
    [
        (-0.01, 0.0, 4.0),
        (0.0, -0.01, 4.0),
        (0.0, 0.0, 0.0),
        (0.0, float("inf"), 4.0),
    ],
)
def test_speech_audio_graph_rejects_invalid_timeline_values(
    audio_start: float,
    video_start: float,
    duration: float,
) -> None:
    with pytest.raises(AudioTimelineError):
        build_audio_filter_graph(
            visual_plan={"video_start": video_start},
            audio_start_seconds=audio_start,
            main_duration_seconds=duration,
            intro=False,
            intro_position="final",
        )
