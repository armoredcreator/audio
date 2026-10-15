from __future__ import annotations

import json
import os
from pathlib import Path

from .audio_intelligence import AudioIntelligence, AudioMode
from .contracts import AudioClass, SpeechDecision
from .media import prepare_wav_asset


class ModelProcessor:
    """CPU-first model-backed adapter for the ArmoredSpeech batch runner.

    Requires the optional analysis dependencies and locally cached/downloadable
    Whisper, Silero VAD, and AudioSet/AST models. Any missing/ambiguous evidence
    maps to UNKNOWN and is never approved for RVC.
    """

    def __init__(
        self,
        *,
        separation_model: str = "UVR-MDX-NET-Voc_FT.onnx",
        timeout_seconds: int = 7200,
    ) -> None:
        self.intelligence = AudioIntelligence()
        self.separation_model = separation_model
        self.timeout_seconds = timeout_seconds

    def analyze(self, video_path: Path) -> SpeechDecision:
        result = self.intelligence.analyze(video_path)
        mode_map = {
            AudioMode.NO_AUDIO: AudioClass.NO_AUDIO,
            AudioMode.MUSIC_ONLY: AudioClass.MUSIC_ONLY,
            AudioMode.NOISE: AudioClass.NOISE,
            AudioMode.SPEECH: AudioClass.PT_BR_NARRATION,
            AudioMode.SPEECH_PLUS_MUSIC: AudioClass.PT_BR_NARRATION_WITH_MUSIC,
            AudioMode.FOREIGN_SPEECH: AudioClass.FOREIGN_SPEECH,
        }
        audio_class = mode_map.get(result.mode)
        speech_activity = result.speech_activity_ratio
        min_activity = float(os.getenv("ARMORED_AUDIO_VAD_MIN_ACTIVITY_RATIO", "0.08"))

        speech_model_threshold = float(
            os.getenv("ARMORED_AUDIO_CONTENT_SPEECH_THRESHOLD", "0.35")
        )
        speech_evidence = (
            speech_activity is not None and speech_activity >= min_activity
        ) or (
            result.speech_probability is not None
            and result.speech_probability >= speech_model_threshold
        )

        if audio_class is None:
            # Only call the audio NO_USABLE_NARRATION when both VAD and the
            # independent speech-content score indicate no usable speech.
            # Speech evidence without a verified language stays UNKNOWN.
            audio_class = (
                AudioClass.UNKNOWN if speech_evidence
                else AudioClass.NO_USABLE_NARRATION
            )

        language = (result.language or "unknown").lower()
        if language == "pt":
            language = "pt-br"
        music_detected = None
        if result.music_probability is not None:
            threshold = float(os.getenv("ARMORED_AUDIO_CONTENT_MUSIC_THRESHOLD", "0.25"))
            music_detected = result.music_probability >= threshold
        elif audio_class in {AudioClass.MUSIC_ONLY, AudioClass.PT_BR_NARRATION_WITH_MUSIC}:
            music_detected = True
        elif audio_class is AudioClass.PT_BR_NARRATION:
            music_detected = False

        speech_detected = audio_class in {
            AudioClass.PT_BR_NARRATION,
            AudioClass.PT_BR_NARRATION_WITH_MUSIC,
            AudioClass.FOREIGN_SPEECH,
        } or speech_evidence

        decision = SpeechDecision(
            audio_class=audio_class,
            language=None if language == "unknown" else language,
            speech_detected=speech_detected,
            music_detected=music_detected,
            confidence=float(result.confidence),
            rationale=json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True),
        )
        decision.validate()
        return decision

    def prepare_wav(self, video_path: Path, wav_path: Path, decision: SpeechDecision) -> None:
        prepare_wav_asset(
            video_path,
            wav_path,
            decision,
            model=self.separation_model,
            timeout_seconds=self.timeout_seconds,
        )
