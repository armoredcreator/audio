from __future__ import annotations
import uuid
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
from ArmoredIA.caption.policy import CaptionPolicyError, validate_caption
from ArmoredIA.caption.selector import CaptionGenerationError, CaptionSelector
from ArmoredIA.providers.base import AIProviderError


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

    def _caption_is_policy_valid(self, item, caption: str | None) -> bool:
        raw = str(caption or "").strip()
        if not raw or not item.affiliate_name:
            return False
        try:
            canonical = validate_caption(
                raw,
                product_name=str(item.affiliate_name),
                product_context=item.ia_context or {},
            )
        except CaptionPolicyError:
            return False
        return canonical == raw

    @staticmethod
    def _caption_failure_reason(exc: Exception) -> tuple[str, str, bool]:
        category = str(getattr(exc, "category", "") or type(exc).__name__)
        if isinstance(exc, CaptionPolicyError):
            return "caption-content-rejected", category, False
        if isinstance(exc, CaptionGenerationError):
            return "caption-candidates-exhausted-policy", category, False
        if isinstance(exc, AIProviderError):
            if category in {"TRANSIENT_UNAVAILABLE", "RATE_LIMITED", "RETRY_BUDGET_EXHAUSTED"}:
                return f"caption-provider-temporary:{category}", category, True
            if category == "AUTHENTICATION_OR_PERMISSION":
                return "caption-provider-authentication-required", category, False
            return f"caption-provider-failed:{category}", category, False
        return f"caption-recovery-{type(exc).__name__}", category, False

    def _persist_caption_failure(
        self,
        item_id: str,
        exc: Exception,
        *,
        provider_metadata: dict,
        attempt_id: int | None = None,
        batch_id: str | None = None,
        evaluations=(),
    ) -> str:
        if batch_id and evaluations:
            self.db.record_caption_candidates(item_id, batch_id, evaluations)
        if attempt_id is not None:
            provider_obj = getattr(getattr(self.ia, "caption_generator", None), "provider", None)
            request_count = max(0, int(getattr(exc, "provider_requests", 0)))
            self.db.record_ia_generation_evidence(
                attempt_id,
                provider_requests=request_count,
                policy_retries=max(0, request_count - 1),
                feedback=getattr(exc, "feedback_rounds", ()),
                provider_attempts=(
                    getattr(exc, "provider_attempts", ())
                    or getattr(provider_obj, "last_attempts", ())
                ),
            )
        reason, category, retryable = self._caption_failure_reason(exc)
        try:
            auto_retry_limit = int(os.getenv("ARMORED_IA_AUTO_TRANSIENT_RETRIES", "1"))
        except ValueError:
            auto_retry_limit = 0
        auto_retry_limit = max(0, min(auto_retry_limit, 2))
        # Count retries actually claimed from the append-only ledger. The
        # recovery row's `attempts` also includes the initial IA attempt, so
        # comparing it to the retry budget incorrectly denied the first retry.
        automatic_retry_claims = self.db.automatic_caption_retry_claim_count(item_id)
        publication = self.db.publication(item_id)
        no_uncertain_publication = publication is None or (
            str(publication["verification_status"] or "").upper() == "ABSENT_CONFIRMED"
            and not bool(publication["confirmed"])
            and not str(publication["published_message_id"] or "").strip()
        )
        automatic_retry_authorized = bool(
            retryable
            and no_uncertain_publication
            and automatic_retry_claims < auto_retry_limit
        )
        self.db.finish_caption_recovery(
            item_id,
            status="BLOCKED",
            reason=reason,
            provider=str(provider_metadata.get("provider") or "custom"),
            model=provider_metadata.get("model"),
            automatic_retry_authorized=automatic_retry_authorized,
            evidence={
                "batch_id": batch_id,
                "candidate_count": len(evaluations),
                "error_type": type(exc).__name__,
                "error_category": category,
                "retryable_after_operator_review": retryable,
                "automatic_retry_authorized": automatic_retry_authorized,
                "automatic_retry_limit": auto_retry_limit,
                "automatic_retry_claims": automatic_retry_claims,
            },
        )
        self.trace.emit(
            item_id,
            "IA",
            "CAPTION_BLOCKED",
            reason=reason,
            retryable_after_operator_review=retryable,
        )
        return reason

    def _ensure_publication_caption(self, item_id: str) -> bool:
        """Recover a missing Hub caption once from approved IA evidence, before send."""
        item = self.db.get(item_id)
        publication = self.db.publication(item_id)
        if publication is not None:
            verification_status = str(publication["verification_status"] or "").upper()
            receipt_message_id = str(publication["published_message_id"] or "").strip()
            if verification_status == "CONFIRMED" or bool(publication["confirmed"]):
                self.trace.emit(
                    item_id, "HUB", "CAPTION_RECOVERY_BLOCKED",
                    reason="confirmed-publication-history-is-immutable",
                )
                return False
            if verification_status != "ABSENT_CONFIRMED":
                self.publication_quarantine.quarantine_unknown(
                    item_id,
                    publication,
                    evidence={"item_state": item.state.value, "reason": "caption-recovery-saw-existing-receipt"},
                )
                self.trace.emit(
                    item_id, "HUB", "CAPTION_RECOVERY_BLOCKED",
                    reason="publication-receipt-requires-explicit-reconciliation",
                )
                return False
            if receipt_message_id and verification_status != "ABSENT_CONFIRMED":
                self.publication_quarantine.quarantine_unknown(
                    item_id, publication,
                    evidence={"item_state": item.state.value, "reason": "message-id-without-absence-reconciliation"},
                )
                return False

        raw_caption = str(item.publication_caption or "").strip()
        if self._caption_is_policy_valid(item, raw_caption):
            return True

        selected = self.db.selected_valid_caption(item_id)
        if selected and self._caption_is_policy_valid(item, selected):
            self.db.set_caption(item_id, selected)
            self.db.finish_caption_recovery(item_id, status="SUCCEEDED", reason="reused-persisted-policy-selection")
            self.trace.emit(
                item_id, "IA", "CAPTION_RECOVERED",
                source="persisted-selected-policy-candidate",
            )
            return True

        recovery = self.db.caption_recovery(item_id)
        if recovery is not None and str(recovery["status"]) == "SUCCEEDED":
            # A caption may have been approved by an older Policy and remain
            # persisted while the current Hub contract has since become
            # stricter. Do not loop on that stale success or silently replace
            # it: preserve the text and evidence, then require one explicit,
            # auditable retry after verifying the publication receipt.
            reason = "persisted-caption-failed-current-policy"
            self.db.finish_caption_recovery(
                item_id,
                status="BLOCKED",
                reason=reason,
                evidence={
                    "previous_caption": raw_caption or None,
                    "selected_candidate": selected,
                    "current_policy_rejected_persisted_caption": True,
                },
            )
            self.trace.emit(
                item_id, "HUB", "CAPTION_RECOVERY_BLOCKED",
                reason=reason,
                previous_caption_preserved=bool(raw_caption),
            )
            return False

        if (
            recovery is not None
            and str(recovery["status"]) == "BLOCKED"
            and self.db.caption_recovery_blocked(item_id)
        ):
            self.trace.emit(
                item_id, "HUB", "CAPTION_RECOVERY_BLOCKED",
                reason=str(recovery["reason"] or "operator-review-required"),
            )
            return False

        context = dict(item.ia_context or {})
        if not context:
            product_name = str(item.affiliate_name or "").strip()
            if product_name and str(item.affiliate_url or "").strip():
                context = {"productName": product_name}
                self.db.set_ia_context(item_id, context)
                item = self.db.get(item_id)
                self.trace.emit(
                    item_id, "IA", "CONTEXT_RECOVERED",
                    source="persisted_vision_approval",
                    fields=["productName"],
                )
            else:
                self.db.finish_caption_recovery(
                    item_id, status="BLOCKED", reason="approved-product-context-missing",
                )
                self.trace.emit(
                    item_id, "HUB", "CAPTION_RECOVERY_BLOCKED",
                    reason="approved-product-context-missing",
                )
                return False

        if self.ia is None:
            self.db.finish_caption_recovery(
                item_id, status="BLOCKED", reason="armored-ia-service-not-configured",
            )
            return False

        claim = self.db.claim_caption_recovery(
            item_id,
            previous_caption=raw_caption or None,
        )
        if claim != "STARTED":
            self.trace.emit(item_id, "HUB", "CAPTION_RECOVERY_BLOCKED", reason=claim.casefold())
            return False

        configuration = getattr(self.ia, "provider_configuration", None)
        provider_metadata = configuration() if callable(configuration) else {"provider": "custom", "model": None}
        self.trace.emit(item_id, "IA", "CAPTION_RECOVERY_STARTED", **provider_metadata)
        batch_id = None
        evaluations = ()
        ia_attempt_id = None
        try:
            generate_with_evidence = getattr(self.ia, "generate_caption_with_evidence", None)
            if not callable(generate_with_evidence):
                raise RuntimeError("armored-ia-evidence-api-required-for-caption-recovery")
            with self.trace.stage(item_id, "IA_CAPTION_RECOVERY"), self.db.ia_generation_attempt(
                item_id,
                provider=str(provider_metadata.get("provider") or "custom"),
                model=provider_metadata.get("model"),
            ) as ia_attempt_id:
                generated = generate_with_evidence(context)
                batch_id = str(generated.batch_id)
                evaluations = tuple(generated.evaluations)
                self.db.record_caption_candidates(item_id, batch_id, evaluations)
                self.db.record_ia_generation_evidence(
                    ia_attempt_id,
                    provider_requests=generated.provider_requests,
                    policy_retries=max(0, int(generated.provider_requests) - 1),
                    feedback=generated.feedback_rounds,
                    provider_attempts=generated.provider_attempts,
                )
                caption = str(generated.caption or "").strip()
                if not self._caption_is_policy_valid(self.db.get(item_id), caption):
                    raise CaptionPolicyError("recovered-caption-failed-current-policy")
                # SQLite commits the selected candidates before caption text. Both
                # are durable before Hub performs even its dry-run validation.
                self.db.set_caption(item_id, caption)
            self.db.finish_caption_recovery(
                item_id,
                status="SUCCEEDED",
                reason="armored-ia-policy-approved",
                provider=str(provider_metadata.get("provider") or "custom"),
                model=provider_metadata.get("model"),
                evidence={"batch_id": batch_id, "candidate_count": len(evaluations), "policy_valid": True},
            )
            self.trace.emit(
                item_id, "IA", "CAPTION_RECOVERED",
                source="armored-ia",
                batch_id=batch_id,
                candidates=len(evaluations),
                policy_valid=True,
            )
            return True
        except Exception as exc:
            # CaptionGenerator attaches the full failed batch to policy errors.
            # Preserve those candidates too; otherwise a failed local attempt
            # leaves only a generic BLOCKED reason and cannot be audited or
            # used to teach the next explicitly authorized retry.
            if batch_id is None:
                batch_id = getattr(exc, "batch_id", None)
            if not evaluations:
                evaluations = tuple(getattr(exc, "evaluations", ()) or ())
            reason = self._persist_caption_failure(
                item_id,
                exc,
                provider_metadata=provider_metadata,
                attempt_id=ia_attempt_id,
                batch_id=batch_id,
                evaluations=evaluations,
            )
            self.trace.emit(
                item_id, "HUB", "CAPTION_RECOVERY_BLOCKED",
                reason=reason,
                ia_attempted=True,
            )
            self.log.error("[PIPELINE][ITEM %s] caption recovery durably blocked: %s", item_id, reason)
            return False

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
        if self.db.caption_recovery_blocked(item_id):
            self.trace.emit(
                item_id,
                "PIPELINE",
                "CAPTION_BLOCKED_SKIP",
                state=item.state.value,
                reason="explicit-caption-recovery-review-required",
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
                    self.db.finish_caption_recovery(
                        item_id, status="BLOCKED", reason="armored-ia-service-not-configured",
                    )
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
                        self.db.finish_caption_recovery(
                            item_id, status="BLOCKED", reason="approved-product-context-missing",
                        )
                        raise RuntimeError("armored-ia-context-missing")
                prior_caption_recovery = self.db.caption_recovery(item_id)
                if (
                    prior_caption_recovery is not None
                    and str(prior_caption_recovery["status"]) == "BLOCKED"
                    and (
                        int(prior_caption_recovery["manual_retry_authorized"] or 0)
                        or int(prior_caption_recovery["automatic_retry_authorized"] or 0)
                    )
                ):
                    claim = self.db.claim_caption_recovery(item_id)
                    if claim != "STARTED":
                        return
                configuration = getattr(self.ia, "provider_configuration", None)
                provider_metadata = configuration() if callable(configuration) else {"provider": "custom", "model": None}
                self.trace.emit(item_id, "IA", "CONFIGURED", **provider_metadata)
                ia_attempt_id = None
                generated = None
                try:
                    with self.trace.stage(item_id, "IA"), self.db.ia_generation_attempt(item_id, **provider_metadata) as ia_attempt_id:
                        generate_with_evidence = getattr(
                            self.ia,
                            "generate_caption_with_evidence",
                            None,
                        )
                        if callable(generate_with_evidence):
                            generated = generate_with_evidence(dict(item.ia_context))
                            self.db.record_caption_candidates(
                                item_id,
                                generated.batch_id,
                                generated.evaluations,
                            )
                            self.db.record_ia_generation_evidence(
                                ia_attempt_id,
                                provider_requests=generated.provider_requests,
                                policy_retries=max(0, int(generated.provider_requests) - 1),
                                feedback=generated.feedback_rounds,
                                provider_attempts=generated.provider_attempts,
                            )
                            caption = generated.caption
                            self.trace.emit(
                                item_id,
                                "IA",
                                "EVALUATED",
                                batch_id=generated.batch_id,
                                candidates=len(generated.evaluations),
                                valid=sum(1 for evaluation in generated.evaluations if evaluation.policy_valid),
                                selected=generated.selection.index,
                                score=generated.selection.score,
                                provider_requests=generated.provider_requests,
                                policy_retries=max(0, int(generated.provider_requests) - 1),
                            )
                        else:
                            raw_caption = str(self.ia.generate_caption(dict(item.ia_context)) or "")
                            legacy_selector = CaptionSelector()
                            evaluations = legacy_selector.evaluate(
                                [raw_caption],
                                product_name=str(item.affiliate_name or ""),
                                product_context=dict(item.ia_context or {}),
                            )
                            batch_id = f"legacy-{uuid.uuid4().hex}"
                            try:
                                selection, marked = legacy_selector.choose(evaluations)
                            except CaptionGenerationError:
                                self.db.record_caption_candidates(item_id, batch_id, evaluations)
                                raise
                            self.db.record_caption_candidates(item_id, batch_id, marked)
                            caption = selection.caption
                        if not str(caption or "").strip():
                            raise RuntimeError("armored-ia-empty-caption")
                        if not self._caption_is_policy_valid(self.db.get(item_id), str(caption)):
                            raise CaptionPolicyError("armored-ia-caption-failed-shared-policy")
                        self.db.set_caption(item_id, str(caption))
                        self.trace.emit(item_id, "IA", "RESULT", caption=True)
                except Exception as exc:
                    self._persist_caption_failure(
                        item_id,
                        exc,
                        provider_metadata=provider_metadata,
                        attempt_id=ia_attempt_id,
                        batch_id=getattr(generated, "batch_id", None) or getattr(exc, "batch_id", None),
                        evaluations=getattr(generated, "evaluations", ()) or getattr(exc, "evaluations", ()),
                    )
                    raise
                if prior_caption_recovery is not None:
                    self.db.finish_caption_recovery(
                        item_id,
                        status="SUCCEEDED",
                        reason="armored-ia-policy-approved",
                        provider=str(provider_metadata.get("provider") or "custom"),
                        model=provider_metadata.get("model"),
                        evidence={
                            "batch_id": getattr(generated, "batch_id", None),
                            "policy_valid": True,
                        },
                    )
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
                self.log.info("[PIPELINE][ITEM %s] HUB/PUBLICAÇÃO iniciando", item.content_id)
                if not item.result_path or not item.result_path.is_file():
                    raise FileNotFoundError("publication-result-missing")
                if not self._ensure_publication_caption(item_id):
                    return
                item = self.db.get(item_id)
                if self._before_hub_checker is not None:
                    self._before_hub_checker()

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
