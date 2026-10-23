from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from ArmoredHub.service import ArmoredHub
from armored_core.database import Database
from armored_core.integrity import sha256_file
from armored_core.models import State
from armored_core.republication_ledger import VersionedPublicationLedger
from armored_core.services import SyncService
from armored_core.storage import Storage


EXECUTION_ID = "replay-test"
CONTENT_ID = "source-a_10"
RUN_ID = f"{EXECUTION_ID}-v1:{CONTENT_ID}"
CHAT_ID = "-1001234567890"
TOPIC_ID = 228


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.delenv("ARMORED_HUB_DRY_RUN", raising=False)
    monkeypatch.setenv("ARMORED_CREATOR_BOT_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("ARMORED_TELEGRAM_BOT_API_URL", "http://127.0.0.1:8081/bot")
    monkeypatch.setenv("ARMORED_TELEGRAM_BOT_API_FILE_URL", "http://127.0.0.1:8081/file/bot")
    storage = Storage(tmp_path)
    db = Database(storage.database / "republication.sqlite3")
    original = tmp_path / "source.mp4"
    original.write_bytes(b"canonical original")
    item_id = SyncService(db, storage).ingest(original, "10", source_id="source-a")
    db.set_vision(item_id, "Example", "https://affiliate.invalid/item", publication_caption="Replay caption")
    canonical = db.get(item_id)
    render = canonical.workspace / "canonical-render.mp4"
    render.write_bytes(b"historical render")
    db.set_result(item_id, render)
    db.transition(item_id, State.PUBLISHING, "speech-studio-v5-complete")
    db.publication_started(item_id, CHAT_ID, TOPIC_ID)
    db.publication_send_started(item_id)
    db.publication_message_sent(item_id, "1338")
    db.publication_confirmed(item_id, "1338")
    db.transition(item_id, State.PUBLISHED, "historical-confirmed")
    replay = tmp_path / "replay.mp4"
    replay.write_bytes(b"replay v1 output")
    ledger = VersionedPublicationLedger(storage.database / "republication-ledger.sqlite3")
    hub = ArmoredHub(tmp_path, db, replay_ledger=ledger)
    monkeypatch.setattr(hub, "_video_metadata", lambda _: (1080, 1920, 3))
    try:
        yield hub, db, ledger, item_id, replay
    finally:
        ledger.close()
        db.close()


def prepare_run(hub, item_id, replay):
    digest = sha256_file(replay)
    return hub.prepare_republication(
        run_id=RUN_ID,
        execution_id=EXECUTION_ID,
        execution_version=1,
        canonical_content_id=item_id,
        prior_publication_message_id="1338",
        output_path=replay,
        output_sha256=digest,
        idempotency_key=f"armoredcreator:reexecution:{EXECUTION_ID}:v1:{item_id}",
        destination_chat_id=CHAT_ID,
        destination_topic_id=TOPIC_ID,
    )


def fake_bot(monkeypatch, *, send_error=False):
    calls = []

    class NetworkError(Exception):
        pass

    class Bot:
        def __init__(self, **kwargs):
            assert kwargs["token"] == "synthetic-test-token"
            assert kwargs["base_url"] == "http://127.0.0.1:8081/bot"
            assert kwargs["base_file_url"] == "http://127.0.0.1:8081/file/bot"

        async def get_me(self):
            calls.append("get_me")
            return SimpleNamespace(id=7, username="test-bot")

        async def get_chat(self, chat_id):
            calls.append("get_chat")
            assert chat_id == int(CHAT_ID)
            return SimpleNamespace(title="Test forum", type="supergroup", is_forum=True)

        async def get_chat_member(self, chat_id, user_id):
            calls.append("get_chat_member")
            assert chat_id == int(CHAT_ID) and user_id == 7
            return SimpleNamespace(status="administrator")

        async def send_video(self, **kwargs):
            calls.append("send_video")
            assert kwargs["chat_id"] == int(CHAT_ID)
            assert kwargs["message_thread_id"] == TOPIC_ID
            assert kwargs["video"].read() == b"replay v1 output"
            if send_error:
                raise NetworkError("simulated ambiguous timeout")
            return SimpleNamespace(
                message_id=7001,
                chat=SimpleNamespace(id=int(CHAT_ID)),
                message_thread_id=TOPIC_ID,
            )

        async def shutdown(self):
            calls.append("shutdown")

    telegram = ModuleType("telegram")
    telegram.Bot = Bot
    errors = ModuleType("telegram.error")
    errors.NetworkError = errors.TimedOut = NetworkError
    request = ModuleType("telegram.request")
    request.HTTPXRequest = lambda **kwargs: object()
    for name, module in (("telegram", telegram), ("telegram.error", errors), ("telegram.request", request)):
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_prepare_is_idempotent_and_preserves_original_published_receipt(prepared):
    hub, db, ledger, item_id, replay = prepared
    before = dict(db.publication(item_id))

    first = prepare_run(hub, item_id, replay)
    again = prepare_run(hub, item_id, replay)

    assert first["status"] == again["status"] == "PREPARED"
    assert first["run_id"] == RUN_ID
    assert first["prior_publication_message_id"] == "1338"
    assert db.get(item_id).state is State.PUBLISHED
    assert dict(db.publication(item_id)) == before
    assert len(ledger.list_execution(EXECUTION_ID, 1)) == 1
    assert db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_runs'").fetchone() is None


def test_prepare_rejects_hash_conflict_and_noncanonical_item(prepared):
    hub, db, ledger, item_id, replay = prepared
    prepare_run(hub, item_id, replay)
    with pytest.raises(ValueError, match="identity-conflict"):
        ledger.prepare(
            run_id=RUN_ID,
            execution_id=EXECUTION_ID,
            execution_version=1,
            canonical_content_id=item_id,
            prior_publication_message_id="1338",
            output_sha256="0" * 64,
            idempotency_key=f"armoredcreator:reexecution:{EXECUTION_ID}:v1:{item_id}",
            destination_chat_id=CHAT_ID,
            destination_topic_id=TOPIC_ID,
        )


def test_prepare_rejects_published_row_that_is_marked_as_duplicate(prepared):
    hub, db, _ledger, item_id, replay = prepared
    db.conn.execute(
        "UPDATE items SET duplicate_of_content_id='canonical-owner' WHERE content_id=?",
        (item_id,),
    )
    db.conn.commit()
    with pytest.raises(ValueError, match="refuses-duplicate-item"):
        prepare_run(hub, item_id, replay)


def test_default_ledger_is_a_separate_database_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ARMORED_REPUBLICATION_LEDGER_PATH", raising=False)
    db = Database(tmp_path / "storage" / "database" / "operational.sqlite3")
    hub = ArmoredHub(tmp_path, db)
    ledger = hub._republication_ledger()
    try:
        assert ledger.path == tmp_path / "storage" / "database" / "republication-ledger.sqlite3"
        assert db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_runs'"
        ).fetchone() is None
    finally:
        ledger.close()
        db.close()


