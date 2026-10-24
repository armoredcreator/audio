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
            ia_context={"productName": "Produto de teste", "category": "casa", "shopId": "700", "itemId": "800"},
        )


class _IA:
    def __init__(self, events):
        self.events = events

    def generate_caption_with_evidence(self, context):
        self.events.append("ia")
        from ArmoredIA.caption.generator import CaptionGenerator

        class Provider:
            def generate_candidates(self, _task, _context, _limit):
                return ["Casa charmosa 🧡 #casa"]

        return CaptionGenerator(provider=Provider()).generate_with_evidence(context)

    def generate_caption(self, context):
        return self.generate_caption_with_evidence(context).caption


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
        ia_context={"productName": "Produto de teste", "category": "casa", "shopId": "700", "itemId": "800"},
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
    assert current.publication_caption == "Casa 🧡 charmosa\n#casa"
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


def test_catch_up_completes_ia_for_entire_batch_before_speech_studio_and_hub(
    tmp_path, monkeypatch
):
    import asyncio

    events = []
    storage = Storage(tmp_path)
    db = Database(storage.database / "core.sqlite3")
    for index in range(1, 4):
        monkeypatch.setenv(f"ARMORED_SOURCE_{index}_ID", f"source{index}")

    content_ids = []
    sync = SyncService(db, storage)
    for index, source_id in enumerate(("source1", "source2", "source3"), start=1):
        source = tmp_path / "fixtures" / f"original-{index}.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"immutable-original-{index}".encode())
        item_id = sync.ingest(
            source,
            str(100 + index),
            source_id=source_id,
            original_url=f"https://shopee.com.br/product/{100 + index}",
        )
        db.set_vision(
            item_id,
            f"Produto {index}",
            f"https://affiliate.invalid/item-{100 + index}",
            ia_context={
                "productName": f"Produto {index}",
                "category": "casa",
                "shopId": "700",
                "itemId": str(800 + index),
            },
        )
        content_ids.append(str(item_id))

    class Source:
        sources = tuple(SimpleNamespace(source_id=f"source{i}") for i in range(1, 4))
        historical_scan_exhausted = False
        historical_materialization_failed = False

        async def iter_historical_candidates_async(self):
            self.historical_scan_exhausted = True
            if False:
                yield None

        def mark_ingested(self, _message_id):
            pass

        def mark_materialization_failed(self):
            self.historical_materialization_failed = True

        def complete_historical_sync(self):
            db.complete_historical_sync()

    class TraceIA:
        def generate_caption_with_evidence(self, context):
            name = str((context or {}).get("productName", "unknown"))
            events.append(f"ia:{name}")
            from ArmoredIA.caption.generator import CaptionGenerator

            class Provider:
                def generate_candidates(self, _task, _context, _limit):
                    return ["Casa charmosa 🧡 #casa"]

            return CaptionGenerator(provider=Provider()).generate_with_evidence(context)

        def generate_caption(self, context):
            return self.generate_caption_with_evidence(context).caption

    class TraceSpeech:
        intelligence = SimpleNamespace(preflight=lambda: events.append("speech-preflight"))

        def analyze(self, video_path):
            events.append(f"speech-analyze:{Path(video_path).name}")
            return SpeechDecision(
                audio_class=AudioClass.MUSIC_ONLY,
                language=None,
                speech_detected=False,
                music_detected=True,
                confidence=0.99,
                rationale="deterministic catch-up batch test",
            )

        def prepare_wav(self, _video_path, wav_path, _decision):
            events.append("speech-prepare")
            Path(wav_path).write_bytes(b"speech-sidecar")

    class TracePublisher(_Publisher):
        def publish_once(self, item):
            events.append(f"hub:{item.content_id}")
            return super().publish_once(item)

    coordinator = Coordinator(
        db,
        storage,
        _Vision(),
        _LegacyStudioMustNotRun(),
        TracePublisher(db, events),
        source=Source(),
        ia=TraceIA(),
    )
    coordinator._speech_processor_override = TraceSpeech()

    def studio_batch_runner(*, root, manifest_path, speech_database, report_path, copy_only, storage_root):
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert manifest["expected_item_count"] == 3
        assert len(manifest["items"]) == 3
        assert all(row["state"] == "SPEECH" for row in manifest["items"])
        events.append("studio-start")
        target_storage = Storage(Path(root), storage_root=Path(storage_root))
        processed = []
        for row in manifest["items"]:
            original = Path(row["original_path"])
            output = target_storage.result(
                row["content_id"], row["affiliate_url"], row["affiliate_name"],
                source_id=row["source_id"],
            )
            output.write_bytes(original.read_bytes() + b"-rendered")
            events.append(f"studio-item:{row['content_id']}")
            processed.append({
                "source_id": row["source_id"],
                "item_id": row["telegram_message_id"],
                "output_path": str(output.resolve()),
                "original_sha256_before": row["original_sha256"],
            })
        return {
            "status": "SUCCESS",
            "originals_intact": True,
            "processed": processed,
            "report_path": str(report_path),
        }

    coordinator._speech_studio_batch_runner_override = studio_batch_runner
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    monkeypatch.setenv("ARMORED_STUDIO_INTRO", "0")

    try:
        processed = asyncio.run(coordinator._run_staged_catch_up_async())

        assert set(processed) >= set(content_ids)
        ia_indexes = [i for i, event in enumerate(events) if event.startswith("ia:")]
        speech_indexes = [i for i, event in enumerate(events) if event.startswith("speech-")]
        studio_indexes = [i for i, event in enumerate(events) if event.startswith("studio-")]
        hub_indexes = [i for i, event in enumerate(events) if event.startswith("hub:")]
        assert len(ia_indexes) == 3
        assert speech_indexes and studio_indexes and len(hub_indexes) == 3
        assert max(ia_indexes) < min(speech_indexes)
        assert max(speech_indexes) < min(studio_indexes)
        assert max(studio_indexes) < min(hub_indexes)
        assert all(db.get(item_id).state is State.PUBLISHED for item_id in content_ids)
        assert all(db.get(item_id).cleanup_completed for item_id in content_ids)
        assert all(db.get(item_id).original_path.is_file() for item_id in content_ids)
        assert db.historical_complete() is True
    finally:
        coordinator.close()


