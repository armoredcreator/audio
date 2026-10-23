from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from armored_core.catch_up_budget import (
    CatchUpBudgetError,
    ContinuousOperationBudget,
    DurableCatchUpBudget,
)
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.publication_quarantine import PublicationQuarantine
from armored_core.services import SyncService, VisionResult
from armored_core.storage import Storage


def test_continuous_allowance_is_persistent_and_cannot_be_reset_with_new_id(tmp_path):
    now = [datetime(2026, 10, 23, 12, tzinfo=timezone.utc)]
    path = tmp_path / "continuous.sqlite3"
    budget = ContinuousOperationBudget(
        path, "production", 3, 2, now=lambda: now[0]
    )
    first = budget.open_batch()
    assert first is not None and first[1] == 2
    batch = DurableCatchUpBudget(
        tmp_path / "batch.sqlite3", first[0], first[1], continuous_budget=budget
    )
    assert batch.claim(("a", "b", "c")) == {"a", "b"}
    assert budget.used_today == 2
    assert budget.remaining == 1
    batch.close()
    budget.close_batch(first[0])
    second = budget.open_batch()
    assert second is not None and second[1] == 1
    second_batch = DurableCatchUpBudget(
        tmp_path / "batch.sqlite3", second[0], second[1], continuous_budget=budget
    )
    assert second_batch.claim(("c", "d")) == {"c"}
    assert budget.remaining == 0
    second_batch.close()
    budget.close_batch(second[0])
    assert budget.open_batch() is None
    budget.close()

    with pytest.raises(CatchUpBudgetError, match="cannot-be-reconfigured"):
        ContinuousOperationBudget(path, "different-operation", 100, 10, now=lambda: now[0])
    resumed = ContinuousOperationBudget(path, "production", 3, 2, now=lambda: now[0])
    assert resumed.remaining == 0
    now[0] = datetime(2026, 10, 24, 0, 1, tzinfo=timezone.utc)
    assert resumed.remaining == 3
    next_day = resumed.open_batch()
    assert next_day is not None and next_day[1] == 2
    assert resumed.try_claim("a")  # unique allowance is per UTC day
    assert resumed.used_today == 1
    resumed.close()


class _ContinuousSource:
    source_id = "source-1"

    def __init__(self, db, messages):
        self.db = db
        self.messages = list(messages)
        self.sources = tuple(SimpleNamespace(source_id=f"source-{index}") for index in range(1, 4))
        self.limit = None
        self.historical_scan_exhausted = False
        self.historical_materialization_failed = False
        self.live_polls = 0

    def is_historical_complete(self):
        return self.db.historical_complete()

    def set_run_candidate_limit(self, limit):
        self.limit = limit

    def reset_historical_scan(self):
        self.historical_scan_exhausted = False
        self.historical_materialization_failed = False

    async def iter_historical_candidates_async(self):
        count = 0
        for message in self.messages:
            content_id = self.db.content_id_for(message.telegram_message_id, self.source_id)
            existing = self.db.conn.execute(
                "SELECT 1 FROM items WHERE content_id=?", (content_id,)
            ).fetchone()
            if existing:
                continue
            if count >= self.limit:
                self.historical_scan_exhausted = False
                return
            count += 1
            yield message
        self.historical_scan_exhausted = count < self.limit

    async def materialize_candidate_async(self, _source_id, _message_id, target):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"isolated-real-media-fixture")

    def mark_ingested(self, _message_id):
        pass

    def mark_materialization_failed(self):
        self.historical_materialization_failed = True

    async def fetch_live_candidate_async(self):
        self.live_polls += 1
        return None, {}

    def commit_live_checkpoints(self, _checkpoints):
        pass


class _Vision:
    def identify(self, item):
        return VisionResult(
            f"Product {item.telegram_message_id}",
            f"https://affiliate.invalid/{item.telegram_message_id}",
            ia_context={"productName": f"Product {item.telegram_message_id}"},
        )


class _IA:
    def generate_caption(self, _context):
        return "Belo item 🧡 #casa"


class _Hub:
    def __init__(self, db):
        self.db = db
        self.sent = []

    def publish_once(self, item):
        self.db.publication_started(item.content_id)
        self.sent.append(item.content_id)
        return SimpleNamespace(confirmed=True, message_id=f"confirmed-{item.content_id}")


