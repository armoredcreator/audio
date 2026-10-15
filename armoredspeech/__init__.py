"""ArmoredSpeech experimental pipeline.

This package is an isolated prototype. It is not imported by ArmoredCreator
production and does not modify source videos.
"""

from .contracts import AudioClass, SpeechDecision, SpeechStatus

__all__ = ["AudioClass", "SpeechDecision", "SpeechStatus"]
