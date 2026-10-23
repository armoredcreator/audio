from __future__ import annotations

import json

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.services import SyncService
from armored_core.storage import Storage


class VisionMustNotRun:
    def identify(self, _item):
        raise AssertionError("persisted Vision approval must be reused")


class IACapturesContext:
    def __init__(self):
        self.contexts = []

    def generate_caption(self, context):
        self.contexts.append(context)
        return "Ajuste confortável 🧡 diário\n#conforto"


def test_legacy_approved_item_recovers_minimal_ia_context_without_vision(tmp_path, monkeypatch):
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    storage = Storage(tmp_path)
    db = Database(storage.database / "core.sqlite3")
    original = tmp_path / "source.mp4"
    original.write_bytes(b"immutable-approved-original")
    item_id = SyncService(db, storage).ingest(original, "295", source_id="source1")
    db.set_vision(
        item_id,
        "Tênis Feminino Casual Camurça Detalhe Lateral",
        "https://affiliate.invalid/approved",
        ia_context={},
    )
    ia = IACapturesContext()
    coordinator = Coordinator(
        db, storage, VisionMustNotRun(), None, None, ia=ia,
    )
    original_sha = original.read_bytes()
    try:
        coordinator.pipeline.run(item_id, stop_after_ia=True)
        current = db.get(item_id)
        assert current.state is State.SPEECH
        assert current.ia_context == {"productName": "Tênis Feminino Casual Camurça Detalhe Lateral"}
        assert ia.contexts == [current.ia_context]
        assert current.publication_caption == "Ajuste confortável 🧡 diário\n#conforto"
        assert original.read_bytes() == original_sha
        assert db.publication(item_id) is None
        trace = storage.logs / "pipeline_trace.jsonl"
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        assert any(
            row.get("event") == "CONTEXT_RECOVERED"
            and row.get("source") == "persisted_vision_approval"
            for row in events
        )
    finally:
        coordinator.close()
