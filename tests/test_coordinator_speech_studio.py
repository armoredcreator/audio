from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.services import StudioResult, SyncService, VisionResult
from armored_core.storage import Storage
from ArmoredSpeech.contracts import AudioClass, SpeechDecision


class _Vision:
    def identify(self, _item):
        return VisionResult(
            "Produto de teste",
            "https://affiliate.invalid/approved",
            ia_context={"productName": "Produto de teste", "shopId": "700", "itemId": "800"},
        )


class _IA:
    def __init__(self, events):
        self.events = events

    def generate_caption(self, _context):
        self.events.append("ia")
        return "Produto de teste 🧡 #casa"


class _LegacyStudioMustNotRun:
    def process(self, *_args, **_kwargs):
        raise AssertionError("Coordinator must use the Speech v5 batch runner, not legacy studio.process")


class _Publisher:
    def __init__(self, db, events):
        self.db = db
        self.events = events

    def publish_once(self, item):
        self.events.append("hub")
        self.db.publication_started(item.content_id)
        return SimpleNamespace(confirmed=True, message_id="9001")


class _SpeechProcessor:
    def __init__(self, events, *, fail=False):
        self.events = events
        self.fail = fail
        self.intelligence = SimpleNamespace(preflight=lambda: self.events.append("speech-preflight"))

    def analyze(self, _video_path):
        self.events.append("speech-analyze")
        return SpeechDecision(
            audio_class=AudioClass.MUSIC_ONLY,
            language=None,
            speech_detected=False,
            music_detected=True,
            confidence=0.99,
            rationale="deterministic coordinator integration test",
        )

    def prepare_wav(self, _video_path, wav_path, _decision):
        self.events.append("speech-prepare")
        if self.fail:
            raise RuntimeError("synthetic-speech-failure")
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(b"validated-speech-sidecar")


def _fixture(tmp_path: Path, events):
    storage = Storage(tmp_path)
    db = Database(storage.database / "core.sqlite3")
    source = tmp_path / "fixtures" / "untouched.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable-original-content")
    item_id = SyncService(db, storage).ingest(source, "501", source_id="source1")
    db.set_vision(
        item_id,
        "Produto de teste",
        "https://affiliate.invalid/approved",
        ia_context={"productName": "Produto de teste", "shopId": "700", "itemId": "800"},
    )
    coordinator = Coordinator(
        db,
        storage,
        _Vision(),
        _LegacyStudioMustNotRun(),
        _Publisher(db, events),
        ia=_IA(events),
    )
    return coordinator, storage, db, item_id, source.read_bytes()


def _fake_studio_runner(events, *, fail=False):
    def run_complete_batch(*, root, manifest_path, speech_database, report_path, copy_only, storage_root):
        assert copy_only is False
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert manifest["phase"] == "ARMORED_SPEECH"
        assert manifest["phase_status"] == "COMPLETE"
        assert manifest["approved"] is True
        assert all(row["state"] == "SPEECH" for row in manifest["items"])
        events.append("studio")
        if fail:
            raise RuntimeError("synthetic-studio-failure")
        storage = Storage(Path(root), storage_root=Path(storage_root))
        processed = []
        for row in manifest["items"]:
            original = Path(row["original_path"])
            output = storage.result(
                row["content_id"],
                row["affiliate_url"],
                row["affiliate_name"],
                source_id=row["source_id"],
            )
            output.write_bytes(original.read_bytes() + b"-rendered")
            processed.append({
                "source_id": row["source_id"],
                "item_id": row["telegram_message_id"],
                "output_path": str(output.resolve()),
                "original_sha256_before": row["original_sha256"],
            })
        return {"status": "SUCCESS", "originals_intact": True, "processed": processed}
    return run_complete_batch


def _install_test_speech_studio(coordinator, events, monkeypatch, *, speech_fails=False, studio_fails=False):
    processor = _SpeechProcessor(events, fail=speech_fails)
    monkeypatch.setattr(coordinator, "_preflight_speech_studio_v5", lambda: processor)
    from ArmoredSpeech import studio_batch_runner
    monkeypatch.setattr(studio_batch_runner, "run_complete_batch", _fake_studio_runner(events, fail=studio_fails))


def test_coordinator_routes_normal_execution_through_speech_v5_before_studio_and_hub(
    tmp_path, monkeypatch
):
    events = []
    coordinator, _storage, db, item_id, original_bytes = _fixture(tmp_path, events)
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    _install_test_speech_studio(coordinator, events, monkeypatch)

    coordinator.run(item_id)

    current = db.get(item_id)
    assert current.state is State.PUBLISHED
    assert current.cleanup_completed is True
    assert current.publication_caption == "Produto de teste 🧡 #casa"
    assert events.index("ia") < events.index("speech-analyze") < events.index("speech-prepare")
    assert events.index("speech-prepare") < events.index("studio") < events.index("hub")
    assert db.last_state_event(item_id)["new_state"] == State.PUBLISHED.value
    assert coordinator._has_speech_studio_v5_completion(item_id)
    assert current.original_path.read_bytes() == original_bytes
    db.close()


def test_speech_failure_blocks_studio_and_publication_and_persists_recovery(
    tmp_path, monkeypatch
):
    events = []
    coordinator, _storage, db, item_id, original_bytes = _fixture(tmp_path, events)
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    _install_test_speech_studio(coordinator, events, monkeypatch, speech_fails=True)

    with pytest.raises(RuntimeError, match="complete batch"):
        coordinator.run(item_id)

    current = db.get(item_id)
    assert current.state is State.RECOVERY
    assert "studio" not in events
    assert "hub" not in events
    assert current.original_path.read_bytes() == original_bytes
    db.close()


def test_studio_failure_blocks_hub_and_marks_entire_v5_boundary_for_recovery(
    tmp_path, monkeypatch
):
    events = []
    coordinator, _storage, db, item_id, original_bytes = _fixture(tmp_path, events)
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    _install_test_speech_studio(coordinator, events, monkeypatch, studio_fails=True)

    with pytest.raises(RuntimeError, match="synthetic-studio-failure"):
        coordinator.run(item_id)

    current = db.get(item_id)
    assert current.state is State.RECOVERY
    assert events.index("speech-prepare") < events.index("studio")
    assert "hub" not in events
    assert current.original_path.read_bytes() == original_bytes
    last = db.last_state_event(item_id)
    assert last["new_state"] == State.RECOVERY.value
    assert last["reason"] == "speech-studio-v5-failed:RuntimeError"
    db.close()


def test_atomic_speech_studio_batch_state_commit_rolls_back_partial_transition(tmp_path):
    storage = Storage(tmp_path)
    db = Database(storage.database / "core.sqlite3")
    source = tmp_path / "fixtures" / "video.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable")
    first = SyncService(db, storage).ingest(source, "701", source_id="source1")
    second = SyncService(db, storage).ingest(source, "702", source_id="source2")
    db.transition(first, State.SPEECH, "test-speech-ready")
    db.transition(first, State.STUDIO_BATCH, "test-studio-started")
    out1 = tmp_path / "result1.mp4"
    out2 = tmp_path / "result2.mp4"
    out1.write_bytes(b"result-one")
    out2.write_bytes(b"result-two")

    with pytest.raises(RuntimeError, match="state-mismatch"):
        db.complete_speech_studio_batch({first: out1, second: out2})

    assert db.get(first).state is State.STUDIO_BATCH
    assert db.get(first).result_path is None
    assert db.get(second).state is State.RECEIVED
    db.close()
