from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import AudioClass, SpeechDecision
from .media import prepare_wav_asset


class ManifestProcessor:
    """Experimental processor driven by explicit reviewed labels.

    This is deliberately not advertised as automatic classification. It lets
    the bulk/storage/SQLite contract be tested without inventing model outputs.
    Replace this provider only after a model-backed classifier is validated.
    """

    def __init__(
        self,
        manifest_path: Path,
        *,
        model: str = "UVR-MDX-NET-Voc_FT.onnx",
        timeout_seconds: int = 7200,
    ) -> None:
        self.manifest_path = manifest_path.resolve()
        self.model = model
        self.timeout_seconds = timeout_seconds
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Manifest must be a JSON array of item decisions")
        self.decisions: dict[tuple[str, str], dict[str, Any]] = {}
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError("Each manifest entry must be a JSON object")
            key = (str(row.get("source_id", "")), str(row.get("item_id", "")))
            if not all(key):
                raise ValueError("Every manifest entry needs source_id and item_id")
            if key in self.decisions:
                raise ValueError(f"Duplicate manifest entry: {key}")
            self.decisions[key] = row

    def analyze(self, video_path: Path, source_id: str | None = None, item_id: str | None = None) -> SpeechDecision:
        # The runner's protocol currently supplies only video_path; resolve the
        # row by its normalized path to avoid guessing by filename alone.
        matches = [
            row for row in self.decisions.values()
            if Path(str(row.get("video_path", ""))).expanduser().resolve() == video_path.resolve()
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one manifest decision for {video_path}; found {len(matches)}"
            )
        row = matches[0]
        decision = SpeechDecision(
            audio_class=AudioClass(str(row["audio_class"])),
            language=row.get("language"),
            speech_detected=bool(row.get("speech_detected", False)),
            music_detected=row.get("music_detected"),
            confidence=None if row.get("confidence") is None else float(row["confidence"]),
            rationale=str(row.get("rationale", "Reviewed local manifest label")),
        )
        decision.validate()
        return decision

    def prepare_wav(self, video_path: Path, wav_path: Path, decision: SpeechDecision) -> None:
        """Delegate WAV preparation to the canonical Speech media implementation."""
        prepare_wav_asset(
            video_path,
            wav_path,
            decision,
            model=self.model,
            timeout_seconds=self.timeout_seconds,
        )
