from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ArmoredHub.service import ArmoredHub
from ArmoredIA.caption.generator import CaptionGenerator
from ArmoredIA.caption.policy import validate_caption
from ArmoredIA.caption.selector import CaptionCandidateEvaluation, CaptionGenerationError
from ArmoredIA.providers.base import AIProviderError
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
        ia_context=({"productName": "Bancada Suspensa", "category": "cozinha", "usage": "prática"} if product else {}),
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


def test_legacy_success_rejected_by_current_policy_is_blocked_until_audited_reopen(monkeypatch, tmp_path):
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    root = tmp_path / "project"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id, _original, render = _prepared_item(tmp_path, db, storage)
    stale_caption = "Bancada 🧡 suspensa\n#cozinha"
    db.set_caption(item_id, stale_caption)
    db.finish_caption_recovery(
        item_id,
        status="SUCCEEDED",
        reason="approved-under-previous-caption-policy",
        evidence={"policy_version": "legacy"},
    )
    ia = _IA(caption="Cantinho 🧡 prático\n#cozinha")
    pipeline = Pipeline(db, storage, _UnusedVision(), _UnusedStudio(), ArmoredHub(root, db), ia)

    # The title sequence is rejected by the current Policy; stale success must
    # not loop or silently overwrite its persisted caption.
    pipeline.run(item_id)
    pipeline.run(item_id)
    recovery = db.caption_recovery(item_id)
    assert recovery["status"] == "BLOCKED"
    assert recovery["reason"] == "persisted-caption-failed-current-policy"
    assert ia.calls == 0
    assert db.get(item_id).publication_caption == stale_caption
    assert db.get(item_id).state == State.PUBLISHING
    assert db.publication(item_id) is None
    assert db.has_speech_studio_v5_completion(item_id)

    events = db.caption_recovery_events(item_id)
    assert any(
        row["event_type"] == "BLOCKED"
        and json.loads(str(row["evidence_json"])).get("previous_caption") == stale_caption
        for row in events
    )
    before_render = hashlib.sha256(render.read_bytes()).hexdigest()
    db.reopen_caption_recovery(
        item_id,
        actor="caption-reviewer",
        reason="confirmed no external receipt; retry with current Policy",
        evidence={"render_sha256": before_render},
    )
    pipeline.run(item_id)
    assert ia.calls == 1
    assert db.caption_recovery(item_id)["status"] == "SUCCEEDED"
    assert db.get(item_id).publication_caption == "Cantinho 🧡 prático\n#cozinha"
    assert db.get(item_id).state == State.PUBLISHING
    assert hashlib.sha256(render.read_bytes()).hexdigest() == before_render
    assert db.publication(item_id) is None
    db.close()


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


def test_rejected_ia_candidates_are_persisted_when_recovery_is_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("ARMORED_HUB_DRY_RUN", "1")
    root = tmp_path / "project"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id, _original, _render = _prepared_item(tmp_path, db, storage)

    class RejectingIA(_IA):
        def generate_caption_with_evidence(self, context):
            self.calls += 1
            self.contexts.append(dict(context))
            evaluation = CaptionCandidateEvaluation(
                index=1,
                caption="Cantinho 🧡 belo",
                policy_valid=False,
                rejection_reason="texto principal contém termo sem suporte no contexto Vision",
                score=0,
                raw_caption="Cantinho belo #casa",
                normalization_corrections=("orange_heart_inserted",),
            )
            raise CaptionGenerationError(
                "caption-candidates-exhausted-policy",
                batch_id="failed-batch-1",
                evaluations=(evaluation,),
                provider_requests=2,
                feedback_rounds=(("texto principal contém termo sem suporte",),),
            )

    ia = RejectingIA()
    pipeline = Pipeline(db, storage, _UnusedVision(), _UnusedStudio(), ArmoredHub(root, db), ia)
    pipeline.run(item_id)

    candidates = db.caption_candidates(item_id, "failed-batch-1")
    assert len(candidates) == 1
    assert candidates[0]["raw_caption"] == "Cantinho belo #casa"
    assert candidates[0]["policy_valid"] == 0
    attempts = db.conn.execute(
        "SELECT provider_requests,policy_retries,feedback_json FROM ia_generation_attempts WHERE content_id=?",
        (item_id,),
    ).fetchone()
    assert attempts["provider_requests"] == 2
    assert attempts["policy_retries"] == 1
    assert "texto principal contém termo sem suporte" in attempts["feedback_json"]
    assert db.get(item_id).publication_caption is None
    assert db.caption_recovery(item_id)["status"] == "BLOCKED"
    assert db.publication(item_id) is None
    db.close()


