from __future__ import annotations

from pathlib import Path
import os

from armored_core.database import Database
from armored_core.models import State
from armored_core.pipeline import Pipeline
from armored_core.services import StudioResult, SyncService, VisionResult
from armored_core.storage import Storage


class Vision:
    def identify(self, item):
        return VisionResult(
            "Caneca demonstrativa",
            "https://affiliate.invalid/caneca",
            ia_context={"productName": "Caneca demonstrativa", "category": "casa", "shopId": "700", "itemId": "800"},
        )


class IA:
    def __init__(self):
        self.calls = 0

    def generate_caption(self, context):
        self.calls += 1
        return "Caneca bonita #caneca"


class Studio:
    def __init__(self, storage):
        self.storage = storage
        self.calls = 0

    def process(self, item, **_kwargs):
        self.calls += 1
        output = self.storage.result(
            item.content_id, item.affiliate_url, item.affiliate_name, source_id=item.source_id
        )
        output.write_bytes(item.original_path.read_bytes() + b"-rendered")
        return StudioResult(None, output)


class Publisher:
    def __init__(self):
        self.calls = 0

    def publish(self, _item):
        self.calls += 1
        raise AssertionError("Publisher must never be called by the lab stop gate")


def _fixture(tmp_path):
    storage = Storage(tmp_path)
    db = Database(storage.database / "core.sqlite3")
    source_file = tmp_path / "fixtures" / "source.mp4"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"untouched-original")
    content_id = SyncService(db, storage).ingest(source_file, "501", source_id="source1")
    return storage, db, content_id, source_file


def test_pipeline_stop_after_ia_is_durable_and_does_not_call_studio(tmp_path, monkeypatch):
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    storage, db, content_id, original = _fixture(tmp_path)
    ia, studio, publisher = IA(), Studio(storage), Publisher()
    pipeline = Pipeline(db, storage, Vision(), studio, publisher, ia)

    pipeline.run(content_id, stop_after_ia=True)

    item = db.get(content_id)
    assert item.state is State.SPEECH
    assert item.publication_caption == "Caneca 🧡 bonita\n#caneca"
    assert ia.calls == 1
    assert studio.calls == 0
    assert publisher.calls == 0
    assert original.read_bytes() == b"untouched-original"

    # Re-entry must leave the hand-off durable, not regenerate caption or skip to Hub.
    pipeline.run(content_id, stop_after_ia=True)
    assert ia.calls == 1
    assert studio.calls == 0
    assert publisher.calls == 0
    assert db.get(content_id).state is State.SPEECH
    db.close()


def test_pipeline_stop_after_studio_persists_result_without_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("ARMORED_IA_ENABLED", "0")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "0")
    storage, db, content_id, original = _fixture(tmp_path)
    publisher, studio = Publisher(), Studio(storage)
    # Preapprove a fixture candidate so test isolates the Studio completion boundary.
    db.set_vision(
        content_id, "Caneca demonstrativa", "https://affiliate.invalid/caneca",
        ia_context={"productName": "Caneca", "shopId": "700", "itemId": "800"},
    )
    pipeline = Pipeline(db, storage, Vision(), studio, publisher, ia=None)

    pipeline.run(content_id, stop_after_studio=True)

    item = db.get(content_id)
    assert item.state is State.STUDIO_COMPLETE
    assert item.result_path is not None and item.result_path.is_file()
    assert item.result_path.read_bytes() == b"untouched-original-rendered"
    assert original.read_bytes() == b"untouched-original"
    assert studio.calls == 1
    assert publisher.calls == 0

    # A repeated invocation is safely fenced at the final lab state.
    pipeline.run(content_id, stop_after_studio=True)
    assert studio.calls == 1
    assert publisher.calls == 0
    assert db.get(content_id).state is State.STUDIO_COMPLETE
    db.close()