def test_continuous_coordinator_runs_multiple_batches_then_enters_live_in_clone(tmp_path, monkeypatch):
    root = tmp_path / "clone"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    messages = [
        SimpleNamespace(
            telegram_message_id=str(index), source_id="source-1", topic_id=10,
            topic_name="topic", original_url=f"https://shopee.invalid/{index}",
            source_path=Path(f"fixture-{index}.mp4"), materialize=None,
        )
        for index in range(1, 4)
    ]
    source = _ContinuousSource(db, messages)
    hub = _Hub(db)
    coordinator = Coordinator(
        db, storage, _Vision(), SimpleNamespace(), hub,
        source=source, ia=_IA(),
        continuous_budget=ContinuousOperationBudget(
            storage.database / "continuous.sqlite3", "clone-op", 4, 2
        ),
    )

    def speech_studio(item_ids):
        for item_id in item_ids:
            item = db.get(item_id)
            result = storage.result(
                item.content_id, item.affiliate_url, item.affiliate_name, source_id=item.source_id
            )
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_bytes(item.original_path.read_bytes() + b"-v5-result")
            db.set_result(item_id, result)
            db.transition(item_id, State.PUBLISHING, "speech-studio-v5-complete")

    coordinator._run_speech_studio_batch = speech_studio
    coordinator.run_forever(poll_seconds=0.25, max_cycles=2)

    assert db.historical_complete()
    assert source.live_polls == 2
    assert len(hub.sent) == 3
    ids = [str(row[0]) for row in db.conn.execute("SELECT content_id FROM items")]
    assert all(db.get(item_id).state == State.PUBLISHED for item_id in ids)
    assert coordinator.continuous_budget.used_today == 3
    rows = coordinator.continuous_budget.conn.execute(
        "SELECT status,item_limit FROM continuous_batches ORDER BY sequence"
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [("CLOSED", 2), ("CLOSED", 2)]
    coordinator.close()


def test_continuous_safe_items_run_past_quarantine_already_behind_its_cursor(tmp_path):
    root = tmp_path / "quarantine-continuous-clone"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    old_original = root / "fixtures" / "ambiguous.mp4"
    old_original.parent.mkdir(parents=True)
    old_original.write_bytes(b"ambiguous-original")
    ambiguous_id = SyncService(db, storage).ingest(
        old_original, "1", source_id="source-2", topic_id=20,
        original_url="https://shopee.invalid/ambiguous",
    )
    db.set_vision(ambiguous_id, "Historical", "https://affiliate.invalid/historical")
    db.publication_started(ambiguous_id, "-1004341972306", 1160)
    quarantine = PublicationQuarantine(storage.database / "publication-quarantine.sqlite3")
    quarantine.quarantine_unknown(ambiguous_id, db.publication(ambiguous_id), evidence={"item_state": "RECEIVED"})
    quarantine.close()
    db.initialize_source_state("source-2")
    db.set_source_sync_topic_checkpoint("source-2", 20, "old-topic", 1)

    messages = [
        SimpleNamespace(
            telegram_message_id=str(index), source_id="source-1", topic_id=10,
            topic_name="topic", original_url=f"https://shopee.invalid/{index}",
            source_path=Path(f"safe-fixture-{index}.mp4"), materialize=None,
        )
        for index in range(1, 3)
    ]
    source = _ContinuousSource(db, messages)
    hub = _Hub(db)
    coordinator = Coordinator(
        db, storage, _Vision(), SimpleNamespace(), hub,
        source=source, ia=_IA(),
        continuous_budget=ContinuousOperationBudget(
            storage.database / "continuous.sqlite3", "safe-continuous-op", 3, 2
        ),
    )
    speech_studio_calls = []

    def speech_studio(item_ids):
        speech_studio_calls.extend(item_ids)
        for item_id in item_ids:
            item = db.get(item_id)
            output = storage.result(item_id, item.affiliate_url, item.affiliate_name, source_id=item.source_id)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(item.original_path.read_bytes() + b"-safe-v5")
            db.set_result(item_id, output)
            db.transition(item_id, State.PUBLISHING, "speech-studio-v5-complete")

    coordinator._run_speech_studio_batch = speech_studio
    coordinator.run_forever(poll_seconds=0.25, max_cycles=1)

    safe_ids = {
        db.content_id_for("1", "source-1"),
        db.content_id_for("2", "source-1"),
    }
    assert db.historical_complete()
    assert coordinator.publication_quarantine.contains(ambiguous_id)
    assert not coordinator._quarantine_blocks_historical_cursor()
    assert set(speech_studio_calls) == safe_ids
    assert all(db.get(item_id).state is State.PUBLISHED for item_id in safe_ids)
    assert db.get(ambiguous_id).state is State.RECEIVED
    assert not db.publication(ambiguous_id)["confirmed"]
    assert coordinator.continuous_budget.used_today == 2
    coordinator.close()


def test_continuous_start_drains_publishing_v5_before_live_without_rerender(tmp_path):
    root = tmp_path / "resume-clone"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    original = root / "fixture" / "approved.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"immutable-original")
    item_id = SyncService(db, storage).ingest(
        original, "prior", source_id="source-1", topic_id=10,
        original_url="https://shopee.invalid/known",
    )
    db.set_vision(
        item_id, "Known product", "https://affiliate.invalid/known",
        ia_context={"productName": "Known product"},
    )
    item = db.get(item_id)
    output = storage.result(
        item_id, item.affiliate_url, item.affiliate_name, source_id=item.source_id
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"prior-v5-render")
    db.set_result(item_id, output)
    db.transition(item_id, State.PUBLISHING, "speech-studio-v5-complete")
    db.complete_historical_sync()
    source = _ContinuousSource(db, [])
    hub = _Hub(db)
    budget = ContinuousOperationBudget(
        storage.database / "continuous.sqlite3", "resume-op", 2, 1
    )
    coordinator = Coordinator(
        db, storage, _Vision(), SimpleNamespace(), hub,
        source=source, ia=_IA(), continuous_budget=budget,
    )

    def no_rerender(_item_ids):
        raise AssertionError("previous Speech/Studio v5 result must be reused")

    coordinator._run_speech_studio_batch = no_rerender
    coordinator.run_forever(poll_seconds=0.25, max_cycles=1)

    assert db.get(item_id).state is State.PUBLISHED
    assert db.publication(item_id)["confirmed"]
    assert output.read_bytes() == b"prior-v5-render"
    assert source.live_polls == 1
    assert budget.used_today == 1
    coordinator.close()
