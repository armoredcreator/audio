from pathlib import Path

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.services import SyncService
from armored_core.storage import Storage


class ForbiddenExternal:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected external operation: {name}")


def fixture(tmp_path, ia=None):
    storage = Storage(tmp_path)
    db = Database(storage.database / "test.sqlite3")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    item_id = SyncService(db, storage).ingest(source, "100", source_id="source-test")
    db.set_vision(item_id, "Product", "https://affiliate.invalid/product", ia_context={"productName": "Product", "category": "casa", "shopId": "10", "itemId": "20"})
    coordinator = Coordinator(db, storage, ForbiddenExternal(), ForbiddenExternal(), ForbiddenExternal(), ia=ia)
    return coordinator, db, item_id


def test_inflight_ia_recovery_does_not_skip_caption_before_speech(tmp_path, monkeypatch):
    events = []
    class IA:
        def generate_caption(self, context):
            events.append("ia")
            return "Casa charmosa 🧡 #casa"
    coordinator, db, item_id = fixture(tmp_path, IA())
    db.transition(item_id, State.IA, "interrupted-after-entering-ia")
    def speech(ids):
        assert db.get(item_id).publication_caption == "Casa 🧡 charmosa\n#casa"
        assert db.get(item_id).state is State.SPEECH
        events.append("speech")
    monkeypatch.setattr(coordinator, "_run_speech_studio_batch", speech)
    try:
        coordinator.recover(item_id)
        assert events == ["ia", "speech"]
    finally:
        coordinator.close()


def test_coordinator_recovery_of_valid_v5_result_stops_before_hub(tmp_path):
    coordinator, db, item_id = fixture(tmp_path)
    result = db.get(item_id).workspace / "validated-result.mp4"
    result.write_bytes(b"validated render")
    db.set_result(item_id, result)
    db.transition(item_id, State.PUBLISHING, "speech-studio-v5-complete")
    db.transition(item_id, State.RECOVERY, "interrupted-before-hub")
    try:
        coordinator.recover(item_id)
        assert db.get(item_id).state is State.PUBLISHING
        assert db.publication(item_id) is None
        assert result.read_bytes() == b"validated render"
    finally:
        coordinator.close()


def test_coordinator_recovery_leaves_waiting_vision_for_explicit_allowlist(tmp_path):
    coordinator, db, item_id = fixture(tmp_path)
    db.mark_vision_waiting(item_id, "unresolved")
    events_before = db.conn.execute("SELECT COUNT(*) FROM state_events WHERE content_id=?", (item_id,)).fetchone()[0]
    try:
        coordinator.recover(item_id)
        assert db.get(item_id).state is State.WAITING_VISION
        assert db.conn.execute("SELECT COUNT(*) FROM state_events WHERE content_id=?", (item_id,)).fetchone()[0] == events_before
    finally:
        coordinator.close()


def test_v5_hub_preflight_failure_reuses_render_without_external_check(tmp_path):
    coordinator, db, item_id = fixture(tmp_path)
    result = db.get(item_id).workspace / "validated-result.mp4"
    result.write_bytes(b"validated render preserved")
    db.set_result(item_id, result)
    db.transition(item_id, State.PUBLISHING, "speech-studio-v5-complete")
    db.publication_started(item_id)
    db.transition(item_id, State.RECOVERY, "hub-preflight-unavailable")
    try:
        coordinator.recover(item_id)
        assert db.get(item_id).state is State.PUBLISHING
        assert result.read_bytes() == b"validated render preserved"
        assert db.publication(item_id)["verification_status"] == "PENDING"
    finally:
        coordinator.close()
