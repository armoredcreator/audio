from __future__ import annotations
from pathlib import Path
import logging
import os
import shutil
from .database import Database
from .models import PublicationCheck, State
from .services import (
    AIService,
    Publisher,
    StudioService,
    VisionService,
    VisionDuplicateError,
    VisionUnresolvedError,
    PublicationDeferredError,
    PublicationUnknownError,
)
from .storage import Storage
from .trace import PipelineTrace
from .publication_quarantine import PublicationQuarantine


class Pipeline:
    def __init__(self, db: Database, storage: Storage, vision: VisionService, studio: StudioService, publisher: Publisher, ia: AIService | None = None, publication_quarantine: PublicationQuarantine | None = None):
        self.db, self.storage = db, storage
        self.vision, self.studio, self.publisher, self.ia = vision, studio, publisher, ia
        self.publication_quarantine = publication_quarantine or PublicationQuarantine(
            storage.database / "publication-quarantine.sqlite3"
        )
        self.log = logging.getLogger(__name__)
        self.trace = PipelineTrace(getattr(storage, "storage_root", None) or getattr(storage, "root"))
        self._shutdown_checker = lambda: False
        self._before_hub_checker = None

    def set_shutdown_checker(self, checker) -> None:
        """Attach the Coordinator shutdown signal without coupling layers."""
        self._shutdown_checker = checker

    def set_before_hub_checker(self, checker) -> None:
        """Allow async source owners to release shared Telegram sessions before Hub."""
        self._before_hub_checker = checker

    def run(
        self,
        item_id: str,
        *,
        stop_after_vision: bool = False,
        stop_after_ia: bool = False,
        stop_after_studio: bool = False,
        retry_waiting_vision: bool = False,
    ) -> None:
        item = self.db.get(item_id)
        if self.publication_quarantine.contains(item_id):
            self.trace.emit(
                item_id,
                "PIPELINE",
                "QUARANTINE_BLOCK",
                state=item.state.value,
                reason="unresolved-publication-outcome",
            )
            self.log.warning(
                "[PIPELINE][ITEM %s] blocked by unresolved publication quarantine; state=%s",
                item.content_id,
                item.state.value,
            )
            return
        self.trace.emit(item_id, "PIPELINE", "START", state=item.state.value, attempt=item.attempts + 1)
        self.log.info("[PIPELINE][ITEM %s] início state=%s", item.content_id, item.state.value)
        if item.state == State.DUPLICATE:
            self.trace.emit(
                item_id, "PIPELINE", "DUPLICATE_SKIP",
                duplicate_of=item.duplicate_of_content_id,
                reason="candidate-already-represented-by-canonical-product",
            )
            return
        if item.state in {State.SPEECH, State.STUDIO_BATCH, State.STUDIO_COMPLETE}:
            # These are durable hand-off states. Speech/Studio run as an
            # explicit versioned batch outside Pipeline; never auto-skip the
            # contract boundary or fall through to a publisher.
            self.trace.emit(
                item_id, "PIPELINE", "STAGE_HANDOFF_WAIT",
                state=item.state.value,
                reason="explicit-speech-studio-boundary",
            )
            return
        # WAITING_VISION is deliberately not retried by normal runs. An explicit
        # recovery call may re-enter Vision, but only when the original message
        # supplied a URL. No media download or downstream stage happens here.
        if item.state == State.WAITING_VISION and retry_waiting_vision:
            if str(item.affiliate_url or "").strip():
                self.trace.emit(
                    item_id, "VISION", "RETRY_BLOCKED",
                    reason="waiting-vision-item-already-has-affiliate-url",
                )
                return
            if not str(item.original_url or "").strip():
                self.trace.emit(
                    item_id, "VISION", "RETRY_BLOCKED",
                    reason="missing-original-url",
                )
                return
            self.db.transition(item_id, State.VISION, "explicit-waiting-vision-retry")
            item = self.db.get(item_id)
            self.trace.emit(
                item_id, "VISION", "RETRY_STARTED",
                state=item.state.value,
                reason="explicit-waiting-vision-retry",
            )
        if item.state == State.PUBLISHED:
            if not item.cleanup_completed:
                self.cleanup(item_id)
            self.trace.emit(item_id, "PIPELINE", "END", state=State.PUBLISHED.value, cleanup=item.cleanup_completed)
            return
        # stop_after_vision is a hard boundary, not merely a flag checked after
        # identifying a new product. Already-approved rows must return here
        # before RECEIVED can transition to IA/Studio or any later tool.
        if stop_after_vision and str(item.affiliate_url or "").strip():
            self.trace.emit(
                item_id,
                "PIPELINE",
                "STOP_AFTER_VISION",
                state=item.state.value,
                approved=True,
            )
            self.trace.emit(
                item_id,
                "PIPELINE",
                "END",
                state=item.state.value,
                stop_after_vision=True,
            )
            return
        try:
            self.db.record_attempt(item_id)
            # Vision V1 validates the Shopee product from the URL alone.
            # The immutable media is therefore required only after Vision has
            # accepted the candidate. This is the key bandwidth-saving gate.
            if not stop_after_vision and not item.original_path.is_file():
                raise FileNotFoundError(f"immutable-original-missing: {item.original_path}")
            if item.state == State.RECEIVED:
                if str(item.affiliate_url or "").strip():
                    ia_enabled = (
                        self.ia is not None
                        and os.getenv("ARMORED_IA_ENABLED", "1") == "1"
                        and os.getenv("ARMORED_IA_CAPTION_ENABLED", "1") == "1"
                    )
                    next_state = (
                        State.IA if ia_enabled
                        else State.SPEECH if stop_after_ia
                        else State.STUDIO
                    )
                    self.db.transition(item_id, next_state, "vision-gate-complete")
                    self.trace.emit(
                        item_id,
                        "PIPELINE",
                        "TRANSITION",
                        old_state=State.RECEIVED.value,
                        new_state=next_state.value,
                        reason="vision-gate-complete",
                    )
                    if next_state == State.SPEECH:
                        self.trace.emit(
                            item_id, "PIPELINE", "END",
                            state=State.SPEECH.value,
                            stop_after_ia=True,
                        )
                        return
                else:
                    self.db.transition(item_id, State.VISION, "pipeline-start")
                    self.trace.emit(item_id, "PIPELINE", "TRANSITION", old_state=State.RECEIVED.value, new_state=State.VISION.value, reason="pipeline-start")
            elif item.state == State.RECOVERY:
                result = item.result_path
                if not result and str(item.affiliate_url or "").strip():
                    result = self.storage.result(
                        item_id,
                        item.affiliate_url,
                        item.affiliate_name,
                    )
                if result and result.is_file():
                    if item.result_path is None:
                        self.db.set_result(item_id, result)
                    self.db.transition(item_id, State.PUBLISHING, "recovery-resume-publication")
                    self.trace.emit(item_id, "RECOVERY", "TRANSITION", old_state=State.RECOVERY.value, new_state=State.PUBLISHING.value, reason="recovery-resume-publication")
                elif item.working_path and item.working_path.is_file() and item.affiliate_name:
                    self.db.transition(item_id, State.STUDIO, "recovery-resume-studio")
                    self.trace.emit(item_id, "RECOVERY", "TRANSITION", old_state=State.RECOVERY.value, new_state=State.STUDIO.value, reason="recovery-resume-studio")
                elif item.affiliate_name:
                    self.db.transition(item_id, State.STUDIO, "recovery-rebuild-working")
                    self.trace.emit(item_id, "RECOVERY", "TRANSITION", old_state=State.RECOVERY.value, new_state=State.STUDIO.value, reason="recovery-rebuild-working")
                else:
                    self.db.transition(item_id, State.VISION, "recovery-rebuild-vision")
                    self.trace.emit(item_id, "RECOVERY", "TRANSITION", old_state=State.RECOVERY.value, new_state=State.VISION.value, reason="recovery-rebuild-vision")
            item = self.db.get(item_id)
            if item.state == State.WAITING_VISION:
                self.trace.emit(item_id, "VISION", "BLOCKED", state=State.WAITING_VISION.value, reason=self.db.last_error(item_id) or "vision-waiting")
                return
            if item.state == State.FAILED:
                raise RuntimeError("FAILED item requires deterministic recovery before pipeline.run")

            if item.state == State.VISION:
                self.log.info("[PIPELINE][ITEM %s] VISION iniciando", item.content_id)
                with self.trace.stage(item_id, "VISION"):
                    try:
                        v = self.vision.identify(item)
                    except VisionDuplicateError as exc:
                        self.db.mark_duplicate(
                            item_id,
                            exc.owner_content_id,
                            "canonical-product-known-before-vision-api",
                        )
                        self.trace.emit(
                            item_id,
                            "VISION",
                            "DUPLICATE",
                            duplicate_of=exc.owner_content_id,
                            reason="canonical-product-known-before-vision-api",
                        )
                        return
                    except VisionUnresolvedError as exc:
                        self.db.mark_vision_waiting(item_id, str(exc))
                        self.trace.emit(item_id, "VISION", "WAITING", reason=str(exc))
                        return
                    vision_context = getattr(v, "ia_context", None)
                    duplicate_owner = self.db.commit_vision_result(
                        item_id,
                        v.affiliate_name,
                        v.affiliate_url,
                        affiliate_urls=getattr(v, "affiliate_urls", ()),
                        publication_caption=None,
                        ia_context=vision_context,
                    )
                    if duplicate_owner:
                        self.trace.emit(
                            item_id,
                            "VISION",
                            "DUPLICATE",
                            duplicate_of=duplicate_owner,
                            reason="canonical-product-already-represented",
                        )
                        return
                    self.trace.emit(
                        item_id,
                        "VISION",
                        "RESULT",
                        affiliate_links=len(getattr(v, "affiliate_urls", ()) or ()),
                        caption=bool(getattr(v, "publication_caption", None)),
                    )
                self.log.info("[PIPELINE][ITEM %s] VISION concluída", item_id)
                if stop_after_vision:
                    # Vision is the pre-download gate. Persist its evidence,
                    # then return the durable lifecycle to RECEIVED so the
                    # normal pipeline resumes from the persisted affiliate_url
                    # after materialization without running Vision twice.
                    self.db.transition(item_id, State.RECEIVED, "vision-gate-complete")
                    self.trace.emit(
                        item_id,
                        "VISION",
                        "TRANSITION",
                        old_state=State.VISION.value,
                        new_state=State.RECEIVED.value,
                        reason="vision-gate-complete",
                    )
                    return
                if self.ia is not None and os.getenv("ARMORED_IA_ENABLED", "1") == "1" and os.getenv("ARMORED_IA_CAPTION_ENABLED", "1") == "1":
                    self.db.transition(item_id, State.IA, "vision-complete")
                    self.trace.emit(item_id, "VISION", "TRANSITION", old_state=State.VISION.value, new_state=State.IA.value, reason="vision-complete")
                else:
                    next_state = State.SPEECH if stop_after_ia else State.STUDIO
                    self.db.transition(item_id, next_state, "vision-complete")
                    self.trace.emit(
                        item_id, "VISION", "TRANSITION",
                        old_state=State.VISION.value, new_state=next_state.value,
                        reason="vision-complete",
                    )
                    if next_state == State.SPEECH:
                        self.trace.emit(
                            item_id, "PIPELINE", "END",
                            state=State.SPEECH.value, stop_after_ia=True,
                        )
                        return
            item = self.db.get(item_id)
            if item.state == State.IA:
                self.log.info("[PIPELINE][ITEM %s] ARMOREDIA iniciando", item.content_id)
                if self.ia is None:
                    raise RuntimeError("armored-ia-service-not-configured")
                if not item.ia_context:
                    # Older, already-approved rows can predate durable Vision
                    # context persistence. Rebuild only the minimum caption
                    # context from the stored approval; never rerun Vision or
                    # resolve a product through Shopee as a recovery shortcut.
                    approved_name = str(item.affiliate_name or "").strip()
                    if item.affiliate_url and approved_name:
                        fallback_context = {"productName": approved_name}
                        self.db.set_ia_context(item_id, fallback_context)
                        item = self.db.get(item_id)
                        self.trace.emit(
                            item_id,
                            "IA",
                            "CONTEXT_RECOVERED",
                            source="persisted_vision_approval",
                            fields=sorted(fallback_context),
                        )
                    if not item.ia_context:
                        raise RuntimeError("armored-ia-context-missing")
                configuration = getattr(self.ia, "provider_configuration", None)
                provider_metadata = configuration() if callable(configuration) else {"provider": "custom", "model": None}
                self.trace.emit(item_id, "IA", "CONFIGURED", **provider_metadata)
                with self.trace.stage(item_id, "IA"), self.db.ia_generation_attempt(item_id, **provider_metadata):
                    generate_with_evidence = getattr(
                        self.ia,
                        "generate_caption_with_evidence",
                        None,
                    )
                    if callable(generate_with_evidence):
                        try:
                            generated = generate_with_evidence(dict(item.ia_context))
                        except Exception as exc:
                            batch_id = getattr(exc, "batch_id", None)
                            evaluations = getattr(exc, "evaluations", ())
                            if batch_id and evaluations:
                                self.db.record_caption_candidates(
                                    item_id,
                                    batch_id,
                                    evaluations,
                                )
                            raise
                        self.db.record_caption_candidates(
                            item_id,
                            generated.batch_id,
                            generated.evaluations,
                        )
                        caption = generated.caption
                        self.trace.emit(
                            item_id,
                            "IA",
                            "EVALUATED",
                            batch_id=generated.batch_id,
                            candidates=len(generated.evaluations),
                            valid=sum(
                                1
                                for evaluation in generated.evaluations
                                if evaluation.policy_valid
                            ),
                            selected=generated.selection.index,
                            score=generated.selection.score,
                        )
                    else:
                        caption = self.ia.generate_caption(dict(item.ia_context))
                    if not str(caption or "").strip():
                        raise RuntimeError("armored-ia-empty-caption")
                    self.db.set_caption(item_id, str(caption))
                    self.trace.emit(item_id, "IA", "RESULT", caption=True)
                next_state = State.SPEECH if stop_after_ia else State.STUDIO
                reason = "ia-complete-awaiting-speech" if stop_after_ia else "ia-complete"
                self.db.transition(item_id, next_state, reason)
                self.trace.emit(
                    item_id, "IA", "TRANSITION",
                    old_state=State.IA.value, new_state=next_state.value, reason=reason,
                )
                if stop_after_ia:
                    self.trace.emit(
                        item_id, "PIPELINE", "END",
                        state=State.SPEECH.value, stop_after_ia=True,
                    )
                    return

            item = self.db.get(item_id)
            if item.state == State.STUDIO:
                self.log.info("[PIPELINE][ITEM %s] STUDIO/RVC iniciando", item.content_id)
                if not item.affiliate_name:
                    raise RuntimeError("studio-requires-affiliate-metadata")
                with self.trace.stage(item_id, "STUDIO"):
                    studio = self.studio.process(item)
                    if studio.working_path is not None:
                        if not studio.working_path.is_file():
                            raise FileNotFoundError("studio-working-file-missing")
                        self.db.set_working(item_id, studio.working_path)
                    if not studio.result_path.is_file():
                        raise FileNotFoundError("studio-result-file-missing")
                    self.db.set_result(item_id, studio.result_path)
                    self.trace.emit(
                        item_id,
                        "STUDIO",
                        "RESULT",
                        working=bool(studio.working_path),
                        result_exists=studio.result_path.is_file(),
                    )
                self.log.info("[PIPELINE][ITEM %s] STUDIO concluído result=%s", item_id, studio.result_path)
                if stop_after_studio:
                    self.db.transition(item_id, State.STUDIO_COMPLETE, "studio-complete-lab-boundary")
                    self.trace.emit(
                        item_id, "STUDIO", "TRANSITION",
                        old_state=State.STUDIO.value,
                        new_state=State.STUDIO_COMPLETE.value,
                        reason="studio-complete-lab-boundary",
                    )
                    self.trace.emit(
                        item_id, "PIPELINE", "END",
                        state=State.STUDIO_COMPLETE.value,
                        publication_disabled=True,
                    )
                    return
                self.db.transition(item_id, State.PUBLISHING, "studio-complete")
                self.trace.emit(item_id, "STUDIO", "TRANSITION", old_state=State.STUDIO.value, new_state=State.PUBLISHING.value, reason="studio-complete")

            item = self.db.get(item_id)
            if item.state == State.PUBLISHING:
                if self._before_hub_checker is not None:
                    self._before_hub_checker()
                self.log.info("[PIPELINE][ITEM %s] HUB/PUBLICAÇÃO iniciando", item.content_id)
                if not item.result_path or not item.result_path.is_file():
                    raise FileNotFoundError("publication-result-missing")

                with self.trace.stage(item_id, "HUB"):
                    publish_once = getattr(self.publisher, "publish_once", None)
                    if callable(publish_once):
                        try:
                            result = publish_once(item)
                        except PublicationDeferredError as exc:
                            self.trace.emit(
                                item_id, "HUB", "DEFERRED", reason=str(exc),
                                state=item.state.value,
                            )
                            return
                        except PublicationUnknownError as exc:
                            self.db.transition(item_id, State.RECOVERY, str(exc))
                            self.trace.emit(item_id, "HUB", "UNKNOWN", reason=str(exc), new_state=State.RECOVERY.value)
                            return

                        if not result.confirmed:
                            raise RuntimeError("publication-not-confirmed")
                        if not result.message_id:
                            raise RuntimeError("confirmed-publication-without-message-id")
                        self.db.publication_confirmed(item_id, str(result.message_id))
                        self.trace.emit(item_id, "TELEGRAM", "CONFIRMED", message_id=str(result.message_id))
                    else:
                        self.db.publication_started(item_id)
                        check = self.publisher.check_publication(item)
                        self.trace.emit(item_id, "HUB", "CHECK", result=check.value)
                        if check == PublicationCheck.UNKNOWN:
                            self.db.transition(
                                item_id,
                                State.RECOVERY,
                                "publication-check-uncertain-refusing-to-publish",
                            )
                            self.trace.emit(item_id, "TELEGRAM", "UNKNOWN", reason="publication-check-uncertain-refusing-to-publish", new_state=State.RECOVERY.value)
                            return

                        if check == PublicationCheck.ABSENT:
                            try:
                                result = self.publisher.publish(item)
                            except PublicationUnknownError as exc:
                                self.db.transition(item_id, State.RECOVERY, str(exc))
                                self.trace.emit(item_id, "TELEGRAM", "UNKNOWN", reason=str(exc), new_state=State.RECOVERY.value)
                                return

                            if not result.confirmed:
                                raise RuntimeError("publication-not-confirmed")
                            if not result.message_id:
                                raise RuntimeError("confirmed-publication-without-message-id")
                            self.db.publication_confirmed(item_id, str(result.message_id))
                            self.trace.emit(item_id, "TELEGRAM", "CONFIRMED", message_id=str(result.message_id))
                        else:
                            pub = self.db.publication(item_id)
                            message_id = pub["published_message_id"] if pub else None
                            if not message_id:
                                self.db.transition(
                                    item_id,
                                    State.RECOVERY,
                                    "confirmed-publication-without-real-message-id",
                                )
                                self.trace.emit(item_id, "TELEGRAM", "UNKNOWN", reason="confirmed-publication-without-real-message-id", new_state=State.RECOVERY.value)
                                return
                            self.db.publication_confirmed(item_id, str(message_id))
                            self.trace.emit(item_id, "TELEGRAM", "CONFIRMED", message_id=str(message_id))

                self.db.transition(item_id, State.PUBLISHED, "publication-confirmed")
                self.trace.emit(item_id, "PIPELINE", "TRANSITION", old_state=State.PUBLISHING.value, new_state=State.PUBLISHED.value, reason="publication-confirmed")
                self.log.info("[PIPELINE][ITEM %s] PUBLICADO confirmado; cleanup iniciando", item_id)
                self.cleanup(item_id)
                self.log.info("[PIPELINE][ITEM %s] FINALIZADO PUBLISHED+cleanup", item_id)
                self.trace.emit(item_id, "PIPELINE", "END", state=State.PUBLISHED.value, cleanup=True)
        except Exception as exc:
            current = self.db.get(item_id)
            if current.state == State.PUBLISHED:
                self.trace.emit(item_id, "PIPELINE", "ERROR_AFTER_PUBLISH", error_type=type(exc).__name__, error=str(exc))
                raise
            if self._shutdown_checker():
                self.log.warning(
                    "[PIPELINE][ITEM %s] shutdown solicitado; preservando state=%s para recovery: %s",
                    item_id, current.state.value, exc,
                )
                self.trace.emit(item_id, "PIPELINE", "SHUTDOWN", state=current.state.value, error_type=type(exc).__name__, error=str(exc))
                raise KeyboardInterrupt from exc
            reason = f"{type(exc).__name__}: {exc}"
            # Recovery is valid only when the immutable ORIGINAL exists. Before
            # download, preserve RECEIVED/VISION so the explicit Vision/Stock
            # stages can retry from SQLite instead of stranding the item.
            if not current.original_path.is_file():
                retry_state = (
                    current.state
                    if current.state in (State.RECEIVED, State.VISION, State.FAILED)
                    else (
                        State.RECEIVED
                        if current.affiliate_url
                        else State.VISION
                    )
                )
                if current.state != retry_state:
                    self.db.transition(
                        item_id,
                        retry_state,
                        "retryable-missing-original",
                    )
                self.db.record_retryable_error(item_id, reason)
                self.log.error(
                    "[PIPELINE][ITEM %s] RETRYABLE sem ORIGINAL state=%s: %s",
                    item_id,
                    retry_state.value,
                    exc,
                )
                self.trace.emit(
                    item_id,
                    "PIPELINE",
                    "RETRYABLE_NO_ORIGINAL",
                    state=retry_state.value,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise

            # Processing-stage failures with an immutable ORIGINAL are
            # recoverable from durable artifacts/state.
            self.db.transition(item_id, State.RECOVERY, reason)
            self.log.error(
                "[PIPELINE][ITEM %s] ERRO RECOVERABLE state=%s: %s",
                item_id,
                current.state.value,
                exc,
            )
            self.trace.emit(
                item_id,
                "PIPELINE",
                "RECOVERY",
                state=current.state.value,
                new_state=State.RECOVERY.value,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    def cleanup(self, item_id: int) -> None:
        item = self.db.get(item_id)
        if item.state != State.PUBLISHED:
            raise RuntimeError("cleanup-is-allowed-only-after-PUBLISHED")
        workspace = item.workspace.resolve()
        original = item.original_path.resolve()
        result = item.result_path.resolve() if item.result_path is not None else None
        if workspace != original.parent.resolve():
            raise RuntimeError("cleanup-workspace-mismatch")
        if result is not None and result.parent != workspace:
            raise RuntimeError("cleanup-result-workspace-mismatch")
        with self.trace.stage(item_id, "CLEANUP"):
            if not workspace.is_dir():
                self.db.mark_cleanup_completed(item_id)
                return
            for path in workspace.iterdir():
                resolved = path.resolve()
                if resolved == original or resolved == result:
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            self.db.mark_cleanup_completed(item_id)