def test_blocked_caption_reopen_is_audited_and_authorizes_only_one_retry(tmp_path):
    root = tmp_path / "project"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id, _original, _render = _prepared_item(tmp_path, db, storage)
    db.finish_caption_recovery(
        item_id,
        status="BLOCKED",
        reason="caption-candidates-exhausted-policy",
        evidence={"candidate_count": 3},
    )
    before = len(db.caption_recovery_events(item_id))

    db.reopen_caption_recovery(
        item_id,
        actor="caption-reviewer",
        reason="context corrected from persisted Vision approval",
        evidence={"review": "context-only"},
    )
    record = db.caption_recovery(item_id)
    assert record["status"] == "BLOCKED"
    assert record["manual_retry_authorized"] == 1
    assert len(db.caption_recovery_events(item_id)) == before + 1
    assert db.caption_recovery_events(item_id)[-1]["event_type"] == "MANUAL_REOPENED"

    assert db.claim_caption_recovery(item_id) == "STARTED"
    assert db.caption_recovery(item_id)["manual_retry_authorized"] == 0
    assert db.claim_caption_recovery(item_id) == "BLOCKED"
    db.close()


def test_blocked_caption_cannot_reopen_with_uncertain_publication_receipt(tmp_path):
    root = tmp_path / "project"
    storage = Storage(root)
    db = Database(storage.database / "armoredcreator.db")
    item_id, _original, _render = _prepared_item(tmp_path, db, storage)
    db.finish_caption_recovery(item_id, status="BLOCKED", reason="caption-policy")
    db.publication_started(item_id)
    db.conn.execute(
        "UPDATE publications SET verification_status='UNKNOWN' WHERE content_id=?",
        (item_id,),
    )
    db.conn.commit()

    try:
        db.reopen_caption_recovery(
            item_id,
            actor="caption-reviewer",
            reason="attempt to override uncertain receipt",
        )
    except RuntimeError as exc:
        assert "publication-receipt" in str(exc)
    else:
        raise AssertionError("uncertain publication must keep caption recovery locked")
    assert db.caption_recovery(item_id)["manual_retry_authorized"] == 0
    db.close()


def test_temporary_provider_failure_allows_one_durable_auto_retry_then_stops(tmp_path):
    storage = Storage(tmp_path)
    db = Database(storage.database / "auto-caption-retry.sqlite3")
    item_id = "source-auto-retry_1"
    try:
        db.finish_caption_recovery(
            item_id,
            status="BLOCKED",
            reason="caption-provider-temporary:TRANSIENT_UNAVAILABLE",
            automatic_retry_authorized=True,
            evidence={"automatic_retry_limit": 1},
        )
        assert not db.caption_recovery_blocked(item_id)
        assert db.claim_caption_recovery(item_id) == "STARTED"
        assert db.caption_recovery(item_id)["attempts"] == 1
        db.finish_caption_recovery(
            item_id,
            status="BLOCKED",
            reason="caption-provider-temporary:TRANSIENT_UNAVAILABLE",
            automatic_retry_authorized=False,
        )
        assert db.caption_recovery_blocked(item_id)
        assert db.claim_caption_recovery(item_id) == "BLOCKED"
        events = db.caption_recovery_events(item_id)
        event_types = [row["event_type"] for row in events]
        assert event_types.count("AUTOMATIC_RETRY_AUTHORIZED") == 1
        assert event_types.count("AUTOMATIC_RETRY_CLAIMED") == 1
    finally:
        db.close()


def test_pipeline_authorizes_one_transient_retry_after_initial_ia_claim(tmp_path):
    storage = Storage(tmp_path / "project")
    db = Database(storage.database / "pipeline-auto-caption-retry.sqlite3")
    item_id = "source-pipeline-auto-retry_1"
    pipeline = Pipeline(
        db, storage, _UnusedVision(), _UnusedStudio(), ArmoredHub(storage.root), _IA()
    )
    try:
        assert db.claim_caption_recovery(item_id) == "STARTED"
        first_error = AIProviderError("temporary local inference timeout", category="TRANSIENT_UNAVAILABLE")
        pipeline._persist_caption_failure(
            item_id,
            first_error,
            provider_metadata={"provider": "ollama", "model": "gemma3:1b"},
        )
        first = db.caption_recovery(item_id)
        assert first["automatic_retry_authorized"] == 1
        assert first["attempts"] == 1

        assert db.claim_caption_recovery(item_id) == "STARTED"
        second_error = AIProviderError("temporary local inference timeout", category="TRANSIENT_UNAVAILABLE")
        pipeline._persist_caption_failure(
            item_id,
            second_error,
            provider_metadata={"provider": "ollama", "model": "gemma3:1b"},
        )
        second = db.caption_recovery(item_id)
        assert second["automatic_retry_authorized"] == 0
        assert second["attempts"] == 2
        assert db.automatic_caption_retry_claim_count(item_id) == 1
        assert db.claim_caption_recovery(item_id) == "BLOCKED"
    finally:
        db.close()
