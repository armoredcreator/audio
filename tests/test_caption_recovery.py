from __future__ import annotations

import hashlib
from pathlib import Path

from ArmoredHub.service import ArmoredHub
from ArmoredIA.caption.generator import CaptionGenerator
from ArmoredIA.caption.policy import validate_caption
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.pipeline import Pipeline
from armored_core.services import SyncService
from armored_core.storage import Storage


class _UnusedVision:
    def identify(self, _item):
        raise AssertionError("approved item must not rerun Vision")


class _UnusedStudio:
    def process(self, _item):
        raise AssertionError("durable v5 render must not rerun Studio")


class _IA:
    def __init__(self, *, caption="Cantinho 🧡 prático\n#cozinha", fail=False):
        self.calls = 0
        self.contexts = []
        self.caption = caption
        self.fail = fail

    def provider_configuration(self):
        return {"provider": "test-provider", "model": "test-model"}

    def generate_caption_with_evidence(self, context):
        self.calls += 1
        self.contexts.append(dict(context))
        if self.fail:
            raise RuntimeError("provider-unavailable")
        caption = self.caption

        class Provider:
            def generate_candidates(self, _task, _context, _limit):
                return [caption]

        return CaptionGenerator(provider=Provider()).generate_with_evidence(context)


def _prepared_item(tmp_path: Path, db: Database, storage: Storage, *, product=True):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"immutable-original-video")
    item_id = SyncService(db, storage).ingest(source, "295", source_id="source1")
    item = db.get(item_id)
    result = storage.result(
        item_id,
        "https://affiliate.invalid/product",
        "Bancada Suspensa" if product else None,
        source_id=item.source_id,
    )
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_bytes(b"existing-v5-render")
    db.set_vision(
        item_id,
        "Bancada Suspensa" if product else "",
        "https://affiliate.invalid/product" if product else "",
        ia_context={},
    )
    db.transition(item_id, State.IA, "historical-ia-completed-with-caption-lost")
    db.transition(item_id, State.SPEECH, "legacy-stage-complete")
    db.transition(item_id, State.STUDIO_BATCH, "speech-studio-v5-batch-started")
    db.complete_speech_studio_batch({item_id: result})
    return item_id, item.original_path, result


def test_missing_caption_recovers_once_and_hub_dry_run_reuses_v5(monkeypatch, tmp_path):
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    root = tmp_path / "project"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id, original, render = _prepared_item(tmp_path, db, storage)
    before = {
        "original": hashlib.sha256(original.read_bytes()).hexdigest(),
        "render": hashlib.sha256(render.read_bytes()).hexdigest(),
    }
    ia = _IA()
    hub = ArmoredHub(root, db)
    pipeline = Pipeline(db, storage, _UnusedVision(), _UnusedStudio(), hub, ia)

    pipeline.run(item_id)

    item = db.get(item_id)
    assert item.state == State.PUBLISHING
    assert item.publication_caption == "Cantinho 🧡 prático\n#cozinha"
    assert validate_caption(
        item.publication_caption,
        product_name=item.affiliate_name,
        product_context=item.ia_context,
    ) == item.publication_caption
    assert ia.calls == 1
    assert db.caption_recovery(item_id)["status"] == "SUCCEEDED"
    assert db.caption_recovery(item_id)["attempts"] == 1
    assert db.caption_candidates(item_id)
    assert db.publication(item_id) is None
    assert db.has_speech_studio_v5_completion(item_id)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before["original"]
    assert hashlib.sha256(render.read_bytes()).hexdigest() == before["render"]
    trace = (root / "storage" / "logs" / "pipeline_trace.jsonl").read_text(encoding="utf-8")
    assert '"event":"DEFERRED"' in trace

    # Same process/restart semantics: the same durable render and caption pass
    # Hub validation again without invoking IA, Speech, or Studio.
    pipeline.run(item_id)
    assert ia.calls == 1
    assert hashlib.sha256(render.read_bytes()).hexdigest() == before["render"]
    assert db.get(item_id).state == State.PUBLISHING
    assert db.publication(item_id) is None
    db.close()


def test_irrecoverable_caption_context_is_durably_blocked_without_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    root = tmp_path / "project"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id, _original, _render = _prepared_item(tmp_path, db, storage, product=False)
    ia = _IA()
    pipeline = Pipeline(db, storage, _UnusedVision(), _UnusedStudio(), ArmoredHub(root, db), ia)

    pipeline.run(item_id)
    pipeline.run(item_id)
    assert ia.calls == 0
    assert db.caption_recovery(item_id)["status"] == "BLOCKED"
    assert db.caption_recovery(item_id)["reason"] == "approved-product-context-missing"
    assert db.get(item_id).state == State.PUBLISHING
    assert db.publication(item_id) is None

    coordinator = Coordinator(db, storage, _UnusedVision(), _UnusedStudio(), ArmoredHub(root, db), ia=ia)
    coordinator.run(item_id)
    assert ia.calls == 0
    assert db.get(item_id).state == State.PUBLISHING
    coordinator.close()


def test_existing_uncertain_receipt_blocks_caption_mutation_and_ia(monkeypatch, tmp_path):
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    root = tmp_path / "project"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id, _original, _render = _prepared_item(tmp_path, db, storage)
    db.publication_started(item_id)
    db.conn.execute(
        "UPDATE publications SET verification_status='SENT_UNVERIFIED' WHERE content_id=?",
        (item_id,),
    )
    db.conn.commit()
    ia = _IA()
    pipeline = Pipeline(db, storage, _UnusedVision(), _UnusedStudio(), ArmoredHub(root, db), ia)

    pipeline.run(item_id)
    assert ia.calls == 0
    assert db.get(item_id).publication_caption is None
    assert db.caption_recovery(item_id) is None
    assert db.publication(item_id)["verification_status"] == "SENT_UNVERIFIED"
    assert pipeline.publication_quarantine.contains(item_id)
    db.close()


def test_invalid_ai_caption_is_blocked_once_and_does_not_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    root = tmp_path / "project"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id, _original, _render = _prepared_item(tmp_path, db, storage)
    ia = _IA(caption="")
    pipeline = Pipeline(db, storage, _UnusedVision(), _UnusedStudio(), ArmoredHub(root, db), ia)

    pipeline.run(item_id)
    pipeline.run(item_id)
    assert ia.calls == 1
    assert db.caption_recovery(item_id)["status"] == "BLOCKED"
    assert db.get(item_id).publication_caption is None
    assert db.publication(item_id) is None
    db.close()
