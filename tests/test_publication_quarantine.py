from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ArmoredHub.service import ArmoredHub
from ArmoredStock.service import ArmoredStock
from armored_core.catch_up_budget import DurableCatchUpBudget
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.publication_quarantine import PublicationQuarantine
from armored_core.services import PublicationDeferredError, SyncService
from armored_core.storage import Storage


def _approved(db: Database, storage: Storage, message_id: str, *, state=State.RECEIVED, publication_attempt=True):
    source = storage.root / "fixtures" / f"{message_id}.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(f"immutable-original-{message_id}".encode())
    item_id = SyncService(db, storage).ingest(
        source,
        message_id,
        source_id="-1003788989075",
        topic_id=287,
        original_url=f"https://s.shopee.com.br/source-{message_id}",
    )
    db.set_vision(
        item_id,
        "Produto de exemplo",
        f"https://s.shopee.com.br/affiliate-{message_id}",
        publication_caption="Detalhe 🧡 especial\n#casa",
        ia_context={"shopId": "10", "itemId": message_id, "productName": "Produto de exemplo"},
    )
    if state != State.RECEIVED:
        db.transition(item_id, state, "fixture-prior-interruption")
    if publication_attempt:
        db.publication_started(item_id, "-1004341972306", 228)
    return item_id


class _ThreeSourceEmpty:
    sources = tuple(SimpleNamespace(source_id=f"source-{number}") for number in range(1, 4))
    historical_materialization_failed = False
    historical_scan_exhausted = False

    def set_run_candidate_limit(self, limit):
        self.limit = limit

    async def iter_historical_candidates_async(self):
        if False:
            yield None


class _CompleteSource(_ThreeSourceEmpty):
    def is_historical_complete(self):
        return True


def test_quarantine_blocks_every_stage_without_changing_lote001_or_existing_renders(
    tmp_path, monkeypatch
):
    import asyncio
    import hashlib

    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    monkeypatch.setenv("ARMORED_CREATOR_BOT_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("ARMORED_CREATOR_GROUP_ID", "-1004341972306")
    monkeypatch.setenv("ARMORED_HUB_TOPIC_ID", "228")
    root = tmp_path / "clone"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    ids = {
        "291": _approved(db, storage, "291", state=State.RECOVERY),
        "292": _approved(db, storage, "292"),
        "293": _approved(db, storage, "293"),
        "294": _approved(db, storage, "294"),
    }
    legacy_1040 = _approved(db, storage, "1040", state=State.RECOVERY)
    db.publication_message_sent(legacy_1040, "1339")
    db.conn.execute(
        "UPDATE publications SET confirmed=1,verification_status='SENT_UNVERIFIED' "
        "WHERE content_id=?", (legacy_1040,),
    )
    db.conn.commit()

    # 292/293 model the real renders already produced before quarantine. Their
    # bytes, render boundary, item state and receipts must remain untouched.
    render_hashes = {}
    for key in ("292", "293"):
        item_id = ids[key]
        db.transition(item_id, State.STUDIO_BATCH, "fixture-render-existing")
        output = storage.result(item_id, "https://s.shopee.com.br/affiliate", "Produto", source_id="-1003788989075")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"preexisting-approved-render-" + key.encode())
        db.complete_speech_studio_batch({item_id: output})
        render_hashes[item_id] = hashlib.sha256(output.read_bytes()).hexdigest()

    budget = DurableCatchUpBudget(storage.database / "catch-up-work-budget.sqlite3", "lote-001", 3)
    assert budget.claim([ids["291"], ids["292"], ids["293"]]) == {ids["291"], ids["292"], ids["293"]}
    receipts_before = {item_id: tuple(db.publication(item_id)) for item_id in (*ids.values(), legacy_1040)}
    before_291 = dict(db.get(ids["291"]).__dict__)
    routes = (SimpleNamespace(source=SimpleNamespace(source_id="-1003788989075"), hub=SimpleNamespace(chat_id="-1004341972306", topic_id=228)),)
    hub = ArmoredHub(root, db, routes=routes)
    coordinator = Coordinator(db, storage, SimpleNamespace(), SimpleNamespace(), hub, source=_ThreeSourceEmpty(), catch_up_budget=budget)
    summary = coordinator.startup_reconciler.run()
    quarantine = coordinator.publication_quarantine
    assert summary["publication_ambiguous"] == 5
    assert quarantine.quarantined_ids() == {*ids.values(), legacy_1040}
    assert quarantine.observed_outcomes() == {"UNKNOWN"}

    stage_calls = []
    hub_attempts = []
    original_publish_once = hub.publish_once
    hub.publish_once = lambda item: (hub_attempts.append(item.content_id), original_publish_once(item))[1]
    coordinator.recover_pending()
    for item_id in (*ids.values(), legacy_1040):
        coordinator.run(item_id)
        coordinator.pipeline.run(item_id)
        with pytest.raises(RuntimeError, match="speech-studio-blocked-by-publication-quarantine"):
            coordinator._run_speech_studio_batch([item_id])
    first = asyncio.run(coordinator._run_staged_catch_up_async())
    assert first == []
    assert stage_calls == []
    assert hub_attempts == []
    assert budget.assigned_ids() == {ids["291"], ids["292"], ids["293"]}
    assert budget.assigned_count == 3 and budget.remaining == 0
    assert db.get(ids["291"]).state is State.RECOVERY
    assert db.get(ids["292"]).state is State.PUBLISHING
    assert db.get(ids["293"]).state is State.PUBLISHING
    for item_id, digest in render_hashes.items():
        assert hashlib.sha256(db.get(item_id).result_path.read_bytes()).hexdigest() == digest
    assert {item_id: tuple(db.publication(item_id)) for item_id in (*ids.values(), legacy_1040)} == receipts_before
    assert db.historical_complete() is False
    assert db.conn.execute("SELECT COUNT(*) FROM sync_topics").fetchone()[0] == 0

    # Even with sending enabled, both normal Hub entry points fail closed before
    # touching Telegram; no client or external send method is installed.
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "0")
    for item_id in (ids["292"], ids["293"]):
        with pytest.raises(PublicationDeferredError, match="quarantine"):
            hub.publish_once(db.get(item_id))
        with pytest.raises(PublicationDeferredError, match="quarantine"):
            hub.publish(db.get(item_id))
    assert budget.assigned_ids() == {ids["291"], ids["292"], ids["293"]}
    coordinator.close()


