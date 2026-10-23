from __future__ import annotations

from pathlib import Path
from typing import Callable

from .database import Database
from .models import PublicationCheck, State
from .pipeline import Pipeline
from .services import AIService, Publisher, StudioService, VisionService
from .storage import Storage


class Recovery:
    """Evidence-driven recovery for one durable pipeline item."""

    def __init__(
        self,
        db: Database,
        storage: Storage,
        vision: VisionService,
        studio: StudioService,
        publisher: Publisher,
        ia: AIService | None = None,
        *,
        speech_studio_runner: Callable[[str], None] | None = None,
    ):
        self.db, self.storage = db, storage
        self.pipeline = Pipeline(db, storage, vision, studio, publisher, ia)
        self.speech_studio_runner = speech_studio_runner

    def _preserve_artifacts(self, item_id: str, paths: list[Path], reason: str) -> None:
        """Move selected entries to a unique, receipted backup; never delete bytes."""
        if not paths:
            return
        item = self.db.get(item_id)
        self.storage.preserve_artifacts(paths, workspace=item.workspace,
                                        content_id=str(item_id), reason=reason)

    def preserve_partial_download(self, item_id: str) -> None:
        item = self.db.get(item_id)
        partial = item.original_path.with_suffix(item.original_path.suffix + ".part")
        if partial.exists() or partial.is_symlink():
            self._preserve_artifacts(item_id, [partial], "interrupted-original-download")

    def _reset_processing_artifacts(self, item_id: str) -> None:
        """Archive derived entries before rebuilding; keep immutable ORIGINAL."""
        item = self.db.get(item_id)
        original = item.original_path.resolve()
        workspace = item.workspace.resolve()

        if workspace != original.parent.resolve():
            raise RuntimeError("recovery-workspace-mismatch")

        if workspace.is_dir():
            original_entry = Path(item.original_path)
            if not original_entry.is_absolute():
                original_entry = original_entry.resolve()
            original_entry = original_entry.absolute()
            preserve = []
            for path in list(workspace.iterdir()):
                # Preserve both the stored ORIGINAL entry and its resolved target.
                # The stored entry itself may be a symlink to the immutable file.
                if path.absolute() == original_entry or path.resolve() == original:
                    continue
                # Moving the link preserves it without traversing its target.
                preserve.append(path)
            self._preserve_artifacts(item_id, preserve, "rebuild-derived-artifacts")

        self.db.set_working(item_id, None)
        self.db.set_result(item_id, None)

    def _rebuild_processing_from_original(self, item_id: str) -> None:
        self._reset_processing_artifacts(item_id)
        if self.speech_studio_runner is not None:
            self.db.transition(
                item_id, State.SPEECH, "recovery-rebuild-speech-v5-from-original"
            )
            self.speech_studio_runner(item_id)
            return
        self.db.transition(item_id, State.STUDIO, "recovery-rebuild-studio-from-original")
        self.pipeline.run(item_id)

    def _has_speech_studio_v5_completion(self, item_id: str) -> bool:
        return self.db.has_speech_studio_v5_completion(item_id)

    def _resume_durable_result(self, item_id: str, reason: str) -> None:
        self.db.transition(item_id, State.PUBLISHING, reason)
        # Coordinator-owned v5 recovery prepares the durable boundary only.
        # Its outer loop decides when the whole recovered batch may enter Hub.
        if self.speech_studio_runner is None:
            self.pipeline.run(item_id)

    def reconcile(self, item_id: str) -> None:
        item = self.db.get(item_id)
        starting_state = item.state

        # Capture the failure evidence BEFORE Recovery writes its own
        # startup-recovery state event. Otherwise the evidence would be lost.
        last_event = self.db.last_state_event(item_id)

        # ShopeeProductNotFound/VisionUnresolved remains WAITING_VISION.
        if item.state == State.WAITING_VISION:
            if self.speech_studio_runner is not None:
                # Coordinator retries require the explicit reviewed Vision
                # allowlist and runtime lease; generic recovery cannot opt in.
                return
            self.db.transition(item_id, State.VISION, "recovery-retry-waiting-vision")
            item = self.db.get(item_id)
            last_event = self.db.last_state_event(item_id)

        if item.state == State.PUBLISHED:
            self.pipeline.cleanup(item_id)
            return

        if not item.original_path.is_file():
            raise FileNotFoundError(
                f"cannot-recover-without-immutable-original: {item.original_path}"
            )

        if item.state in {State.SPEECH, State.STUDIO_BATCH} and self.speech_studio_runner is not None:
            if self.db.publication(item_id):
                raise RuntimeError("publication-record-unexpected-before-speech-studio-v5-complete")
            self.db.record_recovery(item_id)
            self.db.transition(item_id, State.RECOVERY, "recover-interrupted-speech-studio-v5")
            self._reset_processing_artifacts(item_id)
            self.db.transition(
                item_id,
                State.SPEECH,
                "recovery-restart-speech-studio-v5-from-original",
            )
            self.speech_studio_runner(item_id)
            return

        self.db.record_recovery(item_id)
        self.db.transition(item_id, State.RECOVERY, "startup-recovery")
        item = self.db.get(item_id)

        # Telegram is a separate side-effect boundary. Reconcile it before
        # changing processing artifacts whenever a publication attempt exists.
        pub = self.db.publication(item_id)
        if pub:
            if (
                pub["confirmed"]
                and str(pub["verification_status"] or "") == "CONFIRMED"
            ):
                message_id = pub["published_message_id"]
                if not message_id:
                    raise RuntimeError("confirmed-publication-without-real-message-id")
                self.db.transition(item_id, State.PUBLISHED, "db-publication-already-confirmed")
                self.pipeline.cleanup(item_id)
                return

            if (
                self.speech_studio_runner is not None
                and str(pub["verification_status"] or "") == "PENDING"
                and not pub["published_message_id"]
                and self._has_speech_studio_v5_completion(item_id)
                and item.result_path is not None
                and item.result_path.is_file()
            ):
                # The v5 Hub commits SENT_UNVERIFIED before any send. A
                # preflight-only failure must not destroy a completed render.
                # The outer Coordinator/Hub still revalidates bytes before send.
                self._resume_durable_result(item_id, "recovery-v5-hub-preflight-not-sent")
                return

            check = self.pipeline.publisher.check_publication(item)
            if check == PublicationCheck.UNKNOWN:
                raise RuntimeError("publication-check-uncertain-recovery-stopped")

            if check == PublicationCheck.CONFIRMED:
                refreshed = self.db.publication(item_id)
                message_id = refreshed["published_message_id"] if refreshed else None
                if not message_id:
                    raise RuntimeError("telegram-confirmed-without-real-message-id")
                self.db.publication_confirmed(item_id, str(message_id))
                self.db.transition(item_id, State.PUBLISHED, "publisher-confirms-existing")
                self.pipeline.cleanup(item_id)
                return

            # ABSENT means there is no Telegram evidence of a completed
            # publication. Treat the whole derived processing chain as invalid
            # and restart from the immutable ORIGINAL. Never reuse a result
            # merely because it still exists on disk.
            if item.affiliate_name:
                self._rebuild_processing_from_original(item_id)
                return

            # The only safe exception is unresolved Vision: without resolved
            # product metadata, restart at Vision from the immutable ORIGINAL.
            self.db.transition(item_id, State.VISION, "publication-absent-rebuild-vision")
            self.pipeline.run(item_id)
            return

        # ArmoredIA is its own recoverable stage. Its failure never
        # invalidates or reruns a product already resolved by Vision V1.
        if (
            starting_state == State.IA
            or (
                last_event
                and last_event["old_state"] == State.IA.value
                and last_event["new_state"] == State.RECOVERY.value
            )
        ):
            self.db.transition(item_id, State.IA, "recovery-retry-ia")
            self.pipeline.run(
                item_id,
                stop_after_ia=self.speech_studio_runner is not None,
            )
            if self.speech_studio_runner is not None:
                current = self.db.get(item_id)
                if current.state == State.SPEECH:
                    self.speech_studio_runner(item_id)
            return

        # A processing-stage exception is stronger evidence than the mere
        # presence of a result file. If Studio failed, any derived result in
        # the workspace may be partial/corrupt; rebuild from ORIGINAL.
        if (
            last_event
            and last_event["old_state"] in {State.SPEECH.value, State.STUDIO_BATCH.value, State.STUDIO.value}
            and last_event["new_state"] == State.RECOVERY.value
        ):
            self._rebuild_processing_from_original(item_id)
            return

        # No publication attempt exists. A real durable result is already a
        # safe publication boundary; do not destroy it just because the item
        # is in RECOVERY/FAILED.
        result = item.result_path
        if not result and item.affiliate_url:
            result = self.storage.result(
                item_id,
                item.affiliate_url,
                item.affiliate_name,
                source_id=item.source_id,
            )
        if result and result.is_file():
            if (
                self.speech_studio_runner is not None
                and not self._has_speech_studio_v5_completion(item_id)
            ):
                self._rebuild_processing_from_original(item_id)
                return
            if item.result_path is None:
                self.db.set_result(item_id, result)
            self._resume_durable_result(item_id, "recovery-resume-durable-result")
            return

        # No proven final result. Any working/derived artifacts may be partial,
        # so discard them and rebuild from the immutable ORIGINAL.
        if item.affiliate_name:
            self._rebuild_processing_from_original(item_id)
            return

        # Without resolved product metadata, Vision is the only safe boundary.
        self.db.transition(item_id, State.VISION, "recovery-rebuild-vision-from-original")
        self.pipeline.run(
            item_id,
            stop_after_ia=self.speech_studio_runner is not None,
        )
        if self.speech_studio_runner is not None:
            current = self.db.get(item_id)
            if current.state == State.SPEECH:
                self.speech_studio_runner(item_id)