def test_republication_refuses_nonlocal_bot_api_endpoints(prepared, monkeypatch):
    hub, db, _ledger, item_id, replay = prepared
    prepare_run(hub, item_id, replay)
    monkeypatch.setenv("ARMORED_TELEGRAM_BOT_API_URL", "https://api.telegram.org/bot")
    with pytest.raises(RuntimeError, match="refuses-nonlocal"):
        hub.preflight_republication(RUN_ID, replay)


def test_read_only_preflight_uses_local_bot_api_and_never_sends(prepared, monkeypatch):
    hub, db, ledger, item_id, replay = prepared
    prepare_run(hub, item_id, replay)
    calls = fake_bot(monkeypatch)

    result = hub.preflight_republication(RUN_ID, replay)

    assert result["endpoint"] == "local-bot-api"
    assert result["send_called"] is False
    assert result["bot_status"] == "administrator"
    assert calls == ["get_me", "get_chat", "get_chat_member", "shutdown"]
    assert ledger.get(RUN_ID)["status"] == "PREPARED"


def test_republication_requires_run_specific_approval_and_dry_run_blocks(prepared, monkeypatch):
    hub, db, ledger, item_id, replay = prepared
    prepare_run(hub, item_id, replay)
    calls = fake_bot(monkeypatch)

    with pytest.raises(RuntimeError, match="explicit-run-approval"):
        hub.publish_republication(RUN_ID, replay)
    assert calls == []
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    with pytest.raises(RuntimeError, match="send blocked"):
        hub.publish_republication(RUN_ID, replay, explicit_approval=f"SEND:{RUN_ID}")
    assert calls == []
    assert ledger.get(RUN_ID)["status"] == "PREPARED"


def test_changed_historical_receipt_blocks_replay_before_send(prepared, monkeypatch):
    hub, db, ledger, item_id, replay = prepared
    prepare_run(hub, item_id, replay)
    db.conn.execute(
        "UPDATE publications SET published_message_id='9999' WHERE content_id=?",
        (item_id,),
    )
    db.conn.commit()
    calls = fake_bot(monkeypatch)
    with pytest.raises(RuntimeError, match="prior-receipt-changed"):
        hub.publish_republication(RUN_ID, replay, explicit_approval=f"SEND:{RUN_ID}")
    assert calls == []
    assert ledger.get(RUN_ID)["status"] == "PREPARED"


def test_versioned_send_confirms_additive_receipt_without_mutating_old_publication(prepared, monkeypatch):
    hub, db, ledger, item_id, replay = prepared
    prepare_run(hub, item_id, replay)
    before = dict(db.publication(item_id))
    calls = fake_bot(monkeypatch)

    result = hub.publish_republication(RUN_ID, replay, explicit_approval=f"SEND:{RUN_ID}")

    assert result.confirmed and result.message_id == "7001"
    assert calls.count("send_video") == 1
    assert ledger.get(RUN_ID)["status"] == "CONFIRMED"
    assert ledger.get(RUN_ID)["published_message_id"] == "7001"
    assert dict(db.publication(item_id)) == before
    assert db.get(item_id).state is State.PUBLISHED


def test_ambiguous_versioned_send_becomes_terminal_and_cannot_retry(prepared, monkeypatch):
    hub, db, ledger, item_id, replay = prepared
    prepare_run(hub, item_id, replay)
    calls = fake_bot(monkeypatch, send_error=True)

    with pytest.raises(Exception, match="INDETERMINATE"):
        hub.publish_republication(RUN_ID, replay, explicit_approval=f"SEND:{RUN_ID}")
    assert ledger.get(RUN_ID)["status"] == "INDETERMINATE"
    with pytest.raises(RuntimeError, match="not-retryable"):
        hub.publish_republication(RUN_ID, replay, explicit_approval=f"SEND:{RUN_ID}")
    assert calls.count("send_video") == 1
    assert db.get(item_id).state is State.PUBLISHED
