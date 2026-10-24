from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from armored_core.catch_up_budget import DurableCatchUpBudget
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.services import SyncService, VisionResult, VisionUnresolvedError
from armored_core.storage import Storage
from ArmoredSync.service import MultiTelegramSource, TelegramSource
from armored_core.routing import HubConfig, RouteConfig, SourceConfig
from ArmoredHub.service import ArmoredHub
from ArmoredIA.caption.generator import CaptionGenerator


class _BoundedSource:
    def __init__(self, messages, db=None):
        self.sources = tuple(SimpleNamespace(source_id=f"source-{i}") for i in range(1, 4))
        self.messages = list(messages)
        self.limit = None
        self.yielded = []
        self.downloads = []
        self.db = db
        self.historical_scan_exhausted = False
        self.historical_materialization_failed = False

    def set_run_candidate_limit(self, limit):
        self.limit = limit

    async def iter_historical_candidates_async(self):
        limit = len(self.messages) if self.limit is None else self.limit
        count = 0
        for message in self.messages:
            if self.db is not None:
                content_id = self.db.content_id_for(
                    str(message.telegram_message_id), str(message.source_id)
                )
                row = self.db.conn.execute(
                    "SELECT original_url FROM items WHERE content_id=?", (content_id,)
                ).fetchone()
                if row is not None:
                    stored_url = str(row["original_url"] or "").strip()
                    incoming_url = str(message.original_url or "").strip()
                    if stored_url or not incoming_url:
                        continue
            if count >= limit:
                break
            count += 1
            self.yielded.append(str(message.telegram_message_id))
            yield message
        self.historical_scan_exhausted = count < limit

    def mark_ingested(self, _message_id):
        pass

    def mark_materialization_failed(self):
        self.historical_materialization_failed = True

    async def materialize_candidate_async(self, _source_id, _message_id, target):
        self.downloads.append(str(_message_id))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"downloaded-only-if-approved")


class _Vision:
    def __init__(self, events):
        self.events = events

    def identify(self, item):
        self.events.append(f"vision:{item.telegram_message_id}")
        if str(item.telegram_message_id) == "100":
            return VisionResult(
                "New approved product", "https://affiliate.invalid/9-10",
                ia_context={"productName": "New approved product", "category": "casa", "shopId": "9", "itemId": "10"},
            )
        raise VisionUnresolvedError("no supported Shopee link")


class _IA:
    def __init__(self, events):
        self.events = events

    def generate_caption_with_evidence(self, context):
        self.events.append("ia")
        class Provider:
            def generate_candidates(self, _task, _context, _limit):
                return ["Casa charmosa 🧡 #casa"]

        return CaptionGenerator(provider=Provider()).generate_with_evidence(context)

    def generate_caption(self, context):
        return self.generate_caption_with_evidence(context).caption


class _Publisher:
    def __init__(self, db, events):
        self.db = db
        self.events = events
        self.calls = []

    def publish_once(self, item):
        self.events.append(f"hub:{item.telegram_message_id}")
        self.calls.append({
            "content_id": item.content_id,
            "telegram_message_id": item.telegram_message_id,
            "affiliate_url": item.affiliate_url,
            "caption": item.publication_caption,
            "result_path": item.result_path,
        })
        self.db.publication_started(item.content_id)
        return SimpleNamespace(confirmed=True, message_id=f"tg-{item.telegram_message_id}")


def _message(message_id):
    return SimpleNamespace(
        telegram_message_id=str(message_id), source_id="source-1", topic_id=10,
        topic_name="topic",
        original_url="https://shopee.com.br/product/9/10" if str(message_id) == "100" else None,
        source_path=Path(f"fixture-{message_id}.mp4"), materialize=None,
    )


def _coordinator(root: Path, run_id: str, source, events):
    storage = Storage(root)
    db = getattr(source, "db", None) or Database(storage.database / "armoredcreator.db")
    budget = DurableCatchUpBudget(
        storage.database / "catch-up-work-budget.sqlite3", run_id, 2 if run_id == "same-run" else 1
    )
    coordinator = Coordinator(
        db, storage, _Vision(events), SimpleNamespace(), _Publisher(db, events),
        source=source, ia=_IA(events), catch_up_budget=budget,
    )

    def speech_studio(item_ids):
        for item_id in item_ids:
            item = db.get(item_id)
            events.extend((f"speech:{item.telegram_message_id}", f"studio:{item.telegram_message_id}"))
            result = storage.result(
                item.content_id, item.affiliate_url, item.affiliate_name, source_id=item.source_id
            )
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_bytes(item.original_path.read_bytes() + b"-render")
            db.set_result(item_id, result)
            db.transition(item_id, State.PUBLISHING, "speech-studio-v5-complete")

    coordinator._run_speech_studio_batch = speech_studio
    return coordinator


