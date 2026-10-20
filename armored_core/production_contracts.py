from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass(frozen=True)
class SourceMessage:
    source_path: Path
    telegram_message_id: str
    source_id: str = "telegram"
    topic_id: int | None = None
    topic_name: str | None = None
    original_url: str | None = None

class SourceAdapter(Protocol):
    def fetch_next(self) -> SourceMessage | None: ...

class ProductionAdapterError(RuntimeError):
    pass
