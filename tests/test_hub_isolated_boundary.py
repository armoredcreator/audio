from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from ArmoredHub.service import ArmoredHub
from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.services import PublicationUnknownError, SyncService
from armored_core.storage import Storage


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.delenv("ARMORED_HUB_DRY_RUN", raising=False)
    monkeypatch.setenv("ARMORED_CREATOR_BOT_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("ARMORED_CREATOR_GROUP_ID", "-1000000000001")
    monkeypatch.setenv("ARMORED_HUB_TOPIC_ID", "10")
    monkeypatch.setenv("ARMORED_TELEGRAM_VERIFY_ATTEMPTS", "1")
    storage = Storage(tmp_path)
    db = Database(storage.database / "test.sqlite3")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic original")
    item_id = SyncService(db, storage).ingest(source, "101", source_id="synthetic-source")
    db.set_vision(item_id, "Example", "https://affiliate.invalid/example", publication_caption="Detalhe especial 🧡 #casa")
    output = db.get(item_id).workspace / "result.mp4"
    output.write_bytes(b"synthetic render")
    db.set_result(item_id, output)
    db.transition(item_id, State.PUBLISHING, "speech-studio-v5-complete")
    hub = ArmoredHub(tmp_path, db)
    monkeypatch.setattr(hub, "_video_metadata", lambda _: (1080, 1920, 2))
    try:
        yield hub, db, item_id
    finally:
        db.close()


def fake_telegram(monkeypatch, *, send_error=False):
    calls = []
    class NetworkError(Exception):
        pass
    class Bot:
        def __init__(self, **kwargs):
            assert kwargs["token"] == "synthetic-test-token"
        async def get_me(self):
            calls.append("preflight")
            return SimpleNamespace(id=1)
        async def send_video(self, **kwargs):
            calls.append("send")
            assert kwargs["chat_id"] == -1000000000001
            assert kwargs["message_thread_id"] == 10
            assert kwargs["video"].read() == b"synthetic render"
            if send_error:
                raise NetworkError("synthetic ambiguous transport failure")
            return SimpleNamespace(message_id=7001)
        async def shutdown(self):
            calls.append("shutdown")
    telegram, errors, request = (ModuleType(name) for name in ("telegram", "telegram.error", "telegram.request"))
    telegram.Bot = Bot
    errors.NetworkError = errors.TimedOut = NetworkError
    request.HTTPXRequest = lambda **kwargs: object()
    for name, module in (("telegram", telegram), ("telegram.error", errors), ("telegram.request", request)):
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_disabled_hub_never_reads_telegram_or_creates_publication(prepared, monkeypatch):
    hub, db, item_id = prepared
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    monkeypatch.setattr(hub, "_verify_telegram_message", lambda *_: pytest.fail("external verification"))
    monkeypatch.setattr(hub, "_find_telegram_publications", lambda *_: pytest.fail("external search"))
    item = db.get(item_id)
    assert hub.check_publication(item) is PublicationCheck.UNKNOWN
    for method in (hub.publish_once, hub.publish):
        with pytest.raises(RuntimeError, match="disabled"):
            method(item)
    assert db.publication(item_id) is None


def test_real_hub_code_with_simulated_transport_is_idempotent(prepared, monkeypatch):
    hub, db, item_id = prepared
    calls = fake_telegram(monkeypatch)
    first = hub.publish_once(db.get(item_id))
    monkeypatch.setattr(hub, "_verify_telegram_message", lambda *_: True)
    second = hub.publish_once(db.get(item_id))
    assert first.confirmed and first.message_id == second.message_id == "7001"
    assert calls.count("send") == 1
    assert calls.count("preflight") == 1
    assert db.publication(item_id)["confirmed"]
    assert db.get(item_id).state is State.PUBLISHING  # Coordinator owns transition.


def test_ambiguous_simulated_send_is_not_repeated(prepared, monkeypatch):
    hub, db, item_id = prepared
    calls = fake_telegram(monkeypatch, send_error=True)
    monkeypatch.setattr(hub, "_find_telegram_publications", lambda *_: [])
    for _ in range(2):
        with pytest.raises(PublicationUnknownError):
            hub.publish_once(db.get(item_id))
    assert calls.count("send") == 1
    assert not db.publication(item_id)["confirmed"]


@pytest.mark.parametrize("breakage", ["missing-v5", "wrong-state", "changed-original", "missing-result"])
def test_hub_rejects_unvalidated_input_before_any_transport(prepared, monkeypatch, breakage):
    hub, db, item_id = prepared
    calls = fake_telegram(monkeypatch)
    item = db.get(item_id)
    if breakage == "missing-v5":
        db.conn.execute("DELETE FROM state_events WHERE content_id=? AND reason='speech-studio-v5-complete'", (item_id,))
        db.conn.commit()
    elif breakage == "wrong-state":
        db.transition(item_id, State.RECOVERY, "synthetic interruption")
    elif breakage == "changed-original":
        item.original_path.write_bytes(b"changed")
    else:
        item.result_path.unlink()
    with pytest.raises(RuntimeError):
        hub.publish_once(db.get(item_id))
    assert calls == []
    assert db.publication(item_id) is None


def test_hub_test_lock_allows_only_matching_destination(prepared, monkeypatch):
    hub, db, item_id = prepared
    monkeypatch.setenv("ARMORED_HUB_TEST_CHAT_ID", "-1000000000001")
    monkeypatch.setenv("ARMORED_HUB_TEST_TOPIC_ID", "10")
    calls = fake_telegram(monkeypatch)

    result = hub.publish_once(db.get(item_id))

    assert result.confirmed
    assert result.message_id == "7001"
    assert calls.count("send") == 1
    record = db.publication(item_id)
    assert str(record["destination_chat_id"]) == "-1000000000001"
    assert int(record["destination_topic_id"]) == 10


@pytest.mark.parametrize(
    ("allowed_chat", "allowed_topic"),
    [
        ("-1000000000002", "10"),
        ("-1000000000001", "11"),
        ("-1000000000001", None),
        (None, "10"),
        ("invalid", "10"),
        ("-1000000000001", "0"),
    ],
)
def test_hub_test_lock_rejects_bad_configuration_before_publication(
    prepared, monkeypatch, allowed_chat, allowed_topic
):
    hub, db, item_id = prepared
    for name, value in (
        ("ARMORED_HUB_TEST_CHAT_ID", allowed_chat),
        ("ARMORED_HUB_TEST_TOPIC_ID", allowed_topic),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    calls = fake_telegram(monkeypatch)

    with pytest.raises(RuntimeError, match="test destination lock"):
        hub.publish_once(db.get(item_id))

    assert calls == []
    assert db.publication(item_id) is None


def test_hub_test_lock_checks_effective_route_not_fallback_env(prepared, monkeypatch):
    hub, db, item_id = prepared
    # The operator requested a test topic in the fallback variables, but
    # a real source route still resolves to an operational destination.
    monkeypatch.setenv("ARMORED_HUB_TOPIC_ID", "1329")
    monkeypatch.setenv("ARMORED_HUB_TEST_CHAT_ID", "-1000000000001")
    monkeypatch.setenv("ARMORED_HUB_TEST_TOPIC_ID", "1329")
    hub.routes = (
        SimpleNamespace(
            source=SimpleNamespace(source_id="synthetic-source"),
            hub=SimpleNamespace(chat_id="-1000000000001", topic_id=10),
        ),
    )
    calls = fake_telegram(monkeypatch)

    with pytest.raises(RuntimeError, match="refused configured route"):
        hub.publish_once(db.get(item_id))

    assert calls == []
    assert db.publication(item_id) is None


def test_hub_test_lock_rejects_persisted_operational_destination(prepared, monkeypatch):
    hub, db, item_id = prepared
    # An older send attempt may persist a destination that takes precedence
    # over both routes and the fallback environment.
    db.publication_started(
        item_id,
        destination_chat_id="-1000000000001",
        destination_topic_id=10,
    )
    monkeypatch.setenv("ARMORED_HUB_TEST_CHAT_ID", "-1000000000001")
    monkeypatch.setenv("ARMORED_HUB_TEST_TOPIC_ID", "1329")
    monkeypatch.setenv("ARMORED_HUB_TOPIC_ID", "1329")
    calls = fake_telegram(monkeypatch)

    with pytest.raises(RuntimeError, match="refused configured route"):
        hub.publish_once(db.get(item_id))

    assert calls == []
    assert int(db.publication(item_id)["destination_topic_id"]) == 10