def test_real_coordinator_limits_persisted_and_new_work_and_resumes_without_duplicates(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "0")
    root = tmp_path / "clone"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    original_source = root / "fixture" / "approved.mp4"
    original_source.parent.mkdir(parents=True)
    original_source.write_bytes(b"historical-original")
    old_id = SyncService(db, storage).ingest(
        original_source, "1", source_id="source-1", topic_id=10,
        original_url="https://shopee.com.br/product/7/8",
    )
    db.set_vision(
        old_id, "Approved product", "https://affiliate.invalid/7-8",
        ia_context={"productName": "Approved product", "category": "casa", "shopId": "7", "itemId": "8"},
    )
    db.close()

    source = _BoundedSource([_message(100), _message(101)], db=Database(storage.database / "armoredcreator.db"))
    events = []
    coordinator = _coordinator(root, "same-run", source, events)
    before_checkpoint = coordinator.db.conn.execute(
        "SELECT COUNT(*) FROM sync_topics"
    ).fetchone()[0]
    coordinator.run_forever()

    assert source.limit == 1  # existing approved item consumed one of the two slots
    assert source.yielded == ["100"]
    assert source.downloads == ["100"]
    assert len(coordinator.catch_up_budget.assigned_ids()) == 2
    assert coordinator.db.get(old_id).state is State.PUBLISHED
    new_id = coordinator.db.content_id_for("100", "source-1")
    assert coordinator.db.get(new_id).state is State.PUBLISHED
    assert "vision:100" in events
    # One generated caption for the fresh item and one bounded repair for the
    # already-approved historical item whose caption was missing.
    assert events.count("ia") == 2
    assert coordinator.db.get(old_id).publication_caption == "Casa 🧡 charmosa\n#casa"
    assert all(f"{stage}:100" in events for stage in ("speech", "studio", "hub"))
    assert all(f"{stage}:1" in events for stage in ("speech", "studio", "hub"))
    assert len([event for event in events if event.startswith("speech:")]) == 2
    assert len([event for event in events if event.startswith("studio:")]) == 2
    assert len([event for event in events if event.startswith("hub:")]) == 2
    assert {
        call["telegram_message_id"]
        for call in coordinator.pipeline.publisher.calls
    } == {"1", "100"}
    assert all(call["result_path"].is_file() for call in coordinator.pipeline.publisher.calls)
    assert coordinator.db.historical_complete() is False
    assert coordinator.db.conn.execute("SELECT COUNT(*) FROM sync_topics").fetchone()[0] == before_checkpoint
    assert len(coordinator.catch_up_budget.assigned_ids()) == 2
    coordinator.close()

    # Same run ID resumes its persisted allowlist and cannot spend a third slot.
    source_after_restart = _BoundedSource(
        [_message(100), _message(101)], db=Database(storage.database / "armoredcreator.db")
    )
    restarted = _coordinator(root, "same-run", source_after_restart, events)
    restarted.run_forever()
    assert source_after_restart.yielded == []
    assert coordinator_id_count(restarted.db) == 2
    assert len([event for event in events if event.startswith("hub:")]) == 2
    restarted.close()

    # A new authorized batch skips the already durable WAITING_VISION message
    # and advances to the next unseen message without repeating earlier stages.
    next_source = _BoundedSource(
        [_message(100), _message(101)], db=Database(storage.database / "armoredcreator.db")
    )
    next_run = _coordinator(root, "next-run", next_source, events)
    next_run.run_forever()
    assert next_source.yielded == ["101"]
    assert next_run.db.get(next_run.db.content_id_for("101", "source-1")).state is State.WAITING_VISION
    assert len([event for event in events if event.startswith("hub:")]) == 2
    next_run.close()


