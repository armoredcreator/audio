from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil

from armored_core.models import Item
from armored_core.storage import Storage


@dataclass(frozen=True)
class StudioResult:
    working_path: Path | None
    result_path: Path

from .unified import UnifiedStudio


class ArmoredStudio:
    """Canonical Studio boundary: mandatory analysis followed by processing."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.storage = Storage(self.root)
        self.engine = UnifiedStudio(self.root, self.storage)

    def analyze_visual(self, item: Item):
        """Read-only visual preflight; does not inspect or process source audio."""
        return self.engine.analyze_visual(item)

    def process(
        self,
        item: Item,
        *,
        speech_payload: dict | None = None,
        visual_analysis=None,
    ) -> StudioResult:
        if os.getenv("ARMORED_STUDIO_FORCE_COPY") != "1":
            ffmpeg = os.getenv("ARMORED_FFMPEG", "ffmpeg")
            if not shutil.which(ffmpeg):
                raise RuntimeError("FFmpeg não encontrado")

        result_path, _details = self.engine.process(
            item,
            speech_payload=speech_payload,
            visual_analysis=visual_analysis,
        )
        # Studio reads ORIGINAL directly unless a durable WORKING artifact
        # already exists. It does not manufacture a byte-identical copy.
        return StudioResult(
            Path(item.working_path) if item.working_path else None,
            Path(result_path),
        )


def build(root: Path, **_kwargs):
    return ArmoredStudio(root)
