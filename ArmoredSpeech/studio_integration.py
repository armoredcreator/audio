from __future__ import annotations

"""Explicit lab-only adapter that hands a persisted READY Speech result to Studio.

The caller must provide the Speech-owned registry path; this module never guesses
or opens a Coordinator/production database.
"""

from pathlib import Path
from typing import Protocol

from .studio_bridge import export_audio_directive


class StudioProcessor(Protocol):
    def process(self, item, *, speech_payload: dict | None = None): ...


def process_item_with_speech(
    studio: StudioProcessor,
    item,
    *,
    speech_database: Path,
):
    """Load one READY Speech decision and pass it to Studio for validation.

    Identity uses the canonical source_id + Telegram message ID. Missing,
    non-READY or incompatible Speech results fail closed in the bridge; invalid
    paths, WAV bytes and hashes fail closed in Studio's handoff validator.
    """
    source_id = str(item.source_id)
    item_id = str(item.telegram_message_id)
    directive = export_audio_directive(
        Path(speech_database),
        source_id=source_id,
        item_id=item_id,
        require_files=True,
    )
    return studio.process(item, speech_payload=directive.to_payload())