def test_dry_run_holds_at_hub_without_receipt_and_resumes_same_render(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    root = tmp_path / "dry-run-clone"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    source_path = root / "fixture" / "approved.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"approved-original")
    item_id = SyncService(db, storage).ingest(
        source_path, "7", source_id="source-1", topic_id=10,
        original_url="https://shopee.com.br/product/7/8",
    )
    db.set_vision(
        item_id, "Approved product", "https://affiliate.invalid/7-8",
        ia_context={"productName": "Approved product", "category": "casa", "shopId": "7", "itemId": "8"},
    )
    events = []
    source = _BoundedSource([], db=db)
    budget = DurableCatchUpBudget(
        storage.database / "catch-up-work-budget.sqlite3", "dry-run", 1
    )
    route = RouteConfig(
        SourceConfig("source-1", "-1001", "source-1"),
        HubConfig("hub1", "-2001", 228),
    )
    coordinator = Coordinator(
        db, storage, _Vision(events), SimpleNamespace(),
        ArmoredHub(root, db, routes=(route,)),
        source=source, ia=_IA(events), catch_up_budget=budget,
    )

    def speech_studio(item_ids):
        for current_id in item_ids:
            current = db.get(current_id)
            events.extend((f"speech:{current.telegram_message_id}", f"studio:{current.telegram_message_id}"))
            output = storage.result(
                current.content_id, current.affiliate_url, current.affiliate_name,
                source_id=current.source_id,
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(current.original_path.read_bytes() + b"-render")
            db.set_result(current_id, output)
            db.transition(current_id, State.PUBLISHING, "speech-studio-v5-complete")

    coordinator._run_speech_studio_batch = speech_studio
    checkpoint_count = db.conn.execute("SELECT COUNT(*) FROM sync_topics").fetchone()[0]
    coordinator.run_forever()
    first_events = list(events)
    held = db.get(item_id)
    assert held.state is State.PUBLISHING
    assert db.publication(item_id) is None
    assert held.original_path.read_bytes() == b"approved-original"
    assert held.result_path is not None and held.result_path.is_file()
    assert coordinator._has_speech_studio_v5_completion(item_id)
    assert db.conn.execute("SELECT COUNT(*) FROM sync_topics").fetchone()[0] == checkpoint_count
    coordinator.close()

    # Restarting the same bounded run does not rebuild Speech/Studio and still
    # cannot create a Telegram receipt while the Hub gate is closed.
    reopened_db = Database(storage.database / "armoredcreator.db")
    resumed_source = _BoundedSource([], db=reopened_db)
    resumed_budget = DurableCatchUpBudget(
        storage.database / "catch-up-work-budget.sqlite3", "dry-run", 1
    )
    resumed = Coordinator(
        reopened_db, storage, _Vision(events), SimpleNamespace(),
        ArmoredHub(root, reopened_db, routes=(route,)),
        source=resumed_source, ia=_IA(events), catch_up_budget=resumed_budget,
    )
    resumed._run_speech_studio_batch = speech_studio
    resumed.run_forever()
    assert events == first_events
    assert reopened_db.get(item_id).state is State.PUBLISHING
    assert reopened_db.publication(item_id) is None
    resumed.close()

    # With the separate Hub gate explicitly opened in this clone, the same
    # v5 result resumes at Hub and does not repeat IA/Speech/Studio.
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "0")
    final_db = Database(storage.database / "armoredcreator.db")
    final_source = _BoundedSource([], db=final_db)
    final_budget = DurableCatchUpBudget(
        storage.database / "catch-up-work-budget.sqlite3", "dry-run", 1
    )
    final = Coordinator(
        final_db, storage, _Vision(events), SimpleNamespace(),
        _Publisher(final_db, events), source=final_source, ia=_IA(events),
        catch_up_budget=final_budget,
    )
    final._run_speech_studio_batch = speech_studio
    speech_count = sum(event.startswith("speech:") for event in events)
    studio_count = sum(event.startswith("studio:") for event in events)
    final.run_forever()
    assert final_db.get(item_id).state is State.PUBLISHED
    assert final_db.publication(item_id)["confirmed"]
    assert sum(event.startswith("speech:") for event in events) == speech_count
    assert sum(event.startswith("studio:") for event in events) == studio_count
    final.close()


def test_multi_source_discovery_distributes_one_global_candidate_limit():
    class Source:
        def __init__(self, source_id, count):
            self.source_id = source_id
            self.messages = [f"{source_id}-{index}" for index in range(count)]
            self.historical_limit_reached = False
            self.historical_scan_exhausted = False
            self.limit = None

        def is_historical_complete(self):
            return False

        def set_run_candidate_limit(self, limit):
            self.limit = limit

        async def iter_historical_candidates_async(self):
            yielded = self.messages[:self.limit]
            for value in yielded:
                yield SimpleNamespace(source_id=self.source_id, telegram_message_id=value)
            self.historical_scan_exhausted = len(yielded) == len(self.messages)
            self.historical_limit_reached = len(yielded) < len(self.messages)

    source = MultiTelegramSource.__new__(MultiTelegramSource)
    source.sources = (Source("source-1", 2), Source("source-2", 2), Source("source-3", 2))
    source._run_candidate_limit = 3
    source._last_source = None

    async def collect():
        return [message async for message in source.iter_historical_candidates_async()]

    found = asyncio.run(collect())
    assert [message.telegram_message_id for message in found] == [
        "source-1-0", "source-1-1", "source-2-0",
    ]
    assert [candidate.limit for candidate in source.sources] == [3, 1, None]


def test_sync_resumption_skips_reserved_messages_but_allows_a_late_link(tmp_path: Path):
    db = Database(tmp_path / "state.sqlite3")
    with_url = db.reserve_item(
        "1", source_id="source-1", original_url="https://shopee.com.br/product/1/2",
        original_path=tmp_path / "one.mp4",
    )
    without_url = db.reserve_item(
        "2", source_id="source-1", original_url=None,
        original_path=tmp_path / "two.mp4",
    )
    source = TelegramSource(tmp_path, SimpleNamespace(), db, source="-1001", source_id="source-1")
    assert source._is_durably_reserved_without_new_link("1", "source-1", None)
    assert source._is_durably_reserved_without_new_link(
        "1", "source-1", "https://shopee.com.br/product/1/2"
    )
    assert not source._is_durably_reserved_without_new_link(
        "2", "source-1", "https://shopee.com.br/product/2/3"
    )
    assert with_url != without_url
    db.close()


def coordinator_id_count(db):
    return int(db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