def test_recovery_finishes_speech_studio_but_leaves_hub_for_coordinator_loop(
    tmp_path, monkeypatch
):
    events = []
    coordinator, _storage, db, item_id, original_bytes = _fixture(tmp_path, events)
    db.transition(item_id, State.SPEECH, "test-interrupted-before-speech")
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    _install_test_speech_studio(coordinator, events, monkeypatch)

    coordinator.recovery.reconcile(item_id)

    current = db.get(item_id)
    assert current.state is State.PUBLISHING
    assert coordinator._has_speech_studio_v5_completion(item_id)
    assert "studio" in events
    assert not any(event.startswith("hub") for event in events)
    assert current.original_path.read_bytes() == original_bytes

    coordinator.run(item_id)
    assert db.get(item_id).state is State.PUBLISHED
    assert any(event.startswith("hub") for event in events)
    db.close()


def test_coordinator_rejects_success_report_with_missing_item_before_hub(
    tmp_path, monkeypatch
):
    events = []
    coordinator, _storage, db, item_id, original_bytes = _fixture(tmp_path, events)
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    _install_test_speech_studio(coordinator, events, monkeypatch)

    from ArmoredSpeech import studio_batch_runner

    def incomplete_success_report(**_kwargs):
        events.append("studio")
        return {
            "status": "SUCCESS",
            "originals_intact": True,
            "processed": [],
        }

    monkeypatch.setattr(
        studio_batch_runner, "run_complete_batch", incomplete_success_report
    )

    with pytest.raises(RuntimeError, match="exactly the requested batch"):
        coordinator.run(item_id)

    current = db.get(item_id)
    assert current.state is State.RECOVERY
    assert "studio" in events
    assert "hub" not in events
    assert current.original_path.read_bytes() == original_bytes
    assert db.last_state_event(item_id)["reason"] == "speech-studio-v5-failed:RuntimeError"
    db.close()
