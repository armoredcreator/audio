from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from armored_core.models import State
from armored_core.services import IngestMessage
from armored_core.integrity import sha256_file

log = logging.getLogger(__name__)


class ArmoredStock:
    """Independent, one-at-a-time materializer for Vision-approved originals.

    The tool owns only download/materialization. It shares the Coordinator's
    SQLite, Storage, and Telegram Sync adapters; it never runs Vision, Studio,
    IA, Hub, publication, cleanup, or LIVE transitions.
    """

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator

    def _ordered(self, items: list[Any]) -> list[Any]:
        source = self.coordinator.source
        rank = {
            str(candidate.source_id): index
            for index, candidate in enumerate(getattr(source, "sources", ()))
            if getattr(candidate, "source_id", None) is not None
        }
        return sorted(
            items,
            key=lambda item: (
                rank.get(str(item.source_id), len(rank)),
                int(item.telegram_message_id)
                if str(item.telegram_message_id).isdigit()
                else 0,
                str(item.content_id),
            ),
        )

    @staticmethod
    def _sha256(path) -> str:
        return sha256_file(path)

    async def materialize_item(
        self,
        item: Any,
        *,
        fallback_materialize=None,
        connection_already_managed: bool = False,
    ) -> str:
        """Materialize exactly one approved item without running downstream stages.

        The Coordinator calls this method for the single item it has selected
        for production. It never overwrites an existing ORIGINAL and never
        changes Vision's decision or publication state.
        """
        coordinator = self.coordinator
        source = coordinator.source
        if item.state != State.RECEIVED or not str(item.affiliate_url or "").strip():
            raise RuntimeError(
                f"ArmoredStock recusou item sem aprovação Vision: "
                f"{item.content_id} state={item.state.value}"
            )
        # The standalone batch entry point checks for unresolved Vision
        # decisions before starting. The Coordinator-facing single-item method
        # must not let a newly discovered LIVE candidate block an already
        # approved item; it validates only the selected item's durable approval.

        # Repair only a missing path. Never overwrite an immutable ORIGINAL.
        storage_root = (getattr(coordinator.storage, "storage_root", None) or getattr(coordinator.storage, "root")).resolve()
        canonical_original = coordinator.storage.original(
            item.content_id,
            ".mp4",
            original_url=item.original_url,
            source_id=item.source_id,
        ).resolve()
        try:
            canonical_original.relative_to(storage_root)
        except ValueError as exc:
            raise RuntimeError(
                f"canonical-original-outside-storage-root:{item.content_id}:{canonical_original}"
            ) from exc

        if item.original_path != canonical_original:
            old_path = item.original_path
            if not old_path.is_absolute():
                old_path = storage_root / old_path
            old_path = old_path.resolve()
            try:
                old_path.relative_to(storage_root)
            except ValueError as exc:
                raise RuntimeError(
                    f"refusing-to-repair-original-outside-storage-root:{item.content_id}:{old_path}"
                ) from exc
            if old_path.is_file():
                old_digest = self._sha256(old_path)
                expected_digest = str(getattr(item, "original_sha256", "") or "").strip()
                if expected_digest and old_digest != expected_digest:
                    raise RuntimeError(
                        f"original-immutable-hash-mismatch:{item.content_id}"
                    )
                if canonical_original.is_file():
                    if self._sha256(canonical_original) != old_digest:
                        raise RuntimeError(
                            f"canonical-original-path-conflict:{item.content_id}"
                        )
                else:
                    canonical_original.parent.mkdir(parents=True, exist_ok=True)
                    old_path.replace(canonical_original)
            coordinator.db.repair_original_path(item.content_id, canonical_original)
            item = coordinator.db.get(item.content_id)

        if item.original_path.exists():
            if not item.original_path.is_file() or item.original_path.stat().st_size <= 0:
                raise RuntimeError(f"existing-original-is-not-a-nonempty-file:{item.content_id}")
            actual_digest = self._sha256(item.original_path)
            expected_digest = str(getattr(item, "original_sha256", "") or "").strip().lower()
            if expected_digest and actual_digest.lower() != expected_digest:
                raise RuntimeError(f"original-immutable-hash-mismatch:{item.content_id}")
            if not expected_digest:
                finalize = getattr(coordinator.db, "finalize_original_path", None)
                if callable(finalize):
                    finalize(item.content_id, item.original_path, actual_digest)
            return "existing"

        source_materializer = getattr(source, "materialize_candidate_async", None) if source is not None else None
        if not callable(source_materializer) and not callable(fallback_materialize):
            raise RuntimeError(
                "ArmoredStock: fonte não implementa materialização Telegram "
                "e a mensagem não forneceu callback de materialização"
            )

        async def materialize(
            target,
            source_id=item.source_id,
            message_id=item.telegram_message_id,
        ):
            if callable(source_materializer):
                if hasattr(source, "sources"):
                    await source_materializer(source_id, message_id, target)
                else:
                    if str(source.source_id) != str(source_id):
                        raise RuntimeError(f"source-mismatch-for-materialization:{source_id}")
                    await source_materializer(message_id, target)
                return
            # Compatibility for simple/legacy Sync adapters: preserve the
            # message's own materializer instead of requiring a new source API.
            result = fallback_materialize(target)
            if hasattr(result, "__await__"):
                await result

        ingest = IngestMessage(
            telegram_message_id=item.telegram_message_id,
            source_id=item.source_id,
            topic_id=item.topic_id,
            topic_name=item.topic_name,
            original_url=item.original_url,
            materialize=materialize,
        )
        try:
            if not connection_already_managed:
                await coordinator._ensure_source_connection()
            try:
                await coordinator.sync.materialize_message_async(ingest)
            finally:
                if not connection_already_managed:
                    await coordinator._release_source_connection()
            current = coordinator.db.get(item.content_id)
            if not current.original_path.is_file():
                raise FileNotFoundError(
                    f"original ausente após download: {current.original_path}"
                )
            log.info(
                "[ARMOREDSTOCK] Original materializado item=%s source=%s",
                current.content_id,
                current.source_id,
            )
            return "downloaded"
        except Exception as exc:
            marker = getattr(source, "mark_materialization_failed", None)
            if callable(marker):
                marker()
            log.error("[ARMOREDSTOCK] Falha item=%s: %s", item.content_id, exc)
            raise

    async def run(
        self, *, content_ids: tuple[str, ...] | list[str] | None = None
    ) -> dict[str, Any]:
        """Compatibility entry point: materialize eligible originals sequentially."""
        coordinator = self.coordinator
        source = coordinator.source
        if source is None:
            raise RuntimeError("ArmoredStock: Sync source não configurado")
        if len(tuple(getattr(source, "sources", ()))) != 3:
            raise RuntimeError(
                "ArmoredStock exige exatamente três fontes configuradas em credentials/project.env"
            )

        allowed = None if content_ids is None else {str(value) for value in content_ids}
        pending_vision = [
            item for item in coordinator.db.pending_vision_candidates()
            if allowed is None or str(item.content_id) in allowed
        ]
        interrupted_approved = [
            item for item in coordinator.db.vision_approved_interrupted_items()
            if allowed is None or str(item.content_id) in allowed
        ]
        invalid_recovery = [
            item for item in coordinator.db.pre_download_recovery_items()
            if allowed is None or str(item.content_id) in allowed
        ]
        if pending_vision or interrupted_approved or invalid_recovery:
            raise RuntimeError(
                "ArmoredStock bloqueado: Vision ainda tem candidatos sem decisão "
                "ou evidência interrompida. Execute Vision até terminar sem erro; "
                "nenhum download foi iniciado."
            )

        counts: Counter[str] = Counter(
            {str(candidate.source_id): 0 for candidate in source.sources}
        )
        errors: list[str] = []
        downloaded = 0
        skipped_existing_original = 0
        candidates = self._ordered(coordinator.db.pending_vision_approved_items())
        if allowed is not None:
            candidates = [item for item in candidates if str(item.content_id) in allowed]

        for item in candidates:
            if item.state != State.RECEIVED or not str(item.affiliate_url or "").strip():
                continue
            try:
                outcome = await self.materialize_item(item)
                if outcome == "existing":
                    skipped_existing_original += 1
                    continue
                current = coordinator.db.get(item.content_id)
                counts[str(current.source_id)] += 1
                downloaded += 1
            except Exception as exc:
                errors.append(f"stock:{item.content_id}:{exc}")
                # Keep the failed item durable and retryable, but do not let a
                # single unavailable Telegram media block unrelated approved
                # originals. Continue sequentially; never run two downloads at once.
                log.warning(
                    "[ARMOREDSTOCK] Item adiado após falha de download; "
                    "seguindo para o próximo aprovado item=%s",
                    item.content_id,
                )
                continue

        remaining = sum(
            1
            for candidate in coordinator.db.pending_vision_approved_items()
            if (allowed is None or str(candidate.content_id) in allowed)
            and candidate.state == State.RECEIVED
            and str(candidate.affiliate_url or "").strip()
            and not candidate.original_path.is_file()
        )
        return {
            "tool": "ArmoredStock",
            "processed": downloaded,
            "per_source": dict(counts),
            "outcomes": {
                "downloaded": downloaded,
                "skipped_existing_original": skipped_existing_original,
                "approved_missing_original": int(remaining),
            },
            "errors": errors,
        }
