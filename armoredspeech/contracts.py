from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AudioClass(StrEnum):
    PT_BR_NARRATION = "PT_BR_NARRATION"
    PT_BR_NARRATION_WITH_MUSIC = "PT_BR_NARRATION_WITH_MUSIC"
    MUSIC_ONLY = "MUSIC_ONLY"
    NO_AUDIO = "NO_AUDIO"
    NO_USABLE_NARRATION = "NO_USABLE_NARRATION"
    FOREIGN_SPEECH = "FOREIGN_SPEECH"
    UNKNOWN = "UNKNOWN"


class SpeechStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SpeechDecision:
    audio_class: AudioClass
    language: str | None
    speech_detected: bool
    music_detected: bool | None
    confidence: float | None
    rationale: str

    @property
    def rvc_eligible(self) -> bool:
        """Fail closed: only a confirmed PT-BR narration route can use RVC."""
        return self.audio_class in {
            AudioClass.PT_BR_NARRATION,
            AudioClass.PT_BR_NARRATION_WITH_MUSIC,
        } and self.language in {"pt", "pt-br", "pt_br"} and self.speech_detected

    @property
    def needs_vocal_separation(self) -> bool:
        return self.audio_class is AudioClass.PT_BR_NARRATION_WITH_MUSIC and self.rvc_eligible

    def validate(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.audio_class is AudioClass.NO_AUDIO and self.speech_detected:
            raise ValueError("NO_AUDIO cannot have speech_detected=True")
        if self.audio_class is AudioClass.FOREIGN_SPEECH and self.language in {"pt", "pt-br", "pt_br"}:
            raise ValueError("FOREIGN_SPEECH cannot be labeled as Portuguese")
        if self.audio_class is AudioClass.UNKNOWN and self.rvc_eligible:
            raise ValueError("UNKNOWN must never be eligible for RVC")