def test_new_safe_lot_reaches_real_hub_dry_run_and_resumes_without_stage_duplication(
    tmp_path, monkeypatch
):
    import asyncio
    import hashlib

    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    monkeypatch.setenv("ARMORED_CREATOR_BOT_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("ARMORED_CREATOR_GROUP_ID", "-1004341972306")
    monkeypatch.setenv("ARMORED_HUB_TOPIC_ID", "228")
    root = tmp_path / "safe-lot"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    ambiguous_id = _approved(db, storage, "40")
    item_id = _approved(db, storage, "500", publication_attempt=False)
    item = db.get(item_id)
    original_hash = hashlib.sha256(item.original_path.read_bytes()).hexdigest()
    budget = DurableCatchUpBudget(storage.database / "catch-up-work-budget.sqlite3", "lote-seguro-001", 1)
    assert budget.claim([item_id]) == {item_id}
    hub = ArmoredHub(root, db)
    coordinator = Coordinator(db, storage, SimpleNamespace(), SimpleNamespace(), hub, source=_ThreeSourceEmpty(), catch_up_budget=budget)
    coordinator.startup_reconciler.run()
    assert coordinator.publication_quarantine.contains(ambiguous_id)
    assert coordinator._quarantine_blocks_historical_cursor() is True
    db.initialize_source_state("-1003788989075")
    db.set_source_sync_topic_checkpoint("-1003788989075", 287, "fixture-topic", 40)
    assert coordinator._quarantine_blocks_historical_cursor() is False
    stage_calls = []

    def pipeline_run(candidate_id, *, stop_after_ia=False, **kwargs):
        current = db.get(candidate_id)
        if stop_after_ia and current.state is State.RECEIVED:
            stage_calls.append("IA")
            db.transition(candidate_id, State.SPEECH, "fixture-ia-complete")
            return
        return original_pipeline_run(candidate_id, stop_after_ia=stop_after_ia, **kwargs)

    original_pipeline_run = coordinator.pipeline.run
    coordinator.pipeline.run = pipeline_run

    def speech_studio(candidate_ids):
        outputs = {}
        for candidate_id in candidate_ids:
            current = db.get(candidate_id)
            stage_calls.extend(["Speech", "Studio"])
            db.transition(candidate_id, State.STUDIO_BATCH, "fixture-v5-render-start")
            output = storage.result(candidate_id, current.affiliate_url, current.affiliate_name, source_id=current.source_id)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(current.original_path.read_bytes() + b"-safe-render")
            outputs[candidate_id] = output
        db.complete_speech_studio_batch(outputs)

    coordinator._run_speech_studio_batch = speech_studio
    hub_calls = []
    publish_once = hub.publish_once
    hub.publish_once = lambda current: (hub_calls.append(current.content_id), publish_once(current))[1]

    first = asyncio.run(coordinator._run_staged_catch_up_async())
    assert first == [item_id]
    assert db.get(item_id).state is State.PUBLISHING
    assert stage_calls == ["IA", "Speech", "Studio"]
    assert hub_calls == [item_id]
    assert hashlib.sha256(db.get(item_id).original_path.read_bytes()).hexdigest() == original_hash
    output_hash = hashlib.sha256(db.get(item_id).result_path.read_bytes()).hexdigest()

    # Reopening the same durable lot rechecks only the Hub dry-run barrier.
    resume = asyncio.run(coordinator._run_staged_catch_up_async())
    assert stage_calls == ["IA", "Speech", "Studio"]
    assert hashlib.sha256(db.get(item_id).result_path.read_bytes()).hexdigest() == output_hash
    assert budget.assigned_ids() == {item_id} and budget.assigned_count == 1
    assert db.get(item_id).state is State.PUBLISHING
    assert db.publication(item_id) is None
    assert db.historical_complete() is False
    assert resume in ([], [item_id])
    coordinator.close()


def test_quarantined_unknown_blocks_catchup_to_live_even_if_source_reports_complete(
    tmp_path, monkeypatch
):
    import asyncio

    root = tmp_path / "live-fence"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id = _approved(db, storage, "291", state=State.RECOVERY)
    quarantine = PublicationQuarantine(storage.database / "publication-quarantine.sqlite3")
    quarantine.quarantine_unknown(
        item_id,
        db.publication(item_id),
        evidence={"item_state": "RECOVERY"},
    )
    quarantine.close()
    coordinator = Coordinator(
        db, storage, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
        source=_CompleteSource(),
    )
    entered_live = []
    async def live_once():
        entered_live.append(True)
        return []
    coordinator.run_live_once_async = live_once
    asyncio.run(coordinator._run_forever_async())
    assert entered_live == []
    assert coordinator.publication_quarantine.contains(item_id)
    assert db.get(item_id).state is State.RECOVERY
    coordinator.close()
