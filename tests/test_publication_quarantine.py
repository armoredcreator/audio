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


def _approved(db: Database, storage: Storage, message_id: str, *, state=State.RECEIVED):
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


def test_unknown_receipts_are_quarantined_and_assigned_safe_items_reach_dry_run_barrier(
    tmp_path, monkeypatch
):
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
    # Reproduce the legacy inconsistent flag exactly; the status remains the
    # authority and must not be rewritten to CONFIRMED by startup.
    db.conn.execute(
        "UPDATE publications SET confirmed=1,verification_status='SENT_UNVERIFIED' "
        "WHERE content_id=?", (legacy_1040,),
    )
    db.conn.commit()

    budget = DurableCatchUpBudget(
        storage.database / "catch-up-work-budget.sqlite3", "lote-001", 3
    )
    assert budget.claim([ids["291"], ids["292"], ids["293"]]) == {
        ids["291"], ids["292"], ids["293"]
    }
    before_291 = dict(db.get(ids["291"]).__dict__)
    before_291_events = [tuple(row) for row in db.conn.execute(
        "SELECT old_state,new_state,reason,created_at FROM state_events "
        "WHERE content_id=? ORDER BY id", (ids["291"],)
    )]
    before_receipts = {
        item_id: tuple(db.publication(item_id))
        for item_id in (*ids.values(), legacy_1040)
    }

    routes = (SimpleNamespace(
        source=SimpleNamespace(source_id="-1003788989075"),
        hub=SimpleNamespace(chat_id="-1004341972306", topic_id=228),
    ),)
    hub = ArmoredHub(root, db, routes=routes)
    monkeypatch.setattr(hub, "_video_metadata", lambda _path: (1080, 1920, 2))
    coordinator = Coordinator(
        db, storage, SimpleNamespace(), SimpleNamespace(), hub,
        source=_ThreeSourceEmpty(), catch_up_budget=budget,
    )

    summary = coordinator.startup_reconciler.run()
    quarantine = coordinator.publication_quarantine
    assert summary["publication_ambiguous"] == 5
    assert {item_id for item_id in (*ids.values(), legacy_1040) if quarantine.contains(item_id)} == {
        *ids.values(), legacy_1040,
    }
    assert all(db.publication(item_id)["verification_status"] != "CONFIRMED"
               for item_id in (*ids.values(), legacy_1040))
    assert tuple(db.publication(legacy_1040)) == before_receipts[legacy_1040]
    assert quarantine.observed_outcomes() == {"UNKNOWN"}
    assert quarantine.quarantined_ids() == {*ids.values(), legacy_1040}

    # Recovery continues past all fenced rows without changing item 291 or
    # issuing any publication check/send for them.
    coordinator.recover_pending()
    assert dict(db.get(ids["291"]).__dict__) == before_291
    assert [tuple(row) for row in db.conn.execute(
        "SELECT old_state,new_state,reason,created_at FROM state_events "
        "WHERE content_id=? ORDER BY id", (ids["291"],)
    )] == before_291_events
    assert budget.assigned_ids() == {ids["291"], ids["292"], ids["293"]}

    events = []
    original_pipeline_run = coordinator.pipeline.run

    def pipeline_run(item_id, *, stop_after_ia=False, **kwargs):
        current = db.get(item_id)
        if stop_after_ia and current.state == State.RECEIVED:
            events.append(f"ia:{current.telegram_message_id}")
            db.transition(item_id, State.SPEECH, "ia-complete")
            return None
        return original_pipeline_run(item_id, stop_after_ia=stop_after_ia, **kwargs)

    coordinator.pipeline.run = pipeline_run

    def speech_studio(item_ids):
        outputs = {}
        for item_id in item_ids:
            item = db.get(item_id)
            events.append(f"speech:{item.telegram_message_id}")
            events.append(f"studio:{item.telegram_message_id}")
            db.transition(item_id, State.STUDIO_BATCH, "fixture-v5-render-start")
            output = storage.result(
                item_id, item.affiliate_url, item.affiliate_name, source_id=item.source_id
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(item.original_path.read_bytes() + b"-clone-render")
            outputs[item_id] = output
        db.complete_speech_studio_batch(outputs)

    coordinator._run_speech_studio_batch = speech_studio

    async def no_download_stock(_self, *, content_ids=None):
        assert set(content_ids or ()) == {ids["292"], ids["293"]}
        return {
            "errors": [], "processed": 0,
            "outcomes": {"approved_missing_original": 0, "skipped_existing_original": 2},
        }

    monkeypatch.setattr(ArmoredStock, "run", no_download_stock)

    # The exact assigned batch gets through IA and the Speech/Studio boundary;
    # real Hub dry-run defers both sends and leaves the prepared rows in PUBLISHING.
    import asyncio

    first = asyncio.run(coordinator._run_staged_catch_up_async())
    assert set(first) == {ids["292"], ids["293"]}
    assert db.get(ids["291"]).state is State.RECOVERY
    assert db.get(ids["292"]).state is State.PUBLISHING
    assert db.get(ids["293"]).state is State.PUBLISHING
    assert all(db.publication(item_id)["verification_status"] == "PENDING"
               for item_id in (ids["292"], ids["293"]))
    assert all(db.publication(item_id)["confirmed"] == 0
               for item_id in (ids["292"], ids["293"]))
    assert budget.assigned_count == 3 and budget.remaining == 0
    assert db.historical_complete() is False
    assert db.conn.execute("SELECT COUNT(*) FROM sync_topics").fetchone()[0] == 0
    assert set(events) == {
        "ia:292", "ia:293", "speech:292", "speech:293", "studio:292", "studio:293",
    }

    for item_id in (ids["292"], ids["293"]):
        monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "0")
        with pytest.raises(PublicationDeferredError, match="explicit reconciliation required"):
            hub.publish_once(db.get(item_id))
        with pytest.raises(PublicationDeferredError, match="explicit reconciliation required"):
            hub.publish(db.get(item_id))
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")

    # Same run ID is idempotent: Hub alone is retried, no costly stage repeats,
    # no receipt/checkpoint/status is inferred, and its three IDs stay fixed.
    before_events = list(events)
    receipts_before_resume = {item_id: tuple(db.publication(item_id)) for item_id in ids.values()}
    coordinator.startup_reconciler.run()
    coordinator.recover_pending()
    asyncio.run(coordinator._run_staged_catch_up_async())
    assert events == before_events
    assert budget.assigned_ids() == {ids["291"], ids["292"], ids["293"]}
    assert budget.assigned_count == 3
    assert {item_id: tuple(db.publication(item_id)) for item_id in ids.values()} == receipts_before_resume
    assert tuple(db.publication(ids["294"])) == before_receipts[ids["294"]]
    assert db.get(ids["291"]).state is State.RECOVERY
    assert all(db.get(item_id).state is State.PUBLISHING for item_id in (ids["292"], ids["293"]))
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
