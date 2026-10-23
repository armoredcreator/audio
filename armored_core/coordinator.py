from __future__ import annotations

import asyncio
import os
import signal
import logging
import threading
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .database import Database
from .catch_up_budget import ContinuousOperationBudget, DurableCatchUpBudget
from .models import State
from .pipeline import Pipeline
from .recovery import Recovery
from .services import IngestMessage, SyncService
from .startup_audit import StartupReconciler
from .storage import Storage
from .routing import load_routes


class Coordinator:
    """Single composition root for the isolated ArmoredCreator pipeline."""

    def __init__(self, db, storage, vision, studio, publisher, source=None, ia=None, catch_up_budget=None, continuous_budget=None):
        self.db = db
        self.log = logging.getLogger(__name__)
        self.storage = storage
        self.sync = SyncService(db, storage)
        self.pipeline = Pipeline(db, storage, vision, studio, publisher, ia)
        self.recovery = Recovery(
            db, storage, vision, studio, publisher, ia,
            speech_studio_runner=self._run_recovery_speech_studio,
        )
        self.startup_reconciler = StartupReconciler(db, storage, publisher)
        self.source = source
        self._runtime_lock_held = False
        self._last_catch_up_completed_count = 0
        self.catch_up_budget = catch_up_budget
        self.continuous_budget = continuous_budget
        self._shutdown_requested = False
        self.pipeline.set_shutdown_checker(lambda: self._shutdown_requested)

    def _catch_up_candidate_ids(self) -> list[str]:
        """Stable ordered union of durable rows eligible for this run's stages."""
        candidates = {}
        for item in (
            self.db.pending_vision_candidates()
            + self.db.catch_up_production_items()
            + self.db.pre_download_recovery_items()
            + self.db.vision_approved_interrupted_items()
        ):
            candidates[str(item.content_id)] = item
        failed = self.db.conn.execute(
            "SELECT content_id FROM items WHERE state=? ORDER BY created_at,content_id",
            (State.FAILED.value,),
        ).fetchall()
        for row in failed:
            item_id = str(row["content_id"])
            candidates[item_id] = self.db.get(item_id)
        sources = [
            str(route.source_id)
            for route in getattr(self.source, "sources", ())
            if getattr(route, "source_id", None) is not None
        ]
        rank = {source_id: index for index, source_id in enumerate(sources)}
        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                rank.get(str(item.source_id), len(rank)),
                int(item.telegram_message_id)
                if str(item.telegram_message_id).isdigit() else 0,
                str(item.content_id),
            ),
        )
        return [str(item.content_id) for item in ordered]

    def _prepare_catch_up_budget(self) -> set[str] | None:
        if self.catch_up_budget is None:
            return None
        self.catch_up_budget.claim(self._catch_up_candidate_ids())
        return self.catch_up_budget.assigned_ids()

    @classmethod
    def build(
        cls,
        root: Path | None = None,
        bindings: Any | None = None,
        *,
        acquire_runtime_lock: bool = False,
        staged: bool = False,
        allow_real_publication: bool | None = None,
        catch_up_run_id: str | None = None,
        max_catch_up_items: int | None = None,
        continuous_operation_id: str | None = None,
        max_items_per_day: int | None = None,
        max_items_per_batch: int | None = None,
    ):
        storage = Storage(root)
        # Project-local credential source of truth.
        # Secrets live only in credentials/project.env; runtime modules continue
        # consuming them through os.getenv() and do not know the file location.
        project_credentials = storage.root / "credentials" / "project.env"
        if project_credentials.exists():
            load_dotenv(project_credentials, override=True)

        # .env contains project configuration, not secrets. Keep externally
        # supplied configuration compatible while preventing it from replacing
        # the project credential source above.
        project_config = storage.root / ".env"
        if project_config.exists():
            load_dotenv(project_config, override=False)

        # The command-line production lifetime defaults to a closed Hub gate.
        # Existing programmatic callers retain their environment-controlled
        # behavior unless they pass this explicit policy argument.
        if allow_real_publication is not None:
            os.environ["ARMORED_HUB_DRY_RUN"] = "0" if allow_real_publication else "1"

        # The staged historical runner is exclusively a real Telegram
        # operation; never silently fall back to LocalSource because of an
        # inherited shell variable or a stale project.env setting.
        if staged:
            os.environ["ARMORED_REAL_TELEGRAM"] = "1"
            os.environ["ARMORED_REQUIRED_SOURCE_COUNT"] = "3"
            os.environ["ARMORED_SYNC_VERBOSE_PROGRESS"] = "0"

        # Staged runs defer schema creation/migrations until after the
        # SQLite runtime lease is acquired. Opening a second Coordinator must
        # not write the database before discovering an active owner.
        db = Database(
            storage.database / "armoredcreator.db",
            initialize=not acquire_runtime_lock,
        )
        runtime_lock_acquired = False
        try:
            if acquire_runtime_lock:
                db.acquire_runtime_lock("coordinator")
                runtime_lock_acquired = True
                db.initialize()

            if (catch_up_run_id is None) != (max_catch_up_items is None):
                raise ValueError("catch-up run ID and item limit must be supplied together")
            continuous_args = (continuous_operation_id, max_items_per_day, max_items_per_batch)
            if any(value is not None for value in continuous_args) and any(
                value is None for value in continuous_args
            ):
                raise ValueError("continuous operation ID, daily limit, and batch size are required together")
            if catch_up_run_id is not None and continuous_operation_id is not None:
                raise ValueError("one-shot and continuous budgets are mutually exclusive")

            def make_coordinator(vision, studio, publisher, source, ia=None):
                budget = None
                if catch_up_run_id is not None:
                    budget = DurableCatchUpBudget(
                        storage.database / "catch-up-work-budget.sqlite3",
                        catch_up_run_id,
                        int(max_catch_up_items),
                    )
                continuous_budget = None
                if continuous_operation_id is not None:
                    continuous_budget = ContinuousOperationBudget(
                        storage.database / "continuous-work-budget.sqlite3",
                        continuous_operation_id,
                        int(max_items_per_day),
                        int(max_items_per_batch),
                    )
                coordinator = cls(
                    db, storage, vision, studio, publisher, source, ia, budget,
                    continuous_budget,
                )
                coordinator._runtime_lock_held = runtime_lock_acquired
                return coordinator

            if bindings is None:
                from ArmoredVision.service import ArmoredVision
                from ArmoredSync.service import LocalSource, TelegramReader, TelegramSource, MultiTelegramSource

                vision = ArmoredVision(db=db)
                routes = load_routes()
                if staged and len(routes) != 3:
                    raise RuntimeError(
                        "Execução por etapas exige exatamente 3 fontes Telegram; "
                        f"foram configuradas {len(routes)}."
                    )
                legacy_source_id = (os.getenv("ARMORED_SYNC_SOURCE_ID") or os.getenv("ARMORED_SYNC_SOURCE") or "").strip()
                for route in routes:
                    db.initialize_source_state(route.source.source_id, legacy_source_id=legacy_source_id or None)

                if staged:
                    # The first three catch-up stages require only Sync, Vision and
                    # SQLite. Do not instantiate Studio/RVC, IA or Hub/publishing.
                    ia = None
                    studio = None
                    publisher = None
                else:
                    from ArmoredHub.service import ArmoredHub
                    from ArmoredStudio.service import ArmoredStudio
                    from ArmoredIA.service import ArmoredIA

                    ia = ArmoredIA()
                    studio = ArmoredStudio(storage.root)
                    publisher = ArmoredHub(storage.root, db, routes=routes)

                if os.getenv("ARMORED_REAL_TELEGRAM", "0") == "1":
                    api_id = os.getenv("TELEGRAM_API_ID")
                    api_hash = os.getenv("TELEGRAM_API_HASH")
                    if not api_id or not api_hash:
                        raise RuntimeError("TELEGRAM_API_ID e TELEGRAM_API_HASH são obrigatórios")
                    reader = TelegramReader(storage.root, int(api_id), api_hash)
                    source = (
                        MultiTelegramSource(storage.root, reader, db, routes)
                        if len(routes) > 1
                        else TelegramSource(
                            storage.root,
                            reader,
                            db,
                            source=(routes[0].source.chat_id if routes else None),
                            source_id=(routes[0].source.source_id if routes else None),
                        )
                    )
                else:
                    source = LocalSource(storage.root / "input")
                return make_coordinator(vision, studio, publisher, source, ia)
            return make_coordinator(
                bindings.vision,
                bindings.studio,
                bindings.publisher,
                bindings.source,
                getattr(bindings, "ia", None),
            )

        except BaseException:
            # If composition fails after acquiring the lease, do not strand
            # the current PID as owner of SQLite and block the next safe retry.
            if runtime_lock_acquired:
                try:
                    db.release_runtime_lock("coordinator")
                except Exception:
                    pass
            db.close()
            raise

    async def _ensure_source_connection(self) -> None:
        """Reconnect a real Telegram source before materializing the next item."""
        source = self.source
        reader = getattr(source, "reader", None)
        connect = getattr(reader, "connect", None)
        if connect is not None:
            is_connected = getattr(reader, "is_connected", None)
            if callable(is_connected):
                if is_connected():
                    return
            else:
                client = getattr(reader, "client", None)
                client_is_connected = getattr(client, "is_connected", None)
                if callable(client_is_connected) and client_is_connected():
                    return
            await connect()

    async def _release_source_connection(self) -> None:
        """Release a real Telegram Sync session before Hub opens the same session.

        Telethon stores the user session in SQLite. Keeping ArmoredSync connected
        while ArmoredHub opens that same session causes a Windows SQLite lock.
        Callers release it after discovery/materialization and pause live polling
        before the Hub opens its own connection.
        """
        source = self.source
        reader = getattr(source, "reader", None)
        disconnect = getattr(reader, "disconnect", None)
        if disconnect is not None:
            await disconnect()
            return
        # Non-Telegram lab sources may expose a convenience disconnect()
        # method without owning a real Sync session. Do not call it blindly:
        # the Coordinator must release only the connection it actually manages.
        # Real Telegram ownership is represented by source.reader above.

    async def _materialize_approved_item_async(
        self,
        item,
        *,
        fallback_materialize=None,
        connection_already_managed: bool = False,
    ) -> str:
        """Route every approved ORIGINAL download through ArmoredStock.

        Coordinator remains the composition root and chooses one active item;
        ArmoredStock owns only the download/materialization operation. A
        message-provided callback remains supported for legacy/test adapters.
        """
        from ArmoredStock.service import ArmoredStock

        return await ArmoredStock(self).materialize_item(
            item,
            fallback_materialize=fallback_materialize,
            connection_already_managed=connection_already_managed,
        )

    async def ingest_once_async(self):
        if self.source is None:
            raise RuntimeError("Sync source não configurado")
        fetch_async = getattr(self.source, "fetch_next_async", None)
        message = await fetch_async() if fetch_async is not None else self.source.fetch_next()
        if message is None:
            await self._release_source_connection()
            return None
        item_id = await self.sync.ingest_message_async(IngestMessage(
            telegram_message_id=str(message.telegram_message_id),
            source_id=getattr(message, "source_id", "telegram"),
            topic_id=getattr(message, "topic_id", None),
            topic_name=getattr(message, "topic_name", None),
            original_url=getattr(message, "original_url", None),
            source_path=getattr(message, "source_path", None),
            materialize=getattr(message, "materialize", None),
        ))
        marker = getattr(self.source, "mark_ingested", None)
        if marker is not None:
            marker(str(message.telegram_message_id))
        await self._release_source_connection()
        return item_id

    def ingest_once(self):
        if self.source is None:
            raise RuntimeError("Sync source não configurado")
        if getattr(self.source, "fetch_next_async", None) is not None:
            import asyncio
            return asyncio.run(self.ingest_once_async())
        message = self.source.fetch_next()
        if message is None:
            return None
        item_id = self.sync.ingest_message(IngestMessage(
            telegram_message_id=str(message.telegram_message_id),
            source_id=getattr(message, "source_id", "telegram"),
            topic_id=getattr(message, "topic_id", None),
            topic_name=getattr(message, "topic_name", None),
            original_url=getattr(message, "original_url", None),
            source_path=getattr(message, "source_path", None),
            materialize=getattr(message, "materialize", None),
        ))
        marker = getattr(self.source, "mark_ingested", None)
        if marker is not None:
            marker(str(message.telegram_message_id))
        reader = getattr(self.source, "reader", None)
        disconnect = getattr(reader, "disconnect", None)
        if disconnect is not None:
            import asyncio
            asyncio.run(disconnect())
        return item_id

    async def _vision_gate_and_materialize_async(self, message):
        """Reserve candidate, run Vision, then materialize only when accepted."""
        ingest = IngestMessage(
            telegram_message_id=str(message.telegram_message_id),
            source_id=getattr(message, "source_id", "telegram"),
            topic_id=getattr(message, "topic_id", None),
            topic_name=getattr(message, "topic_name", None),
            original_url=getattr(message, "original_url", None),
            source_path=getattr(message, "source_path", None),
            materialize=getattr(message, "materialize", None),
        )
        # Test doubles/subclasses that override Coordinator.run() are part of
        # the original lifecycle contract: let them receive a normally materialized
        # candidate instead of forcing the new Vision gate through their override.
        if getattr(self.run, "__func__", None) is not Coordinator.run:
            await self._ensure_source_connection()
            try:
                item_id = str(await self.sync.ingest_message_async(ingest))
                return item_id, True, self.db.get(item_id)
            finally:
                await self._release_source_connection()

        item_id = str(self.sync.reserve_message(ingest))
        # Legacy and local adapters may provide a source file instead of a
        # Telegram source materializer or callback. Preserve that contract by
        # adapting the source_path to the same selected-item callback.
        fallback_materialize = ingest.materialize
        if fallback_materialize is None and ingest.source_path is not None:
            source_path = Path(ingest.source_path)

            def materialize_from_source_path(target, source_path=source_path):
                import shutil
                shutil.copy2(source_path, target)

            fallback_materialize = materialize_from_source_path

        try:
            # Match the original Coordinator lifecycle: Sync owns the source
            # connection while the single candidate is reserved and materialized.
            # Vision itself is URL-only, so no media is downloaded here.
            await self._ensure_source_connection()
            current = self.db.get(item_id)

            # A previous Vision-approved candidate may have failed during
            # materialization. It remains RECEIVED with durable affiliate_url
            # but without ORIGINAL. On rediscovery, materialize first; never let
            # Pipeline.run() jump directly to Studio against a missing ORIGINAL.
            if current.affiliate_url and not current.original_path.is_file():
                await self._materialize_approved_item_async(
                    current,
                    fallback_materialize=fallback_materialize,
                    connection_already_managed=True,
                )
                return item_id, True, self.db.get(item_id)

            if current.state != State.FAILED:
                self.pipeline.run(item_id, stop_after_vision=True)
            current = self.db.get(item_id)
            if current.state == State.WAITING_VISION:
                return item_id, False, current

            # stop_after_vision returns the accepted candidate to RECEIVED with
            # durable Vision evidence. Materialize only now, after the gate.
            if not current.affiliate_url:
                return item_id, False, current
            if not current.original_path.is_file():
                await self._materialize_approved_item_async(
                    current,
                    fallback_materialize=fallback_materialize,
                    connection_already_managed=True,
                )
                return item_id, True, self.db.get(item_id)
            return item_id, True, current
        except Exception:
            # Before an immutable ORIGINAL exists, a technical Vision failure
            # remains retryable in RECEIVED/VISION and blocks checkpoint progress.
            # After Vision approval, a download failure leaves the approved
            # RECEIVED row durable so the explicit Stock stage can retry it.
            # Never create RECOVERY merely to make a pre-download error recoverable.
            raise
        finally:
            # Sync must be released before Studio/Hub can use the same session.
            await self._release_source_connection()

    def _run_pipeline_with_private_database(
        self,
        item_id: str,
        *,
        stop_after_vision: bool = False,
        before_hub=None,
    ) -> None:
        """Run synchronous pipeline work on a thread-owned SQLite connection."""
        from copy import copy

        db = Database(self.db.path, initialize=False)
        try:
            # Vision's persistent negative-offer cache must use this worker's
            # connection too, never the main-thread SQLite object.
            vision = copy(self.pipeline.vision)
            if hasattr(vision, "db"):
                vision.db = db
            publisher = None
            studio = None
            ia = None
            if not stop_after_vision:
                publisher = copy(self.pipeline.publisher)
                if hasattr(publisher, "db"):
                    publisher.db = db
                studio = self.pipeline.studio
                ia = self.pipeline.ia
            if stop_after_vision:
                pipeline = Pipeline(
                    db,
                    self.storage,
                    vision,
                    studio,
                    publisher,
                    ia,
                )
                pipeline.set_shutdown_checker(lambda: self._shutdown_requested)
                pipeline.run(item_id, stop_after_vision=True)
            else:
                private_coordinator = Coordinator(
                    db,
                    self.storage,
                    vision,
                    studio,
                    publisher,
                    source=None,
                    ia=ia,
                )
                private_coordinator._shutdown_requested = self._shutdown_requested
                if before_hub is not None:
                    private_coordinator.pipeline.set_before_hub_checker(before_hub)
                private_coordinator.run(item_id)
        finally:
            db.close()

    async def _vision_only_async(self, message, *, offload: bool = False):
        """Reserve and classify a candidate without materializing its media."""
        ingest = IngestMessage(
            telegram_message_id=str(message.telegram_message_id),
            source_id=str(getattr(message, "source_id", "telegram") or "telegram"),
            topic_id=getattr(message, "topic_id", None),
            topic_name=getattr(message, "topic_name", None),
            original_url=getattr(message, "original_url", None),
            source_path=getattr(message, "source_path", None),
            materialize=getattr(message, "materialize", None),
        )
        item_id = str(self.sync.reserve_message(ingest))
        current = self.db.get(item_id)
        if current.state in {State.PUBLISHED, State.WAITING_VISION}:
            return item_id, current
        # A prior run may already have durably approved this candidate. Do not
        # call Vision twice merely because its ORIGINAL still needs materializing.
        if current.affiliate_url:
            return item_id, current
        if current.state == State.FAILED:
            raise RuntimeError("FAILED item requires deterministic recovery before Vision")
        if offload:
            await asyncio.to_thread(
                self._run_pipeline_with_private_database,
                item_id,
                stop_after_vision=True,
            )
        else:
            self.pipeline.run(item_id, stop_after_vision=True)
        return item_id, self.db.get(item_id)

    async def _poll_live_during_catch_up(
        self,
        source,
        stop_event: asyncio.Event,
        source_io_lock: asyncio.Lock,
        poll_seconds: float,
        hub_pause_requested: threading.Event,
        hub_paused: threading.Event,
    ) -> None:
        fetch = source.fetch_live_candidate_async
        commit = source.commit_live_checkpoints
        while not stop_event.is_set():
            if hub_pause_requested.is_set():
                await self._release_source_connection()
                hub_paused.set()
                try:
                    while hub_pause_requested.is_set() and not stop_event.is_set():
                        await asyncio.sleep(0.05)
                finally:
                    hub_paused.clear()
                continue
            try:
                async with source_io_lock:
                    try:
                        message, checkpoints = await fetch()
                    finally:
                        await self._release_source_connection()
                if message is None:
                    if checkpoints:
                        commit(checkpoints)
                else:
                    item_id, item = await self._vision_only_async(
                        message,
                        offload=True,
                    )
                    if item.state == State.WAITING_VISION or item.affiliate_url:
                        if checkpoints:
                            commit(checkpoints)
                        self.log.info(
                            "[COORDINATOR][CATCH-UP][LIVE-DISCOVERY] "
                            "durable candidate=%s state=%s",
                            item_id,
                            item.state.value,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log.exception(
                    "[COORDINATOR][CATCH-UP][LIVE-DISCOVERY] "
                    "candidate remains retryable; checkpoint not advanced: %s",
                    exc,
                )

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def _run_staged_catch_up_async(self) -> list[str]:
        """Discover and classify the batch before stocking and producing it.

        SQLite is the restart boundary. Discovery reserves metadata only; Vision
        decisions are persisted before any download. ArmoredStock materializes
        every approved original before Studio starts. New messages continue to
        be discovered and Vision-checked during production; they are materialized
        together as the next batch before any of that batch reaches Studio.
        """
        source = self.source
        processed: list[str] = []
        checkpoint_blocked = False
        allowed_ids = self._prepare_catch_up_budget()

        # Repair interrupted pre-download state before Vision retries.
        for item in self.db.pre_download_recovery_items():
            if allowed_ids is not None and str(item.content_id) not in allowed_ids:
                continue
            target = (
                State.RECEIVED
                if str(item.affiliate_url or "").strip()
                else State.VISION
            )
            self.db.transition(
                item.content_id,
                target,
                "catch-up-repaired-recovery-without-original",
            )
        for item in self.db.vision_approved_interrupted_items():
            if allowed_ids is not None and str(item.content_id) not in allowed_ids:
                continue
            self.db.transition(
                item.content_id,
                State.RECEIVED,
                "catch-up-recovered-vision-evidence",
            )

        # Stage 1: discover/reserve candidates from all configured sources.
        # Do not download media while any source is still scanning.
        remaining = self.catch_up_budget.remaining if self.catch_up_budget else None
        if remaining is not None and remaining > 0:
            limit_setter = getattr(source, "set_run_candidate_limit", None)
            if callable(limit_setter):
                limit_setter(remaining)
        iterator = (
            source.iter_historical_candidates_async()
            if remaining is None or remaining > 0
            else None
        )
        try:
            if iterator is not None:
                async for message in iterator:
                    source_id = str(getattr(message, "source_id", "telegram") or "telegram")
                    item_id = str(self.db.content_id_for(
                        str(message.telegram_message_id), source_id
                    ))
                    try:
                        ingest = IngestMessage(
                            telegram_message_id=str(message.telegram_message_id),
                            source_id=source_id,
                            topic_id=getattr(message, "topic_id", None),
                            topic_name=getattr(message, "topic_name", None),
                            original_url=getattr(message, "original_url", None),
                            source_path=getattr(message, "source_path", None),
                            materialize=getattr(message, "materialize", None),
                        )
                        item_id = str(self.sync.reserve_message(ingest))
                        if self.catch_up_budget is not None:
                            newly_claimed = self.catch_up_budget.claim((item_id,))
                            if item_id not in newly_claimed:
                                raise RuntimeError("catch-up-budget-reservation-exceeded")
                            allowed_ids.add(item_id)
                        marker = getattr(source, "mark_ingested", None)
                        if marker is not None:
                            marker(str(message.telegram_message_id))
                        processed.append(item_id)
                    except Exception as exc:
                        checkpoint_blocked = True
                        marker = getattr(source, "mark_materialization_failed", None)
                        if marker is not None:
                            marker()
                        self.log.exception(
                            "[COORDINATOR][CATCH-UP][RESERVE] %s failed; "
                            "historical checkpoint remains blocked: %s",
                            item_id, exc,
                        )
                        break
        finally:
            await self._release_source_connection()

        # Stage 2: classify all durable candidates that still need Vision.
        # This includes VISION rows left by an interrupted run. WAITING_VISION
        # rows remain durable and are retried only by the explicit retry policy.
        vision_candidates = self.db.pending_vision_candidates()
        if allowed_ids is not None:
            vision_candidates = [
                item for item in vision_candidates
                if str(item.content_id) in allowed_ids
            ]
        for item in vision_candidates:
            try:
                self.pipeline.run(item.content_id, stop_after_vision=True)
                current = self.db.get(item.content_id)
                if current.state == State.RECOVERY:
                    checkpoint_blocked = True
            except Exception as exc:
                checkpoint_blocked = True
                self.log.exception(
                    "[COORDINATOR][CATCH-UP][VISION] %s failed; no media downloaded: %s",
                    item.content_id, exc,
                )
            if item.content_id not in processed:
                processed.append(item.content_id)

        # WAITING_VISION is a durable business outcome. Unresolved rows still in
        # RECEIVED/VISION are technical Vision failures and must block progress.
        vision_failures = [
            item
            for item in self.db.pending_vision_candidates()
            if item.state != State.WAITING_VISION
            and (allowed_ids is None or str(item.content_id) in allowed_ids)
        ]
        if vision_failures:
            checkpoint_blocked = True
            for item in vision_failures:
                self.log.error(
                    "[COORDINATOR][CATCH-UP][VISION] item=%s remains retryable; "
                    "historical cutover is blocked",
                    item.content_id,
                )

        failed = checkpoint_blocked or bool(
            getattr(source, "historical_materialization_failed", False)
        )
        if failed:
            self._last_catch_up_completed_count = 0
            return processed

        # Stage 3: fill ArmoredStock with every currently approved original
        # before Studio is allowed to begin. This is a durable batch barrier:
        # a download error holds the entire batch and prevents partial
        # publication from this catch-up run.
        from ArmoredStock.service import ArmoredStock

        stock = ArmoredStock(self)
        stock_report = (
            await stock.run(content_ids=tuple(allowed_ids))
            if allowed_ids is not None
            else await stock.run()
        )
        stock_errors = list(stock_report.get("errors", []))
        stock_remaining = int(
            stock_report.get("outcomes", {}).get("approved_missing_original", 0)
        )
        if stock_errors or stock_remaining:
            checkpoint_blocked = True
            self._last_catch_up_completed_count = 0
            self.log.error(
                "[COORDINATOR][CATCH-UP][STOCK] batch incompleto; "
                "Studio bloqueado. baixados=%s restantes=%s erros=%s",
                stock_report.get("processed", 0),
                stock_remaining,
                stock_errors,
            )
            return processed
        self.log.info(
            "[COORDINATOR][CATCH-UP][STOCK] lote pronto antes do Studio: "
            "baixados=%s existentes=%s",
            stock_report.get("processed", 0),
            stock_report.get("outcomes", {}).get("skipped_existing_original", 0),
        )

        live_fetch = getattr(source, "fetch_live_candidate_async", None)
        live_commit = getattr(source, "commit_live_checkpoints", None)
        commit_discovered = getattr(
            source,
            "commit_historical_discovery_checkpoints",
            None,
        )
        live_task = None
        live_stop = None
        source_io_lock = asyncio.Lock()
        hub_pause_requested = threading.Event()
        hub_paused = threading.Event()
        if (
            self.catch_up_budget is None
            and
            callable(live_fetch)
            and callable(live_commit)
            and callable(commit_discovered)
            and bool(getattr(source, "historical_scan_exhausted", False))
        ):
            # All discovered historical candidates and their Vision outcomes
            # are durable before moving the reader's cursor to live discovery.
            commit_discovered()
            live_stop = asyncio.Event()
            try:
                poll_seconds = max(
                    0.25,
                    float(
                        os.getenv(
                            "ARMORED_CATCHUP_LIVE_POLL_SECONDS",
                            "2",
                        )
                    ),
                )
            except ValueError:
                poll_seconds = 2.0
            live_task = asyncio.create_task(
                self._poll_live_during_catch_up(
                    source,
                    live_stop,
                    source_io_lock,
                    poll_seconds,
                    hub_pause_requested,
                    hub_paused,
                )
            )

        # Production consumes only stock-backed items. If live discovery adds
        # more approved candidates while this batch is being edited, stop that
        # discovery pass, bulk-materialize the newly approved rows, then let
        # Studio consume the next complete batch.
        configured_sources = [
            str(candidate.source_id)
            for candidate in getattr(source, "sources", ())
            if getattr(candidate, "source_id", None) is not None
        ]
        source_rank = {source_id: index for index, source_id in enumerate(configured_sources)}
        production_seen: set[str] = set()
        while not checkpoint_blocked:
            production_items = self.db.catch_up_production_items()
            if allowed_ids is not None:
                production_items = [
                    item for item in production_items
                    if str(item.content_id) in allowed_ids
                ]
            if not configured_sources:
                configured_sources = sorted(
                    {item.source_id for item in production_items}
                )
                source_rank = {
                    source_id: index for index, source_id in enumerate(configured_sources)
                }
            production_items.sort(key=lambda item: (
                source_rank.get(str(item.source_id), len(source_rank)),
                int(item.telegram_message_id)
                if str(item.telegram_message_id).isdigit()
                else 0,
                item.content_id,
            ))
            available_items = [
                candidate
                for candidate in production_items
                if candidate.content_id not in production_seen
                and candidate.original_path.is_file()
            ]

            # Batch barrier for new work: complete IA for every eligible item,
            # then prepare every Speech v5 package, then render the full Studio
            # batch, and only after all results are validated may Hub begin.
            # Earlier in-flight/recovery states are resolved first, in order.
            phase_batch = []
            for candidate in available_items:
                if candidate.state in {State.RECEIVED, State.IA, State.SPEECH}:
                    phase_batch.append(candidate)
                else:
                    break

            if phase_batch:
                batch_ids = [str(candidate.content_id) for candidate in phase_batch]
                production_seen.update(batch_ids)
                speech_ids = []
                ia_failed = False

                for candidate in phase_batch:
                    try:
                        current = self.db.get(candidate.content_id)
                        if current.state in {State.RECEIVED, State.IA}:
                            self.pipeline.run(current.content_id, stop_after_ia=True)
                        current = self.db.get(candidate.content_id)
                        if current.state != State.SPEECH:
                            raise RuntimeError(
                                "ia-phase-did-not-reach-speech:"
                                f"{current.content_id}:{current.state.value}"
                            )
                        speech_ids.append(str(current.content_id))
                    except Exception as exc:
                        ia_failed = True
                        self.log.exception(
                            "[COORDINATOR][CATCH-UP][IA] %s failed; "
                            "Speech/Studio/Hub are held for this batch: %s",
                            candidate.content_id,
                            exc,
                        )

                for batch_item_id in batch_ids:
                    if batch_item_id not in processed:
                        processed.append(batch_item_id)

                if ia_failed or len(speech_ids) != len(batch_ids):
                    checkpoint_blocked = True
                    self._last_catch_up_completed_count = 0
                    break

                try:
                    self._run_speech_studio_batch(speech_ids)
                except Exception as exc:
                    checkpoint_blocked = True
                    self.log.exception(
                        "[COORDINATOR][CATCH-UP][SPEECH/STUDIO] batch=%s failed; "
                        "Hub is held: %s",
                        speech_ids,
                        exc,
                    )
                    break

                # Every item in this batch is now PUBLISHING with an atomic,
                # durable "speech-studio-v5-complete" event. Only now enter Hub.
                for batch_item_id in speech_ids:
                    try:
                        if live_task is not None:
                            def pause_source_for_hub():
                                hub_pause_requested.set()
                                try:
                                    timeout = max(
                                        5.0,
                                        float(
                                            os.getenv(
                                                "ARMORED_CATCHUP_HUB_PAUSE_TIMEOUT",
                                                "30",
                                            )
                                        ),
                                    )
                                except ValueError:
                                    timeout = 30.0
                                if not hub_paused.wait(timeout):
                                    hub_pause_requested.clear()
                                    raise TimeoutError(
                                        "live Telegram discovery did not release its "
                                        "session before Hub publication"
                                    )

                            await asyncio.to_thread(
                                self._run_pipeline_with_private_database,
                                batch_item_id,
                                before_hub=pause_source_for_hub,
                            )
                        else:
                            self.run(batch_item_id)

                        current = self.db.get(batch_item_id)
                        if (
                            current.state != State.PUBLISHED
                            or not current.cleanup_completed
                            or current.result_path is None
                            or not current.result_path.is_file()
                        ):
                            raise RuntimeError(
                                "hub-did-not-reach-durable-published-state:"
                                f"{batch_item_id}:{current.state.value}"
                            )
                    except Exception as exc:
                        checkpoint_blocked = True
                        self.log.exception(
                            "[COORDINATOR][CATCH-UP][HUB] %s failed after the "
                            "complete validated Studio batch; later publication is held: %s",
                            batch_item_id,
                            exc,
                        )
                        break
                    finally:
                        hub_pause_requested.clear()

                if checkpoint_blocked:
                    break
                continue

            item = next(
                (
                    candidate
                    for candidate in production_items
                    if candidate.content_id not in production_seen
                    and candidate.original_path.is_file()
                ),
                None,
            )
            if item is None:
                if live_task is not None and live_stop is not None:
                    live_stop.set()
                    await live_task
                    live_task = None
                    live_stop = None
                    # A poll that was already classifying a candidate may have
                    # committed it while shutdown was requested. It must reach
                    # a durable Vision outcome before Stock begins.
                    vision_failures = [
                        pending
                        for pending in self.db.pending_vision_candidates()
                        if pending.state != State.WAITING_VISION
                    ]
                    if vision_failures:
                        checkpoint_blocked = True
                        for pending in vision_failures:
                            self.log.error(
                                "[COORDINATOR][CATCH-UP][VISION] item=%s remains "
                                "retryable; historical cutover is blocked",
                                pending.content_id,
                            )
                        break

                # Drain any approvals discovered during the previous production
                # batch as one Stock batch. Studio will not start for these
                # candidates until all of their originals exist.
                stock = ArmoredStock(self)
                stock_report = (
                    await stock.run(content_ids=tuple(allowed_ids))
                    if allowed_ids is not None
                    else await stock.run()
                )
                stock_errors = list(stock_report.get("errors", []))
                stock_remaining = int(
                    stock_report.get("outcomes", {}).get(
                        "approved_missing_original", 0
                    )
                )
                if stock_errors or stock_remaining:
                    checkpoint_blocked = True
                    self.log.error(
                        "[COORDINATOR][CATCH-UP][STOCK] next batch incomplete; "
                        "Studio held. downloaded=%s remaining=%s errors=%s",
                        stock_report.get("processed", 0),
                        stock_remaining,
                        stock_errors,
                    )
                    break
                ready_unseen = [
                    candidate
                    for candidate in self.db.catch_up_production_items()
                    if candidate.content_id not in production_seen
                    and (allowed_ids is None or str(candidate.content_id) in allowed_ids)
                    and candidate.original_path.is_file()
                ]
                if ready_unseen:
                    self.log.info(
                        "[COORDINATOR][CATCH-UP][STOCK] próximo lote pronto: %s item(ns)",
                        len(ready_unseen),
                    )
                    continue
                break
            production_seen.add(item.content_id)

            current = self.db.get(item.content_id)
            if current.state == State.WAITING_VISION:
                continue
            if current.state == State.RECEIVED and not current.affiliate_url:
                continue
            if not current.original_path.is_file():
                if (
                    current.state != State.RECEIVED
                    or not str(current.affiliate_url or "").strip()
                ):
                    checkpoint_blocked = True
                    self.log.error(
                        "[COORDINATOR][CATCH-UP][DOWNLOAD] item=%s has no "
                        "recoverable approved original; historical cutover blocked",
                        item.content_id,
                    )
                    break
                if not callable(
                    getattr(source, "materialize_candidate_async", None)
                ):
                    checkpoint_blocked = True
                    self.log.error(
                        "[COORDINATOR][CATCH-UP][DOWNLOAD] source cannot "
                        "materialize item=%s",
                        item.content_id,
                    )
                    break

                try:
                    async with source_io_lock:
                        await self._materialize_approved_item_async(current)
                except Exception as exc:
                    checkpoint_blocked = True
                    marker = getattr(source, "mark_materialization_failed", None)
                    if callable(marker):
                        marker()
                    self.log.exception(
                        "[COORDINATOR][CATCH-UP][DOWNLOAD] %s failed; "
                        "later production is held: %s",
                        current.content_id,
                        exc,
                    )
                    break

            try:
                if current.state == State.PUBLISHED and current.cleanup_completed:
                    pass
                elif live_task is not None:
                    def pause_source_for_hub():
                        hub_pause_requested.set()
                        try:
                            timeout = max(
                                5.0,
                                float(
                                    os.getenv(
                                        "ARMORED_CATCHUP_HUB_PAUSE_TIMEOUT",
                                        "30",
                                    )
                                ),
                            )
                        except ValueError:
                            timeout = 30.0
                        if not hub_paused.wait(timeout):
                            hub_pause_requested.clear()
                            raise TimeoutError(
                                "live Telegram discovery did not release its "
                                "session before Hub publication"
                            )

                    await asyncio.to_thread(
                        self._run_pipeline_with_private_database,
                        item.content_id,
                        before_hub=pause_source_for_hub,
                    )
                else:
                    self.run(item.content_id)
                current = self.db.get(item.content_id)
                if current.state == State.RECOVERY or (
                    current.state != State.WAITING_VISION
                    and (
                        current.state != State.PUBLISHED
                        or (
                            current.result_path is None
                            or not current.result_path.is_file()
                        )
                        or not current.cleanup_completed
                    )
                ):
                    checkpoint_blocked = True
                    self.log.error(
                        "[COORDINATOR][CATCH-UP][PRODUCTION] item=%s did not "
                        "reach a durable terminal state; later items are held",
                        item.content_id,
                    )
                    break
            except Exception as exc:
                checkpoint_blocked = True
                self.log.exception(
                    "[COORDINATOR][CATCH-UP][PRODUCTION] %s failed; "
                    "later source production is held: %s",
                    item.content_id, exc,
                )
                break
            finally:
                hub_pause_requested.clear()
            if item.content_id not in processed:
                processed.append(item.content_id)

        if live_task is not None and live_stop is not None:
            live_stop.set()
            await live_task
            pending_vision = [
                item
                for item in self.db.pending_vision_candidates()
                if item.state != State.WAITING_VISION
            ]
            if pending_vision:
                checkpoint_blocked = True
                for item in pending_vision:
                    self.log.error(
                        "[COORDINATOR][CATCH-UP][VISION] item=%s remains "
                        "retryable; historical cutover is blocked",
                        item.content_id,
                    )

        failed = checkpoint_blocked or bool(
            getattr(source, "historical_materialization_failed", False)
        )
        scan_complete = bool(getattr(source, "historical_scan_exhausted", False))
        if scan_complete and not failed:
            complete = getattr(source, "complete_historical_sync", None)
            if complete is not None:
                complete()
            else:
                self.db.complete_historical_sync()

        self._last_catch_up_completed_count = sum(
            1 for item_id in set(processed)
            if self.db.get(item_id).state == State.PUBLISHED
            and self.db.get(item_id).cleanup_completed
        )
        return processed

    async def run_catch_up_async(self) -> list[str]:
        """Classify history before serial production while continuing live intake.

        Telegram history is streamed; SQLite stores the durable candidate state.
        New messages are discovered during production, media is never
        batch-downloaded, and the canonical pipeline processes only one
        materialized item at a time.
        """
        source = self.source
        if (
            callable(getattr(source, "iter_historical_candidates_async", None))
            and callable(getattr(source, "materialize_candidate_async", None))
        ):
            return await self._run_staged_catch_up_async()

        processed: list[str] = []
        completed_count = 0
        checkpoint_blocked = False
        bounded_limit = getattr(source, "_historical_limit", None)
        fetch_next = getattr(source, "fetch_next_async", None)
        if fetch_next is None:
            raise RuntimeError("Sync source não implementa fetch_next_async")

        while True:
            message = await fetch_next()
            if message is None:
                failed = checkpoint_blocked or bool(
                    getattr(source, "historical_materialization_failed", False)
                )
                scan_complete = bool(
                    getattr(source, "historical_scan_exhausted", True)
                )
                if scan_complete and not failed:
                    complete = getattr(source, "complete_historical_sync", None)
                    if complete is not None:
                        complete()
                    else:
                        self.db.complete_historical_sync()
                break

            # Resolve the durable, source-scoped SQLite key before any operation
            # that can fail. Telegram message IDs are only unique inside one
            # source; using the raw ID here makes the error path lose the row for
            # multi-source items (and can hide a persisted RECOVERY state).
            source_id = str(getattr(message, "source_id", "telegram") or "telegram")
            item_id = str(self.db.content_id_for(str(message.telegram_message_id), source_id))

            # A historical candidate can be rediscovered when the persisted
            # checkpoint is still behind it (for example after an interrupted
            # certification run). A previously completed item must never be
            # counted as part of the current bounded certification.
            try:
                existing = self.db.get(item_id)
            except KeyError:
                existing = None
            if (
                existing is not None
                and existing.state == State.PUBLISHED
                and existing.cleanup_completed
            ):
                marker = getattr(source, "mark_ingested", None)
                if marker is not None:
                    marker(str(message.telegram_message_id))

                # A previously completed item may be rediscovered after an
                # earlier candidate was recovered. Its checkpoint is safe to
                # advance now because this item is durably PUBLISHED+cleanup.
                topic_id = getattr(message, "topic_id", None)
                commit = getattr(source, "commit_live_checkpoints", None)
                if topic_id is not None and commit is not None and not checkpoint_blocked:
                    commit({int(topic_id): int(message.telegram_message_id)})
                continue

            materialized = False
            current = None
            try:
                item_id, materialized, current = await self._vision_gate_and_materialize_async(message)
                marker = getattr(source, "mark_ingested", None)
                if marker is not None:
                    marker(str(message.telegram_message_id))
            except Exception as exc:
                checkpoint_blocked = True
                marker = getattr(source, "mark_materialization_failed", None)
                if marker is not None:
                    marker()
                import logging
                logging.getLogger(__name__).exception(
                    "[COORDINATOR][CATCH-UP] Vision/materialization failed for %s; checkpoint blocked: %s",
                    getattr(message, "telegram_message_id", "?"), exc,
                )
                # The gate may fail after reserving the candidate (for example
                # during Vision or materialization). Re-read durable state so
                # the lifecycle handler never dereferences a missing local value.
                try:
                    current = self.db.get(item_id)
                except Exception:
                    current = None

            if not materialized:
                if current is not None and current.state == State.WAITING_VISION:
                    topic_id = getattr(message, "topic_id", None)
                    commit = getattr(source, "commit_live_checkpoints", None)
                    if topic_id is not None and commit is not None and not checkpoint_blocked:
                        commit({int(topic_id): int(message.telegram_message_id)})
                    processed.append(item_id)
                elif current is not None and current.state == State.RECOVERY:
                    # Vision failed technically before the ORIGINAL existed.
                    # Keep the source-scoped row visible to the caller and keep
                    # the historical checkpoint blocked for a later retry.
                    checkpoint_blocked = True
                    processed.append(item_id)
                continue

            # Exactly one item crosses the Sync -> Pipeline boundary.
            try:
                if self.db.get(item_id).state != State.FAILED:
                    self.run(item_id)
                    current = self.db.get(item_id)

                    # RECOVERY is a technical/incomplete-processing state:
                    # it keeps the historical checkpoint blocked until the item
                    # is resolved. WAITING_VISION is different: Vision has
                    # durably classified the candidate but found no Shopee
                    # destination. It is retained in SQLite and does not block
                    # historical progress or LIVE.
                    if current.state == State.RECOVERY:
                        checkpoint_blocked = True
                        processed.append(item_id)
                        continue

                    if current.state == State.WAITING_VISION:
                        topic_id = getattr(message, "topic_id", None)
                        commit = getattr(source, "commit_live_checkpoints", None)
                        if topic_id is not None and commit is not None and not checkpoint_blocked:
                            commit({int(topic_id): int(message.telegram_message_id)})
                        processed.append(item_id)
                        continue

                    if (
                        current.state == State.PUBLISHED
                        and current.cleanup_completed
                        and not checkpoint_blocked
                    ):
                        # Checkpoints are monotonic and must never jump past
                        # an earlier candidate whose materialization or
                        # processing failed. WAITING_VISION is not such a
                        # blocker: it is a durable "no destination for now"
                        # classification and may advance its topic checkpoint.
                        topic_id = getattr(message, "topic_id", None)
                        commit = getattr(source, "commit_live_checkpoints", None)
                        if topic_id is not None and commit is not None:
                            commit({int(topic_id): int(message.telegram_message_id)})
            except Exception as exc:
                checkpoint_blocked = True
                import logging
                logging.getLogger(__name__).exception(
                    "[COORDINATOR][CATCH-UP] Falha no processamento de %s; "
                    "checkpoint permanece no último item confirmado: %s",
                    item_id,
                    exc,
                )

                # Pipeline.run() persists recoverable failures as RECOVERY and
                # then re-raises. Keep the failed candidate durable, but continue
                # the historical scan. Its checkpoint remains blocked until a
                # later recovery pass resolves the technical failure.
                current = self.db.get(item_id)
                if current.state == State.RECOVERY:
                    # Technical pipeline failures are durable RECOVERY items,
                    # but they must not abort the historical scan. The current
                    # checkpoint remains blocked; later candidates may be
                    # processed one at a time. A later recovery pass revisits
                    # this item before LIVE is allowed.
                    processed.append(item_id)
                    continue

            # Count only a fully published and cleaned item.
            current = self.db.get(item_id)
            # "processed" means a candidate completed its pipeline attempt and
            # is retained for lifecycle diagnostics/tests. Certification limits
            # are intentionally based only on fully published + cleaned items.
            processed.append(item_id)
            if current.state == State.PUBLISHED and current.cleanup_completed:
                completed_count += 1

            if bounded_limit is not None and completed_count >= bounded_limit:
                import logging
                logging.getLogger(__name__).info(
                    "[COORDINATOR][CATCH-UP] Limite fechado atingido: %s item(ns). "
                    "Devolvendo controle ao Coordinator para encerramento ou cutover de certificação.",
                    completed_count,
                )
                self._last_catch_up_completed_count = completed_count
                return processed

        self._last_catch_up_completed_count = completed_count
        return processed

    def run_catch_up(self) -> list[str]:
        import asyncio
        processed = asyncio.run(self.run_catch_up_async())
        # Never force LIVE here. The async runner is the authority: unresolved
        # technical failures leave historical sync open while later candidates
        # may still be processed; recovery remains responsible for the blocked
        # checkpoint before LIVE.
        return processed

    async def _fetch_live_candidate_with_watchdog(self, fetch_candidate):
        """Bound one LIVE Telegram discovery operation and force a clean reconnect on stall."""
        try:
            timeout = max(
                0.1,
                float(os.getenv("ARMORED_LIVE_DISCOVERY_TIMEOUT", "30")),
            )
        except ValueError:
            timeout = 30.0

        try:
            return await asyncio.wait_for(fetch_candidate(), timeout=timeout)
        except asyncio.TimeoutError:
            import logging

            logging.getLogger(__name__).error(
                "[COORDINATOR][LIVE] Descoberta Telegram excedeu %.1fs; "
                "forçando desconexão para permitir reconexão limpa no próximo ciclo.",
                timeout,
            )
            try:
                await self._release_source_connection()
            except Exception as release_exc:
                logging.getLogger(__name__).warning(
                    "[COORDINATOR][LIVE] Falha ao liberar sessão após timeout: %s",
                    release_exc,
                )
            raise

    async def run_live_once_async(self) -> list[str]:
        """Discover, materialize, release Sync, and process exactly one LIVE item."""
        source = self.source
        fetch_candidate = getattr(source, "fetch_live_candidate_async", None)

        if fetch_candidate is not None:
            message, checkpoints = await self._fetch_live_candidate_with_watchdog(
                fetch_candidate
            )
            messages = [] if message is None else [message]
        else:
            # Compatibility for lab sources that still expose the old method.
            fetch_batch = getattr(source, "fetch_live_batch_async", None)
            if fetch_batch is None:
                return []
            try:
                messages, checkpoints = await fetch_batch(limit=1)
            except TypeError as exc:
                if "limit" not in str(exc):
                    raise
                messages, checkpoints = await fetch_batch()
                checkpoints = dict(checkpoints) if messages else {}

        if len(messages) > 1 and checkpoints:
            # Never truncate an adapter's batch and then commit a checkpoint
            # covering messages that were not reserved or processed.
            raise RuntimeError("LIVE adapter must return at most one candidate per poll")
        # Cursor-free local adapters retain their original one-item contract;
        # there is no durable source checkpoint to advance past later inputs.
        messages = messages[:1]

        if not messages:
            commit = getattr(source, "commit_live_checkpoints", None)
            if commit is not None and checkpoints:
                commit(checkpoints)
            return []

        message = messages[0]
        item_id = str(message.telegram_message_id)
        if self.continuous_budget is not None:
            reservation = IngestMessage(
                telegram_message_id=str(message.telegram_message_id),
                source_id=str(getattr(message, "source_id", "telegram") or "telegram"),
                topic_id=getattr(message, "topic_id", None),
                topic_name=getattr(message, "topic_name", None),
                original_url=getattr(message, "original_url", None),
                source_path=getattr(message, "source_path", None),
                materialize=getattr(message, "materialize", None),
            )
            item_id = str(self.sync.reserve_message(reservation))
            if not self.continuous_budget.try_claim(item_id):
                self.log.info(
                    "[COORDINATOR][LIVE] orçamento diário esgotado; candidato reservado "
                    "sem Vision/download e checkpoint mantido"
                )
                return []
        materialized = False
        current = None
        try:
            item_id, materialized, current = await self._vision_gate_and_materialize_async(message)
            marker = getattr(source, "mark_ingested", None)
            if marker is not None:
                marker(str(message.telegram_message_id))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception(
                "[COORDINATOR][LIVE] Vision/materialization failed for %s; checkpoint blocked: %s",
                getattr(message, "telegram_message_id", "?"), exc,
            )
            try:
                current = self.db.get(str(item_id)) if item_id is not None else None
            except Exception:
                current = None

        if not materialized:
            if current is not None and current.state == State.WAITING_VISION:
                commit = getattr(source, "commit_live_checkpoints", None)
                if commit is not None and checkpoints:
                    commit(checkpoints)
                return [str(item_id)]
            if item_id is not None:
                # The candidate was durably reserved and attempted; even a
                # recoverable Vision/materialization failure belongs to this
                # poll result and must not disappear from lifecycle diagnostics.
                return [str(item_id)]
            return []

        # Materialization alone never advances the source checkpoint.
        # The item must complete the full pipeline and cleanup first.
        try:
            if self.db.get(str(item_id)).state != State.FAILED:
                self.run(str(item_id))
                current = self.db.get(str(item_id))

                # An unresolved RECOVERY item blocks the next LIVE polling
                # cycle. Reconcile once now; UNKNOWN remains durable and must
                # never be followed by another publication candidate.
                if current.state == State.RECOVERY:
                    try:
                        self.recover(str(item_id))
                    except Exception as recovery_exc:
                        import logging
                        logging.getLogger(__name__).warning(
                            "[COORDINATOR][LIVE] Item %s permanece em RECOVERY; "
                            "próximo ciclo será bloqueado: %s",
                            item_id,
                            recovery_exc,
                        )
                    current = self.db.get(str(item_id))

                if current.state == State.PUBLISHED and current.cleanup_completed:
                    commit = getattr(source, "commit_live_checkpoints", None)
                    if commit is not None and checkpoints:
                        commit(checkpoints)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception(
                "[COORDINATOR][LIVE] Falha no processamento de %s; checkpoint "
                "permanece no último item confirmado: %s",
                item_id,
                exc,
            )
        return [str(item_id)]

    def run_live_once(self) -> list[str]:
        import asyncio
        return asyncio.run(self.run_live_once_async())

    @staticmethod
    def _is_transient_source_error(exc: BaseException) -> bool:
        """Return whether an origin failure is safe to retry inside CATCH-UP."""
        return isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError))

    async def _run_catch_up_with_recovery_async(self) -> None:
        """Run historical processing and reconcile only recoverable items with ORIGINAL.

        A technical pre-download Vision failure remains in VISION with its error
        recorded; it is not sent to Recovery and the historical checkpoint stays
        blocked. RECOVERY reconciliation is reserved for failures with durable
        media/artifacts. WAITING_VISION is intentionally not auto-retried here.

        Real Telegram connection failures can also happen while the historical
        iterator is being advanced after a materialization failure. In that
        case the iterator is no longer safe to resume. Release the source,
        rebuild its historical iterator from the durable checkpoint, and retry
        with bounded backoff so the Coordinator process remains alive.
        """
        rediscovery_attempted: set[str] = set()
        source_error_backoff = max(
            1.0,
            float(os.getenv("ARMORED_SYNC_ERROR_BACKOFF", "5")),
        )
        while True:
            try:
                await self.run_catch_up_async()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._is_transient_source_error(exc) or not hasattr(
                    self.source, "reader"
                ):
                    raise
                import logging
                logging.getLogger(__name__).exception(
                    "[COORDINATOR][CATCH-UP] Telegram/rede de origem indisponível; "
                    "Coordinator permanece vivo e tentará reconectar: %s",
                    exc,
                )
                try:
                    await self._release_source_connection()
                except Exception as release_exc:
                    logging.getLogger(__name__).warning(
                        "[COORDINATOR][CATCH-UP] Falha ao liberar sessão após "
                        "erro de origem: %s",
                        release_exc,
                    )
                reset = getattr(self.source, "reset_historical_scan", None)
                if reset is None:
                    raise
                reset()
                await asyncio.sleep(source_error_backoff)
                continue
            # Historical completion alone is not enough to stop recovery:
            # a materialization failure may leave a durable RECEIVED reservation
            # without its immutable original while the source reports its scan
            # exhausted. Inspect the durable recovery candidates first.
            before = set()
            for row in self.db.conn.execute(
                "SELECT content_id, state FROM items "
                "WHERE state IN (?, ?, ?) ORDER BY created_at, content_id",
                (State.RECOVERY.value, State.RECEIVED.value, State.VISION.value),
            ).fetchall():
                item = self.db.get(str(row["content_id"]))
                if (
                    item.state == State.RECOVERY
                    or (
                        item.state in (State.RECEIVED, State.VISION)
                        and item.affiliate_url
                        and not item.original_path.is_file()
                    )
                ):
                    before.add(str(row["content_id"]))
            if not before:
                return

            # Pre-download candidates without an immutable original cannot be
            # recovered from SQLite alone. They are durable reservations created by
            # the Vision gate (RECEIVED/VISION) or technical Vision failures (RECOVERY),
            # so give each one its source rediscovery attempt before generic Recovery
            # can touch the remaining candidates. This is deliberately done after
            # the historical scan is exhausted: the source checkpoint must never
            # be reset while the current scan still has unseen candidates.
            source_rediscovery_missing = {
                item_id
                for item_id in before
                if not self.db.get(item_id).original_path.is_file()
                and (
                    self.db.get(item_id).state == State.RECOVERY
                    or (
                        self.db.get(item_id).state in (State.RECEIVED, State.VISION)
                        and self.db.get(item_id).affiliate_url
                    )
                )
            }
            pending_rediscovery = source_rediscovery_missing - rediscovery_attempted
            if pending_rediscovery:
                rediscovery_attempted.update(pending_rediscovery)
                reset = getattr(self.source, "reset_historical_scan", None)
                if reset is None:
                    return
                reset()
                continue

            self.recover_pending()

            after = set()
            for row in self.db.conn.execute(
                "SELECT content_id, state FROM items "
                "WHERE state IN (?, ?, ?) ORDER BY created_at, content_id",
                (State.RECOVERY.value, State.RECEIVED.value, State.VISION.value),
            ).fetchall():
                item = self.db.get(str(row["content_id"]))
                if (
                    item.state == State.RECOVERY
                    or (
                        item.state in (State.RECEIVED, State.VISION)
                        and item.affiliate_url
                        and not item.original_path.is_file()
                    )
                ):
                    after.add(str(row["content_id"]))

            # When Recovery makes progress, the Sync iterator must be rebuilt
            # from the durable checkpoint. The real Telegram source otherwise
            # remains exhausted and its in-memory _seen set would hide the
            # already-scanned candidate on the same process lifetime.
            if not before.issubset(after):
                reset = getattr(self.source, "reset_historical_scan", None)
                if reset is None:
                    return
                reset()
                continue

            # No Recovery/rediscovery progress: stop instead of tight-looping.
            return

    async def _run_forever_async(
        self,
        poll_seconds: float = 2.0,
        max_cycles: int | None = None,
    ) -> None:
        """Run the complete Coordinator lifetime inside one asyncio event loop.

        Telethon binds its client to the event loop used at connection time.
        The previous implementation called ``asyncio.run()`` for every
        CATCH-UP/LIVE operation, creating a new loop on every cycle.
        That is incompatible with a persistent Telethon session and can
        surface as ``The asyncio event loop must not change after connection``.
        """
        if self.continuous_budget is not None:
            await self._run_continuous_async(poll_seconds, max_cycles)
            return

        # Startup is a separate reconciliation phase. It inventories SQLite +
        # canonical storage and reconciles ambiguous publications before Sync
        # opens its Telegram session. It never downloads or publishes.
        self.startup_reconciler.run()
        self._prepare_catch_up_budget()
        self.recover_pending()

        if self.catch_up_budget is not None:
            import logging
            logging.getLogger(__name__).info(
                "[COORDINATOR][CATCH-UP] execução limitada run_id=%s limit=%s assigned=%s; LIVE fica para uma execução posterior",
                self.catch_up_budget.run_id,
                self.catch_up_budget.limit,
                self.catch_up_budget.assigned_count,
            )
            await self._run_catch_up_with_recovery_async()
            return

        source_complete = (
            self.source.is_historical_complete()
            if self.source is not None and hasattr(self.source, "is_historical_complete")
            else self.db.historical_complete()
        )
        if not source_complete or not self.db.has_sync_checkpoints():
            if (
                self.source is None or not hasattr(self.source, "is_historical_complete")
            ) and self.db.historical_complete() and not self.db.has_sync_checkpoints():
                self.db.set_sync_mode("CATCH_UP")
            await self._run_catch_up_with_recovery_async()

            # A bounded CATCH-UP run remains opt-in certification behavior.
            # By default it terminates here, preserving the existing contract.
            bounded_limit = getattr(self.source, "_historical_limit", None)
            cert_then_live = (
                os.getenv("ARMORED_CERT_CATCHUP_THEN_LIVE", "0").strip() == "1"
            )
            bounded_completed = (
                bounded_limit is not None
                and self._last_catch_up_completed_count >= int(bounded_limit)
            )

            # An unresolved historical candidate blocks the checkpoint and must
            # never fall through into LIVE. The only exception is the explicit
            # bounded certification flow, which first performs the source's
            # durable history-to-LIVE cutover.
            source_complete = (
                self.source.is_historical_complete()
                if self.source is not None and hasattr(self.source, "is_historical_complete")
                else self.db.historical_complete()
            )
            if not source_complete:
                if bounded_completed and cert_then_live:
                    cutover = getattr(self.source, "prepare_live_cutover_async", None)
                    if cutover is None:
                        raise RuntimeError(
                            "ARMORED_CERT_CATCHUP_THEN_LIVE=1 exige que a fonte "
                            "implemente prepare_live_cutover_async()"
                        )
                    import logging
                    await cutover()
                    logging.getLogger(__name__).info(
                        "[COORDINATOR][CERT] CATCH-UP limitado concluído; "
                        "cutover histórico seguro executado; entrando em LIVE"
                    )
                    source_complete = (
                        self.source.is_historical_complete()
                        if self.source is not None and hasattr(self.source, "is_historical_complete")
                        else self.db.historical_complete()
                    )

                if not source_complete:
                    import logging
                    logging.getLogger(__name__).warning(
                        "[COORDINATOR][CATCH-UP] Histórico ainda não concluído; "
                        "LIVE bloqueado. O processo permanecerá encerrado até a "
                        "reconciliação/reexecução do candidato pendente."
                    )
                    return

            if bounded_completed and not cert_then_live:
                return

        import logging
        logging.getLogger(__name__).info(
            "[COORDINATOR][LIVE] CATCH-UP concluído; Coordinator entrou em modo LIVE "
            "(monitoramento contínuo iniciado)"
        )

        cycles = 0
        live_error_backoff = max(
            1.0,
            float(os.getenv("ARMORED_LIVE_ERROR_BACKOFF", "5")),
        )
        while max_cycles is None or cycles < max_cycles:
            self.db.heartbeat_runtime_lock("coordinator")
            try:
                processed = await self.run_live_once_async()
                cycles += 1
                if not processed and (max_cycles is None or cycles < max_cycles):
                    await asyncio.sleep(float(poll_seconds))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Telegram/network outages are transient LIVE conditions, not
                # process-fatal errors. The source deliberately leaves the
                # checkpoint unchanged when discovery/materialization fails,
                # so the same candidate can be rediscovered after reconnect.
                # Keep the Coordinator alive and let the next iteration rebuild
                # the Telegram client/session through _ensure_source_connection.
                import logging
                logging.getLogger(__name__).exception(
                    "[COORDINATOR][LIVE] Telegram/rede de origem indisponível; "
                    "Coordinator permanece vivo e tentará reconectar: %s",
                    exc,
                )
                cycles += 1
                if max_cycles is None or cycles < max_cycles:
                    await asyncio.sleep(live_error_backoff)

    def _continuous_batch_has_unfinished_items(self, item_ids: set[str]) -> bool:
        terminal = {State.WAITING_VISION, State.DUPLICATE}
        for item_id in item_ids:
            item = self.db.get(item_id)
            if item.state in terminal:
                continue
            if item.state == State.PUBLISHED and item.cleanup_completed:
                continue
            return True
        return False

    async def _run_continuous_async(
        self, poll_seconds: float = 2.0, max_live_cycles: int | None = None
    ) -> None:
        """Run durable bounded batches, complete CATCH-UP, then stay LIVE.

        A fixed UTC-day ledger caps unique content IDs across all batches and
        LIVE. Exhaustion waits for the next window without advancing source
        checkpoints or changing the persistent allowance.
        """
        assert self.continuous_budget is not None
        controller = self.continuous_budget
        self.startup_reconciler.run()
        live_cycles = 0
        backoff = max(0.25, float(poll_seconds))
        while not self._shutdown_requested:
            history_complete = (
                self.source.is_historical_complete()
                if self.source is not None and hasattr(self.source, "is_historical_complete")
                else self.db.historical_complete()
            )
            pending_work = self._catch_up_candidate_ids()
            if history_complete and not pending_work:
                self.catch_up_budget = None
                if controller.remaining <= 0:
                    await asyncio.sleep(min(controller.seconds_until_reset(), 60.0))
                    continue
                self.db.heartbeat_runtime_lock("coordinator")
                try:
                    processed = await self.run_live_once_async()
                    live_cycles += 1
                    if max_live_cycles is not None and live_cycles >= max_live_cycles:
                        return
                    if not processed:
                        await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.log.exception("[COORDINATOR][LIVE] erro transitório; checkpoints mantidos")
                    await asyncio.sleep(backoff)
                continue

            batch = controller.open_batch()
            if batch is None:
                await asyncio.sleep(min(controller.seconds_until_reset(), 60.0))
                continue
            run_id, limit = batch
            budget = DurableCatchUpBudget(
                self.storage.database / "catch-up-work-budget.sqlite3",
                run_id,
                limit,
                continuous_budget=controller,
            )
            previous_budget = self.catch_up_budget
            self.catch_up_budget = budget
            try:
                if callable(getattr(self.source, "reset_historical_scan", None)):
                    self.source.reset_historical_scan()
                self._prepare_catch_up_budget()
                self.recover_pending()
                await self._run_catch_up_with_recovery_async()
                assigned = budget.assigned_ids()
                source_failed = bool(
                    getattr(self.source, "historical_materialization_failed", False)
                )
                history_complete = (
                    self.source.is_historical_complete()
                    if self.source is not None and hasattr(self.source, "is_historical_complete")
                    else self.db.historical_complete()
                )
                if source_failed or self._continuous_batch_has_unfinished_items(assigned):
                    self.log.info(
                        "[COORDINATOR][CONTINUOUS] lote %s mantido aberto para retomada; itens=%s limit=%s",
                        run_id, len(assigned), limit,
                    )
                    await asyncio.sleep(backoff)
                    continue
                controller.close_batch(run_id)
                self.log.info(
                    "[COORDINATOR][CONTINUOUS] lote %s concluído; histórico_completo=%s "
                    "itens=%s quota_diária_restante=%s",
                    run_id, history_complete, len(assigned), controller.remaining,
                )
            finally:
                self.catch_up_budget = previous_budget
                budget.close()
            if not history_complete and callable(
                getattr(self.source, "reset_historical_scan", None)
            ):
                self.source.reset_historical_scan()

    def run_forever(self, poll_seconds: float = 2.0, max_cycles: int | None = None) -> None:
        """Recover, finish catch-up once, then monitor Telegram continuously.

        The entire production lifetime is executed under one asyncio loop.
        This is required by Telethon and also makes LIVE restart behavior
        deterministic.
        """
        import asyncio
        # The production CLI acquires the SQLite lease before schema/runtime
        # validation in Coordinator.build(). run_forever() must reuse that
        # ownership instead of attempting a second same-process acquisition.
        if not self._runtime_lock_held:
            self.db.acquire_runtime_lock("coordinator")
            self._runtime_lock_held = True
        self._shutdown_requested = False
        previous_sigint = signal.getsignal(signal.SIGINT)

        def _request_shutdown(signum, frame):
            # Mark shutdown before an interrupted blocking call returns. Child
            # tools such as RVC/FFmpeg may turn Ctrl+C into a non-zero exit code.
            self._shutdown_requested = True

        signal.signal(signal.SIGINT, _request_shutdown)
        try:
            asyncio.run(
                self._run_forever_async(
                    poll_seconds=poll_seconds,
                    max_cycles=max_cycles,
                )
            )
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            if self._runtime_lock_held:
                self.db.release_runtime_lock("coordinator")
                self._runtime_lock_held = False
    def retry_waiting_vision(
        self,
        *,
        limit: int | None = 1,
        content_ids: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, str]]:
        """Explicitly retry WAITING_VISION items through Vision only.

        Requires the runtime lock. Positive limits run batches capped at 1000;
        ``content_ids`` restricts execution to an explicit reviewed allowlist.
        ``limit=None`` or ``limit=0`` snapshots all eligible rows only when no
        allowlist is supplied. Never downloads media or runs Studio/IA/Hub.
        Exact recent no-offer outcomes are persisted with an expiry and do not
        trigger another affiliate API request during that interval.
        """
        if not self._runtime_lock_held:
            raise RuntimeError(
                "WAITING_VISION retry requires the Coordinator runtime lock"
            )
        candidates = self.db.pending_waiting_vision_retries(
            limit=limit,
            content_ids=content_ids,
        )
        outcomes: list[dict[str, str]] = []
        started = time.perf_counter()
        total = len(candidates)
        for index, candidate in enumerate(candidates, start=1):
            try:
                self.pipeline.run(
                    candidate.content_id,
                    stop_after_vision=True,
                    retry_waiting_vision=True,
                )
                current = self.db.get(candidate.content_id)
                outcomes.append({
                    "content_id": candidate.content_id,
                    "state": current.state.value,
                    "result": "approved" if current.affiliate_url else "unresolved",
                })
            except Exception as exc:
                self.log.exception(
                    "[COORDINATOR][VISION-RETRY] item=%s remains durable: %s",
                    candidate.content_id, exc,
                )
                outcomes.append({
                    "content_id": candidate.content_id,
                    "state": self.db.get(candidate.content_id).state.value,
                    "result": "technical-error",
                })

            # Keep long historical runs observable without emitting one log
            # record per candidate. This is instrumentation only: ordering and
            # durable state transitions remain unchanged.
            if index % 100 == 0 or index == total:
                elapsed = time.perf_counter() - started
                average = elapsed / index if index else 0.0
                self.log.info(
                    "[COORDINATOR][VISION-RETRY][PROGRESS] processed=%s/%s "
                    "elapsed=%.1fs average=%.2fs/item approved=%s unresolved=%s errors=%s",
                    index,
                    total,
                    elapsed,
                    average,
                    sum(1 for row in outcomes if row["result"] == "approved"),
                    sum(1 for row in outcomes if row["result"] == "unresolved"),
                    sum(1 for row in outcomes if row["result"] == "technical-error"),
                )
        return outcomes


    def _has_speech_studio_v5_completion(self, item_id: str) -> bool:
        """Return whether SQLite records the v5 render as the publication input."""
        return self.db.has_speech_studio_v5_completion(item_id)

    def _preflight_speech_studio_v5(self):
        """Load Speech's real processor and validate all fixed render dependencies."""
        # Explicit object injection exists for deterministic tests only. Normal
        # Coordinator construction never sets this attribute and must preflight
        # the actual Speech runtime before processing media.
        injected = getattr(self, "_speech_processor_override", None)
        if injected is not None:
            return injected
        import shutil
        from ArmoredSpeech.model_processor import ModelProcessor

        root = self.storage.root.resolve(strict=True)
        processor = ModelProcessor()
        preflight = getattr(getattr(processor, "intelligence", None), "preflight", None)
        if not callable(preflight):
            raise RuntimeError("ArmoredSpeech processor does not expose intelligence.preflight()")
        preflight()

        for label, key, fallback in (
            ("FFmpeg", "ARMORED_FFMPEG", "ffmpeg"),
            ("FFprobe", "ARMORED_FFPROBE", "ffprobe"),
        ):
            executable = (os.getenv(key) or fallback).strip()
            if not executable or shutil.which(executable) is None:
                raise RuntimeError(f"{label} não encontrado; configuração {key} inválida.")

        configured_effect = (os.getenv("ARMORED_SPEECH_EFFECT") or "").strip()
        effect = Path(configured_effect).expanduser() if configured_effect else root / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
        if not effect.is_absolute():
            effect = root / effect
        effect = effect.resolve(strict=True)
        try:
            effect.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("Asset de efeito Speech precisa permanecer dentro do root do projeto.") from exc
        if not effect.is_file() or effect.stat().st_size <= 0:
            raise RuntimeError("Asset canônico ArmoredSpeech/assets/efeitosonoro.wav ausente ou vazio.")

        if os.getenv("ARMORED_STUDIO_INTRO", "1").strip() != "0":
            configured_banner = (os.getenv("ARMORED_STUDIO_BANNER") or "").strip()
            banner = Path(configured_banner).expanduser() if configured_banner else root / "ArmoredStudio" / "assets" / "banner.png"
            if not banner.is_absolute():
                banner = root / banner
            banner = banner.resolve(strict=True)
            try:
                banner.relative_to(root)
            except ValueError as exc:
                raise RuntimeError("Banner do Studio precisa permanecer dentro do root do projeto.") from exc
            if not banner.is_file() or banner.stat().st_size <= 0:
                raise RuntimeError("Banner canônico do Studio ausente ou vazio.")
        return processor

    def _run_speech_studio_batch(self, item_ids: list[str]) -> dict[str, Path]:
        """Run the complete Speech v5 hand-off before Studio, then persist results atomically.

        The public Coordinator path may pass a one-item batch (LIVE). CATCH-UP can
        pass all currently stock-backed items together. No Publisher is invoked here.
        """
        import json
        import sqlite3
        from datetime import datetime, timezone
        from uuid import uuid4

        from ArmoredSpeech.batch import SpeechItem, run_bulk
        from ArmoredSpeech.studio_batch_runner import run_complete_batch
        from armored_core.integrity import sha256_file

        normalized_ids = [str(value) for value in item_ids]
        if not normalized_ids or len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError("Speech/Studio batch requires unique non-empty content IDs")

        root = self.storage.root.resolve(strict=True)
        items = [self.db.get(item_id) for item_id in normalized_ids]
        before_hashes: dict[str, str] = {}
        for item in items:
            if item.state != State.SPEECH:
                raise RuntimeError(
                    f"Speech/Studio v5 requires durable SPEECH state; "
                    f"item={item.content_id}, state={item.state.value}"
                )
            if not item.affiliate_url or not item.affiliate_name:
                raise RuntimeError(f"Speech/Studio v5 requires Vision-approved affiliate metadata: {item.content_id}")
            original = item.original_path.resolve(strict=True)
            workspace = item.workspace.resolve(strict=True)
            try:
                original.relative_to(workspace)
                original.relative_to(root)
                workspace.relative_to(root)
            except ValueError as exc:
                raise RuntimeError(f"Original/workspace escaped project root for {item.content_id}") from exc
            if not original.is_file() or original.stat().st_size <= 0:
                raise FileNotFoundError(f"immutable-original-missing-or-empty:{original}")
            destination = self.storage.result(
                item.content_id, item.affiliate_url, item.affiliate_name,
                source_id=item.source_id,
            ).resolve()
            try:
                destination.relative_to(root)
            except ValueError as exc:
                raise RuntimeError(f"Studio destination escaped project root: {destination}") from exc
            if destination == original:
                raise RuntimeError("Studio destination collides with immutable original")
            if destination.exists():
                raise FileExistsError(
                    f"Studio refuses to overwrite an existing output; recovery/review required: {destination}"
                )
            before_hashes[str(item.content_id)] = sha256_file(original).lower()

        run_id = (
            "coordinator-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + uuid4().hex[:12]
        )
        run_root = self.storage.storage / "validation" / "speech-studio-v5" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        manifest_path = run_root / "manifest.json"
        studio_report_path = run_root / "studio-report.json"
        speech_database = self.storage.database / "armoredspeech.sqlite3"

        try:
            processor = self._preflight_speech_studio_v5()
            speech_items = [
                SpeechItem(
                    source_id=str(item.source_id),
                    item_id=str(item.telegram_message_id),
                    video_path=Path(item.original_path),
                    source_order=index,
                )
                for index, item in enumerate(items)
            ]
            counts = run_bulk(
                speech_items,
                database=speech_database,
                processor=processor,
            )
            if not isinstance(counts, dict):
                raise RuntimeError("ArmoredSpeech.run_bulk returned an invalid result")
            if int(counts.get("failed", 0) or 0) != 0 or int(counts.get("prepared", 0) or 0) != len(items):
                raise RuntimeError(
                    f"Speech did not prepare the complete batch: expected={len(items)}, counts={counts}"
                )

            # Verify Speech's persisted SQLite decisions in read-only mode.
            db_uri = f"file:{speech_database.resolve().as_posix()}?mode=ro"
            connection = sqlite3.connect(db_uri, uri=True)
            try:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT source_id,item_id,status,wav_path,original_sha256_before,original_sha256_after "
                    "FROM armored_speech_results"
                ).fetchall()
            finally:
                connection.close()
            decisions = {(str(row["source_id"]), str(row["item_id"])): row for row in rows}
            manifest_items = []
            for item in items:
                identity = (str(item.source_id), str(item.telegram_message_id))
                decision = decisions.get(identity)
                if decision is None or str(decision["status"]) != "READY":
                    status = str(decision["status"]) if decision is not None else "MISSING_DECISION"
                    raise RuntimeError(f"Speech decision not READY for {identity}: {status}")
                expected_hash = before_hashes[str(item.content_id)]
                before = str(decision["original_sha256_before"] or "").lower()
                after = str(decision["original_sha256_after"] or "").lower()
                if not before or before != expected_hash or after != expected_hash:
                    raise RuntimeError(f"Speech original hash evidence invalid for {identity}")
                original = Path(item.original_path).resolve(strict=True)
                if sha256_file(original).lower() != expected_hash:
                    raise RuntimeError(f"Original changed during Speech for {identity}")
                wav = Path(str(decision["wav_path"] or "")).resolve(strict=True)
                try:
                    wav.relative_to(Path(item.workspace).resolve(strict=True))
                except ValueError as exc:
                    raise RuntimeError(f"Speech WAV escaped item workspace for {identity}") from exc
                if not wav.is_file() or wav.stat().st_size <= 0:
                    raise RuntimeError(f"Speech WAV missing or empty for {identity}")

                manifest_items.append({
                    "source_id": str(item.source_id),
                    "telegram_message_id": str(item.telegram_message_id),
                    "content_id": str(item.content_id),
                    "state": State.SPEECH.value,
                    "workspace": str(Path(item.workspace).resolve()),
                    "original_path": str(original),
                    "original_sha256": expected_hash,
                    "working_path": str(item.working_path.resolve()) if item.working_path else None,
                    "affiliate_name": item.affiliate_name,
                    "affiliate_url": item.affiliate_url,
                    "original_url": item.original_url,
                    "topic_id": item.topic_id,
                    "topic_name": item.topic_name,
                    "affiliate_urls": list(item.affiliate_urls),
                    "ia_context": item.ia_context or {},
                })

            manifest = {
                "schema_version": 1,
                "phase": "ARMORED_SPEECH",
                "phase_status": "COMPLETE",
                "approved": True,
                "batch_id": run_id,
                "expected_item_count": len(manifest_items),
                "items": manifest_items,
            }
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            # Persist the hand-off fence for the entire set before any Studio
            # render starts. A partial Studio failure cannot flow into Hub.
            for item in items:
                self.db.transition(
                    item.content_id,
                    State.STUDIO_BATCH,
                    "speech-v5-ready-entering-studio-batch",
                )

            studio_runner_override = getattr(
                self, "_speech_studio_batch_runner_override", None
            )
            studio_report = (studio_runner_override or run_complete_batch)(
                root=root,
                manifest_path=manifest_path,
                speech_database=speech_database,
                report_path=studio_report_path,
                copy_only=False,
                storage_root=self.storage.storage_root,
            )
            if (
                not isinstance(studio_report, dict)
                or studio_report.get("status") != "SUCCESS"
                or studio_report.get("originals_intact") is not True
            ):
                raise RuntimeError("Speech/Studio did not confirm SUCCESS and original integrity")

            expected_items = {
                (str(item.source_id), str(item.telegram_message_id)): item
                for item in items
            }
            processed_rows = studio_report.get("processed")
            if not isinstance(processed_rows, list) or len(processed_rows) != len(items):
                raise RuntimeError("Studio report does not contain exactly the requested batch")
            outputs: dict[str, Path] = {}
            seen: set[tuple[str, str]] = set()
            for record in processed_rows:
                if not isinstance(record, dict):
                    raise RuntimeError("Studio report contains an invalid item record")
                identity = (str(record.get("source_id") or ""), str(record.get("item_id") or ""))
                item = expected_items.get(identity)
                if item is None or identity in seen:
                    raise RuntimeError(f"Studio report item identity invalid/duplicated: {identity}")
                seen.add(identity)
                expected_hash = before_hashes[str(item.content_id)]
                if str(record.get("original_sha256_before") or "").lower() != expected_hash:
                    raise RuntimeError(f"Studio did not confirm original hash for {identity}")
                output = Path(str(record.get("output_path") or "")).resolve(strict=True)
                expected_output = self.storage.result(
                    item.content_id, item.affiliate_url, item.affiliate_name,
                    source_id=item.source_id,
                ).resolve(strict=True)
                if output != expected_output or output == Path(item.original_path).resolve(strict=True):
                    raise RuntimeError(f"Studio output path does not match canonical destination for {identity}")
                try:
                    output.relative_to(root)
                except ValueError as exc:
                    raise RuntimeError(f"Studio output escaped project root for {identity}") from exc
                if not output.is_file() or output.stat().st_size <= 0:
                    raise RuntimeError(f"Studio output invalid for {identity}")
                if sha256_file(Path(item.original_path)).lower() != expected_hash:
                    raise RuntimeError(f"Original changed during Studio for {identity}")
                outputs[str(item.content_id)] = output

            if set(outputs) != set(normalized_ids):
                raise RuntimeError("Studio omitted one or more requested items")

            # One SQLite transaction changes every item from STUDIO_BATCH to
            # PUBLISHING only after the complete batch is rendered and verified.
            self.db.complete_speech_studio_batch(outputs)
            self.log.info(
                "[COORDINATOR][SPEECH/STUDIO] v5 batch=%s items=%s complete; "
                "publication may now begin",
                run_id, len(outputs),
            )
            return outputs
        except Exception as exc:
            # Preserve the durable in-flight boundary on requested shutdown;
            # Recovery on restart will rebuild from ORIGINAL instead of treating
            # a killed native child as a completed render.
            if self.pipeline._shutdown_checker():
                self.log.warning(
                    "[COORDINATOR][SPEECH/STUDIO] shutdown during v5 batch; "
                    "preserving STUDIO_BATCH for restart: %s",
                    exc,
                )
                raise KeyboardInterrupt from exc
            # If the external phase failed, keep immutable ORIGINALs and write a
            # durable failure event. Recovery recognizes this boundary and will
            # rebuild derived artifacts through Speech v5, never legacy Studio.
            reason = f"speech-studio-v5-failed:{type(exc).__name__}"
            for item_id in normalized_ids:
                try:
                    current = self.db.get(item_id)
                    if current.state in {State.SPEECH, State.STUDIO_BATCH}:
                        self.db.transition(item_id, State.RECOVERY, reason)
                except Exception:
                    self.log.exception(
                        "[COORDINATOR][SPEECH/STUDIO] could not persist failure boundary for %s",
                        item_id,
                    )
            raise


    def _run_recovery_speech_studio(self, item_id: str) -> None:
        """Repair Speech/Studio through v5 without crossing into Hub.

        Recovery can reconcile several interrupted items in one pass. It must
        finish the derived batch for each item and leave publication to the
        normal Coordinator loop only after the recovery pass has completed.
        """
        self._run_speech_studio_batch([str(item_id)])

    def run(self, item_id: str) -> None:
        """Run one item through IA -> Speech v5 batch -> Studio -> Hub in order."""
        item_id = str(item_id)
        item = self.db.get(item_id)

        if item.state in {State.PUBLISHED, State.WAITING_VISION, State.DUPLICATE, State.STUDIO_COMPLETE}:
            # Preserve existing terminal/business outcomes. PUBLISHED may still
            # need its already-authorized cleanup; the other states are fences.
            self.pipeline.run(item_id)
            return

        if item.state in {State.RECOVERY, State.FAILED}:
            if item.state == State.FAILED:
                self.db.transition(item_id, State.RECOVERY, "legacy-failed-recovery")
            self.recovery.reconcile(item_id)
            return

        if item.state == State.STUDIO:
            # Existing rows from the pre-v5 path cannot be rendered/published
            # again through the legacy Studio call. Force evidence-driven
            # Recovery to rebuild the derived chain through Speech v5.
            self.db.transition(item_id, State.RECOVERY, "legacy-studio-state-requires-speech-v5")
            self.recovery.reconcile(item_id)
            return

        if item.state == State.STUDIO_BATCH:
            # STUDIO_BATCH surviving a Coordinator invocation means the process
            # stopped inside the external phase. Do not blindly reuse a partial
            # output; the explicit Recovery path preserves ORIGINAL and rebuilds.
            self.db.transition(item_id, State.RECOVERY, "interrupted-speech-studio-v5-batch")
            self.recovery.reconcile(item_id)
            return

        if item.state == State.PUBLISHING:
            if not self._has_speech_studio_v5_completion(item_id):
                self.db.transition(item_id, State.RECOVERY, "legacy-publishing-state-requires-speech-studio-v5")
                self.recovery.reconcile(item_id)
                return
            self.pipeline.run(item_id)
            return

        if item.state == State.SPEECH:
            self._run_speech_studio_batch([item_id])
            self.pipeline.run(item_id)
            return

        # Coordinator is the sole composition root. Stop Pipeline immediately
        # after IA, so no standard path can call Studio without its v5 payload.
        self.pipeline.run(item_id, stop_after_ia=True)
        current = self.db.get(item_id)
        if current.state == State.SPEECH:
            self._run_speech_studio_batch([item_id])
            self.pipeline.run(item_id)

    def recover(self, item_id: str) -> None:
        self.recovery.reconcile(item_id)

    def close(self) -> None:
        try:
            try:
                if self.catch_up_budget is not None:
                    self.catch_up_budget.close()
            finally:
                try:
                    if self.continuous_budget is not None:
                        self.continuous_budget.close()
                finally:
                    if self._runtime_lock_held:
                        self.db.release_runtime_lock("coordinator")
        finally:
            self._runtime_lock_held = False
            self.db.close()

    def process_next(self):
        item_id = self.ingest_once()
        if item_id is None:
            return None
        self.run(item_id)
        return item_id

    def recover_pending(self):
        allowed_ids = self._prepare_catch_up_budget()
        states = (
            State.RECEIVED.value, State.VISION.value, State.IA.value,
            State.SPEECH.value, State.STUDIO_BATCH.value, State.STUDIO.value,
            State.PUBLISHING.value, State.RECOVERY.value,
            State.FAILED.value, State.PUBLISHED.value,
        )
        placeholders = ",".join("?" for _ in states)
        rows = self.db.conn.execute(
            f"SELECT content_id, state FROM items WHERE state IN ({placeholders}) "
            "OR (state=? AND cleanup_completed=0) ORDER BY created_at, content_id",
            (*states, State.PUBLISHED.value),
        ).fetchall()
        recovered = []
        for row in rows:
            item_id = str(row["content_id"])
            if allowed_ids is not None and item_id not in allowed_ids:
                continue
            if (
                str(row["state"]) == State.PUBLISHING.value
                and self.db.publication(item_id) is None
                and self._has_speech_studio_v5_completion(item_id)
            ):
                # A prior bounded run reached the durable publication barrier.
                # Preserve the validated payload for a later explicitly
                # authorized send; do not rebuild Speech/Studio on resume.
                continue

            # FAILED is a legacy terminal state from the previous
            # generic-exception path. Current processing failures are persisted
            # as RECOVERY; legacy FAILED rows must therefore be reopened once
            # through the same deterministic recovery path instead of being
            # silently abandoned. Recovery itself decides the correct resume
            # stage from durable state/artifacts.
            if str(row["state"]) == State.FAILED.value:
                self.db.transition(item_id, State.RECOVERY, "legacy-failed-recovery")
            # RECEIVED without an immutable original is a durable Telegram
            # reservation whose download was interrupted. The Sync source must
            # rediscover/materialize it; Recovery cannot invent the missing
            # bytes. Keep it in SQLite and let CATCH_UP/LIVE continue it.
            if str(row["state"]) == State.RECEIVED.value:
                item = self.db.get(item_id)
                if not item.original_path.is_file():
                    # An interrupted download leaves only the transactional
                    # .part artifact. Sync always restarts materialization
                    # from Telegram, so never treat the partial as a usable
                    # original. Preserve it in a receipted backup before
                    # rediscovery; never erase interruption evidence.
                    self.recovery.preserve_partial_download(item_id)
                    continue
            try:
                self.recover(item_id)
                current = self.db.get(item_id)
                recovered.append(item_id)
                if (
                    current.state in (State.WAITING_VISION, State.RECOVERY)
                    or (current.state == State.PUBLISHED and not current.cleanup_completed)
                ):
                    # Startup recovery is ordered. An unresolved current item,
                    # including PUBLISHED with cleanup still pending, blocks
                    # progression because its durable lifecycle is incomplete.
                    break
            except Exception as exc:
                import logging
                current = self.db.get(item_id)
                logging.getLogger(__name__).exception(
                    "Recovery falhou para item %s; state=%s: %s",
                    item_id,
                    current.state.value,
                    exc,
                )
                if (
                    current.state in (State.WAITING_VISION, State.RECOVERY)
                    or (current.state == State.PUBLISHED and not current.cleanup_completed)
                ):
                    # The current candidate still owns the checkpoint. Never
                    # continue startup recovery with a later item while this
                    # candidate remains unresolved or cleanup-pending.
                    break
        return recovered
