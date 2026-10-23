from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from armored_core.database import Database
from armored_core.models import Item, PublicationCheck, State
from armored_core.integrity import sha256_file
from armored_core.services import PublicationResult, PublicationUnknownError
from armored_core.republication_ledger import VersionedPublicationLedger
from armored_core.telegram_session import telegram_session_basename


class ArmoredHub:
    """Telegram publication boundary; SQLite is the only durable state."""

    def __init__(self, root: Path, db: Database | None = None, routes=(), replay_ledger=None):
        self.root = Path(root)
        self.db = db
        self.routes = tuple(routes)
        self._destination_chat_id: str | None = None
        self.republication_ledger = replay_ledger

    def _republication_ledger(self):
        if self.republication_ledger is None:
            configured = (os.getenv("ARMORED_REPUBLICATION_LEDGER_PATH") or "").strip()
            path = Path(configured) if configured else self.root / "storage" / "database" / "republication-ledger.sqlite3"
            self.republication_ledger = VersionedPublicationLedger(path)
        return self.republication_ledger

    def _route_for(self, item: Item):
        for route in self.routes:
            if str(route.source.source_id) == str(item.source_id):
                return route
        return None

    def _destination_for(self, item: Item) -> tuple[str, int]:
        record = self._publication(item)
        if self._publication_field(record, "destination_chat_id") and self._publication_field(record, "destination_topic_id") is not None:
            return str(self._publication_field(record, "destination_chat_id")), int(self._publication_field(record, "destination_topic_id"))
        route = self._route_for(item)
        if route is not None:
            return str(route.hub.chat_id), int(route.hub.topic_id)
        topic = (os.getenv("ARMORED_HUB_TOPIC_ID") or "").strip()
        chat = (os.getenv("ARMORED_CREATOR_GROUP_ID") or "").strip()
        if not chat:
            chat = self._resolve_destination_chat_id(topic)
        if not chat or not topic:
            raise RuntimeError("Destino Hub não configurado")
        return str(chat), int(topic)

    def _assert_test_destination_lock(self, item: Item) -> None:
        """Fail closed when a test run is restricted to one Telegram destination.

        The lock checks the *effective* route (including any persisted
        publication destination), not merely the fallback environment values.
        It does not redirect an operational route to a test topic.
        """
        allowed_chat = (os.getenv("ARMORED_HUB_TEST_CHAT_ID") or "").strip()
        allowed_topic = (os.getenv("ARMORED_HUB_TEST_TOPIC_ID") or "").strip()
        if not allowed_chat and not allowed_topic:
            return
        if not allowed_chat or not allowed_topic:
            raise RuntimeError("Hub test destination lock requires both chat and topic IDs")
        try:
            if int(allowed_chat) == 0 or int(allowed_topic) <= 0:
                raise ValueError("invalid Telegram destination")
            expected = (str(int(allowed_chat)), int(allowed_topic))
        except ValueError as exc:
            raise RuntimeError("Hub test destination lock has invalid chat/topic IDs") from exc
        actual = self._destination_for(item)
        if actual != expected:
            raise RuntimeError(
                "Hub test destination lock refused configured route or persisted destination"
            )

    def _publication(self, item: Item):
        publication = getattr(self.db, "publication", None) if self.db is not None else None
        return publication(item.content_id) if callable(publication) else None

    @staticmethod
    def _publication_field(record, name: str, default=None):
        if record is None:
            return default
        try:
            value = record[name]
        except (KeyError, IndexError, TypeError, AttributeError):
            return default
        return default if value is None else value

    @staticmethod
    def _run_async(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="armored-hub-telegram") as executor:
            return executor.submit(asyncio.run, coro).result()

    def check_publication(self, item: Item) -> PublicationCheck:
        # A disabled Hub must not open a Telegram session even to reconcile.
        if os.getenv("ARMORED_HUB_DRY_RUN", "0") == "1":
            return PublicationCheck.UNKNOWN
        record = self._publication(item)
        if record is None:
            return PublicationCheck.UNKNOWN

        if record["confirmed"]:
            message_id = record["published_message_id"]
            if not message_id:
                return PublicationCheck.UNKNOWN
            status = self._verify_telegram_message(str(message_id), item)
            if status is True:
                return PublicationCheck.CONFIRMED
            if status is None:
                return PublicationCheck.UNKNOWN
            return PublicationCheck.ABSENT

        message_id = record["published_message_id"]
        if message_id:
            status = self._verify_telegram_message(str(message_id), item)
            if status is True:
                self.db.publication_confirmed(item.content_id, str(message_id))
                return PublicationCheck.CONFIRMED
            if status is None:
                return PublicationCheck.UNKNOWN
            return PublicationCheck.ABSENT

        if (
            str(record["verification_status"] or "") == "PENDING"
            and self.db.has_speech_studio_v5_completion(item.content_id)
        ):
            # This implementation durably marks send intent before send_video.
            # A preflight-only failure has no external side effect to reconcile.
            return PublicationCheck.ABSENT

        attempts = max(1, int(os.getenv("ARMORED_TELEGRAM_VERIFY_ATTEMPTS", "3")))
        delay = max(0.0, float(os.getenv("ARMORED_TELEGRAM_VERIFY_RETRY_DELAY", "2")))
        send_started = str(record["verification_status"] or "") == "SENT_UNVERIFIED"

        for attempt in range(1, attempts + 1):
            matches = self._find_telegram_publications(item)
            if matches is not None:
                if len(matches) == 1:
                    self.db.publication_confirmed(item.content_id, matches[0])
                    return PublicationCheck.CONFIRMED
                if len(matches) > 1:
                    print(
                        f"[HUB][VERIFY][UNKNOWN] item={item.content_id} "
                        f"multiple exact matches={len(matches)}"
                    )
                    return PublicationCheck.UNKNOWN

                if attempt < attempts and delay:
                    time.sleep(delay)
                    continue
                # Once an external send has started, absence of
                # Telegram evidence is not proof that the publication did not
                # happen. Keep the item UNKNOWN so recovery cannot republish
                # through an unresolved external side-effect window.
                if send_started:
                    print(
                        f"[HUB][VERIFY][UNKNOWN] item={item.content_id} "
                        "send already started; no exact Telegram evidence"
                    )
                    return PublicationCheck.UNKNOWN
                return PublicationCheck.ABSENT

            print(
                f"[HUB][VERIFY] item={item.content_id} "
                f"attempt={attempt}/{attempts} returned UNKNOWN"
            )
            if attempt < attempts and delay:
                time.sleep(delay)

        return PublicationCheck.UNKNOWN

    def _validate_publication_input(self, item: Item) -> None:
        if os.getenv("ARMORED_HUB_DRY_RUN", "0") == "1":
            raise RuntimeError("ARMORED_HUB_DRY_RUN=1: publication and verification are disabled")
        if self.db is None:
            raise RuntimeError("ArmoredHub exige Database para publicação idempotente")
        current = self.db.get(item.content_id)
        if current != item:
            raise RuntimeError("Hub received a stale item; reload SQLite before publication")
        if item.state not in (State.PUBLISHING, State.PUBLISHED):
            raise RuntimeError("Hub requires a completed Speech/Studio publication boundary")
        if not self.db.has_speech_studio_v5_completion(item.content_id):
            raise RuntimeError("Hub requires durable Speech/Studio v5 completion evidence")
        output = Path(item.result_path or "")
        for path in (item.original_path, output):
            if not path.is_file() or path.stat().st_size <= 0:
                raise RuntimeError("Hub received missing or empty original/result")
            try:
                path.resolve().relative_to(self.root.resolve())
            except ValueError as exc:
                raise RuntimeError("Hub media must remain inside the project root") from exc
        if output.resolve().parent != item.workspace.resolve():
            raise RuntimeError("Hub result must belong to the item's canonical workspace")
        if not item.original_sha256 or sha256_file(item.original_path) != item.original_sha256:
            raise RuntimeError("Hub original integrity validation failed")
        self._publication_text(item)
        self._assert_test_destination_lock(item)

    def publish_once(self, item: Item) -> PublicationResult:
        self._validate_publication_input(item)
        if self.db is None:
            raise RuntimeError("ArmoredHub exige Database para publicação idempotente")

        existing = self._publication(item)
        if existing is None:
            self.db.publication_started(item.item_id)
            check = PublicationCheck.ABSENT
        else:
            check = self.check_publication(item)

        if check == PublicationCheck.CONFIRMED:
            record = self._publication(item)
            message_id = record["published_message_id"] if record else None
            if not message_id:
                raise PublicationUnknownError("publication-confirmed-without-real-message-id")
            return PublicationResult(True, str(message_id))

        if check == PublicationCheck.UNKNOWN:
            raise PublicationUnknownError("Telegram publication outcome is UNKNOWN")

        return self.publish(item)

    @staticmethod
    def _require_local_bot_api() -> tuple[str, str]:
        """Require the explicitly configured local Bot API endpoints."""
        base_url = (os.getenv("ARMORED_TELEGRAM_BOT_API_URL") or "").strip()
        file_url = (os.getenv("ARMORED_TELEGRAM_BOT_API_FILE_URL") or "").strip()
        if not base_url or not file_url:
            raise RuntimeError("versioned-republication-requires-local-bot-api-endpoints")
        for endpoint in (base_url, file_url):
            parsed = urlparse(endpoint)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise RuntimeError("versioned-republication-refuses-nonlocal-bot-api")
        return base_url, file_url

    def prepare_republication(
        self,
        *,
        run_id: str,
        execution_id: str,
        execution_version: int,
        canonical_content_id: str,
        prior_publication_message_id: str,
        output_path: Path,
        output_sha256: str,
        idempotency_key: str,
        destination_chat_id: str,
        destination_topic_id: int,
    ):
        """Prepare an additive replay ledger row; the existing receipt is read-only."""
        if self.db is None:
            raise RuntimeError("versioned-republication-requires-database")
        expected_run_id = f"{execution_id}-v{int(execution_version)}:{canonical_content_id}"
        if run_id != expected_run_id:
            raise ValueError("versioned-republication-run-id-mismatch")
        expected_key = (
            f"armoredcreator:reexecution:{execution_id}:v{int(execution_version)}:"
            f"{canonical_content_id}"
        )
        if idempotency_key != expected_key:
            raise ValueError("versioned-republication-idempotency-key-mismatch")
        item = self.db.get(str(canonical_content_id))
        if item is None or item.state != State.PUBLISHED:
            raise ValueError("versioned-republication-requires-published-canonical-item")
        canonical_row = self.db.conn.execute(
            "SELECT duplicate_of_content_id FROM items WHERE content_id=?",
            (str(canonical_content_id),),
        ).fetchone()
        if canonical_row is None or canonical_row["duplicate_of_content_id"]:
            raise ValueError("versioned-republication-refuses-duplicate-item")
        old_receipt = self.db.publication(str(canonical_content_id))
        if (
            old_receipt is None
            or not bool(old_receipt["confirmed"])
            or str(old_receipt["verification_status"]) != "CONFIRMED"
            or str(old_receipt["published_message_id"] or "") != str(prior_publication_message_id)
            or str(old_receipt["destination_chat_id"] or "") != str(destination_chat_id)
            or int(old_receipt["destination_topic_id"] or 0) != int(destination_topic_id)
        ):
            raise ValueError("versioned-republication-prior-receipt-mismatch")
        result = Path(output_path)
        if not result.is_file() or result.stat().st_size <= 0:
            raise ValueError("versioned-republication-output-missing")
        if sha256_file(result).lower() != str(output_sha256).lower():
            raise ValueError("versioned-republication-output-hash-mismatch")
        return self._republication_ledger().prepare(
            run_id=run_id,
            execution_id=execution_id,
            execution_version=execution_version,
            canonical_content_id=canonical_content_id,
            prior_publication_message_id=prior_publication_message_id,
            output_sha256=output_sha256,
            idempotency_key=idempotency_key,
            destination_chat_id=destination_chat_id,
            destination_topic_id=destination_topic_id,
        )

    def preflight_republication(self, run_id: str, output_path: Path | None = None) -> dict:
        """Read-only Hub/Bot API check; never invokes a sending method."""
        if self.db is None:
            raise RuntimeError("versioned-republication-requires-database")
        record = self._republication_ledger().get(run_id)
        if record is None:
            raise KeyError(f"unknown-publication-run:{run_id}")
        if record["status"] != "PREPARED":
            raise RuntimeError("versioned-republication-run-is-not-prepared")
        if output_path is not None:
            result = Path(output_path)
            if not result.is_file() or sha256_file(result).lower() != str(record["output_sha256"]).lower():
                raise ValueError("versioned-republication-output-hash-mismatch")
        base_url, file_url = self._require_local_bot_api()
        token = os.getenv("ARMORED_CREATOR_BOT_TOKEN")
        if not token:
            raise RuntimeError("Telegram Hub exige ARMORED_CREATOR_BOT_TOKEN")
        try:
            from telegram import Bot
            from telegram.request import HTTPXRequest
        except ImportError as exc:
            raise RuntimeError("Dependência python-telegram-bot ausente") from exc

        async def inspect_destination():
            request = HTTPXRequest(
                connection_pool_size=int(os.getenv("ARMORED_TELEGRAM_CONNECTION_POOL_SIZE", "4")),
                connect_timeout=float(os.getenv("ARMORED_TELEGRAM_CONNECT_TIMEOUT", "15")),
                read_timeout=float(os.getenv("ARMORED_TELEGRAM_READ_TIMEOUT", "60")),
                write_timeout=float(os.getenv("ARMORED_TELEGRAM_WRITE_TIMEOUT", "180")),
                pool_timeout=float(os.getenv("ARMORED_TELEGRAM_POOL_TIMEOUT", "15")),
            )
            bot = Bot(token=token, request=request, base_url=base_url, base_file_url=file_url)
            try:
                me = await bot.get_me()
                chat = await bot.get_chat(int(record["destination_chat_id"]))
                member = await bot.get_chat_member(int(record["destination_chat_id"]), me.id)
                status = str(getattr(member, "status", "unknown"))
                if status not in {"administrator", "creator"}:
                    raise RuntimeError("bot-lacks-administrator-status-in-replay-destination")
                if int(record["destination_topic_id"]) > 0 and not bool(getattr(chat, "is_forum", False)):
                    raise RuntimeError("replay-topic-target-is-not-a-forum")
                return {
                    "bot_username": getattr(me, "username", None),
                    "destination_title": getattr(chat, "title", None),
                    "destination_type": getattr(chat, "type", None),
                    "is_forum": bool(getattr(chat, "is_forum", False)),
                    "bot_status": status,
                    "destination_chat_id": str(record["destination_chat_id"]),
                    "destination_topic_id": int(record["destination_topic_id"]),
                    "endpoint": "local-bot-api",
                    "send_called": False,
                }
            finally:
                await bot.shutdown()

        return self._run_async(inspect_destination())

    def publish_republication(
        self,
        run_id: str,
        output_path: Path,
        *,
        explicit_approval: str | None = None,
    ) -> PublicationResult:
        """Send one prepared replay only after a run-specific explicit approval.

        A timeout or crash after SEND_STARTED is never retried automatically;
        the one-shot run must be reconciled externally before any new execution.
        """
        if explicit_approval != f"SEND:{run_id}":
            raise RuntimeError("versioned-republication-requires-explicit-run-approval")
        if os.getenv("ARMORED_HUB_DRY_RUN", "0") == "1":
            raise RuntimeError("ARMORED_HUB_DRY_RUN=1: versioned send blocked")
        if self.db is None:
            raise RuntimeError("versioned-republication-requires-database")
        ledger = self._republication_ledger()
        record = ledger.get(run_id)
        if record is None or record["status"] != "PREPARED":
            raise RuntimeError("versioned-republication-run-is-not-retryable")
        output = Path(output_path)
        if not output.is_file() or output.stat().st_size <= 0:
            raise ValueError("versioned-republication-output-missing")
        if sha256_file(output).lower() != str(record["output_sha256"]).lower():
            raise ValueError("versioned-republication-output-hash-mismatch")
        item = self.db.get(str(record["canonical_content_id"]))
        if item is None or item.state != State.PUBLISHED or not self.db.has_speech_studio_v5_completion(item.content_id):
            raise RuntimeError("versioned-republication-canonical-item-not-ready")
        canonical_row = self.db.conn.execute(
            "SELECT duplicate_of_content_id FROM items WHERE content_id=?",
            (str(item.content_id),),
        ).fetchone()
        old_receipt = self.db.publication(item.content_id)
        if (
            canonical_row is None
            or canonical_row["duplicate_of_content_id"]
            or old_receipt is None
            or not bool(old_receipt["confirmed"])
            or str(old_receipt["verification_status"]) != "CONFIRMED"
            or str(old_receipt["published_message_id"] or "") != str(record["prior_publication_message_id"])
            or str(old_receipt["destination_chat_id"] or "") != str(record["destination_chat_id"])
            or int(old_receipt["destination_topic_id"] or 0) != int(record["destination_topic_id"])
        ):
            raise RuntimeError("versioned-republication-prior-receipt-changed")
        if not item.original_path.is_file() or not item.original_sha256 or sha256_file(item.original_path) != item.original_sha256:
            raise RuntimeError("versioned-republication-original-integrity-failed")
        self._assert_test_destination_lock(item)
        self.preflight_republication(run_id, output)
        self._require_local_bot_api()
        token = os.getenv("ARMORED_CREATOR_BOT_TOKEN")
        base_url, file_url = self._require_local_bot_api()
        try:
            from telegram import Bot
            from telegram.error import NetworkError, TimedOut
            from telegram.request import HTTPXRequest
        except ImportError as exc:
            raise RuntimeError("Dependência python-telegram-bot ausente") from exc
        width, height, duration = self._video_metadata(output)
        ledger.mark_send_started(run_id)

        async def send_once():
            request = HTTPXRequest(
                connection_pool_size=int(os.getenv("ARMORED_TELEGRAM_CONNECTION_POOL_SIZE", "4")),
                connect_timeout=float(os.getenv("ARMORED_TELEGRAM_CONNECT_TIMEOUT", "15")),
                read_timeout=float(os.getenv("ARMORED_TELEGRAM_READ_TIMEOUT", "60")),
                write_timeout=float(os.getenv("ARMORED_TELEGRAM_WRITE_TIMEOUT", "180")),
                pool_timeout=float(os.getenv("ARMORED_TELEGRAM_POOL_TIMEOUT", "15")),
            )
            bot = Bot(token=token, request=request, base_url=base_url, base_file_url=file_url)
            try:
                with output.open("rb") as handle:
                    return await bot.send_video(
                        chat_id=int(record["destination_chat_id"]),
                        message_thread_id=int(record["destination_topic_id"]),
                        video=handle,
                        duration=duration,
                        width=width,
                        height=height,
                        caption=self._publication_text(item),
                        supports_streaming=True,
                    )
            finally:
                await bot.shutdown()

        try:
            message = self._run_async(send_once())
        except Exception as exc:
            ledger.mark_indeterminate(run_id)
            if isinstance(exc, (NetworkError, TimedOut)):
                raise PublicationUnknownError("versioned Telegram send outcome is INDETERMINATE") from exc
            raise PublicationUnknownError("versioned Telegram send started; reconcile before retry") from exc
        message_id = getattr(message, "message_id", None)
        if not message_id or int(message_id) <= 0:
            ledger.mark_indeterminate(run_id)
            raise PublicationUnknownError("versioned Telegram response lacks message ID")
        ledger.mark_message_sent(run_id, str(message_id))
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        topic_id = getattr(message, "message_thread_id", None)
        if str(chat_id) != str(record["destination_chat_id"]) or int(topic_id or 0) != int(record["destination_topic_id"]):
            raise PublicationUnknownError("versioned Telegram message stored SENT_UNVERIFIED; reconcile destination")
        ledger.mark_confirmed(run_id, str(message_id))
        return PublicationResult(True, str(message_id))

    def _telegram_session_path(self) -> Path:
        return (
            self.root / "credentials" / "telegram" / "session" / telegram_session_basename()
        )

    def _resolve_destination_chat_id(self, topic_id: str | int | None = None) -> str | None:
        configured = (os.getenv("ARMORED_CREATOR_GROUP_ID") or "").strip()
        if configured:
            self._destination_chat_id = configured
            return configured

        if self._destination_chat_id:
            return self._destination_chat_id

        topic_value = str(topic_id or (os.getenv("ARMORED_HUB_TOPIC_ID") or "")).strip()
        if not topic_value or not topic_value.lstrip("-").isdigit():
            raise RuntimeError("ARMORED_HUB_TOPIC_ID inválido para descoberta automática")

        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        if not api_id or not api_hash:
            raise RuntimeError(
                "Descoberta automática do grupo exige TELEGRAM_API_ID e TELEGRAM_API_HASH"
            )

        async def discover() -> str | None:
            try:
                from telethon import TelegramClient, functions
            except ImportError as exc:
                raise RuntimeError("Dependência Telethon ausente para descoberta do Hub") from exc

            session = self._telegram_session_path()
            session.parent.mkdir(parents=True, exist_ok=True)
            client = TelegramClient(
                str(session),
                int(api_id),
                api_hash,
                request_retries=0,
                connection_retries=0,
            )
            matches: list[str] = []
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise RuntimeError(
                        "Sessão Telegram do ArmoredSync não está autenticada; "
                        "o Hub não fará login interativo."
                    )
                async for dialog in client.iter_dialogs():
                    entity = getattr(dialog, "entity", None)
                    if entity is None:
                        continue
                    if not bool(getattr(entity, "megagroup", False) or getattr(entity, "forum", False)):
                        continue
                    try:
                        result = await client(
                            functions.messages.GetForumTopicsRequest(
                                peer=entity,
                                q=None,
                                offset_date=None,
                                offset_id=0,
                                offset_topic=0,
                                limit=100,
                            )
                        )
                    except Exception:
                        continue
                    for topic in getattr(result, "topics", []) or []:
                        if str(getattr(topic, "id", "")) == topic_value:
                            matches.append(str(dialog.id))
                            break

                unique_matches = sorted(set(matches))
                if len(unique_matches) == 1:
                    self._destination_chat_id = unique_matches[0]
                    return unique_matches[0]
                if not unique_matches:
                    raise RuntimeError(
                        f"Nenhum grupo-fórum com tópico {topic_value} foi encontrado "
                        "pela sessão Telegram existente"
                    )
                raise RuntimeError(
                    f"Tópico {topic_value} encontrado em múltiplos grupos: "
                    + ", ".join(unique_matches)
                )
            finally:
                if client.is_connected():
                    await client.disconnect()

        return self._run_async(discover())

    @staticmethod
    def _topic_id(message) -> int | None:
        reply_to = getattr(message, "reply_to", None)
        if reply_to is None:
            return None
        value = getattr(reply_to, "reply_to_top_id", None)
        if value is None:
            value = getattr(reply_to, "reply_to_msg_id", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _publication_text(item: Item) -> str:
        links = list(item.affiliate_urls or ())
        if not links and item.affiliate_url:
            links = [str(item.affiliate_url).strip()]
        links = list(dict.fromkeys(str(link).strip() for link in links if str(link).strip()))[:7]
        caption = str(item.publication_caption or "").strip()
        if caption and links:
            text = caption + "\n\n" + "\n".join(links)
        elif caption:
            text = caption
        else:
            text = "\n".join(links)
        if len(text) > 1024:
            raise RuntimeError("Hub caption/publication package excede 1024 caracteres")
        return text

    def _telegram_publication_matches(
        self,
        message,
        item: Item,
        topic_id: int,
        topic_scoped: bool = False,
        allow_unknown_topic: bool = False,
    ) -> bool:
        """Require exact publication text/media and, when available, the expected topic."""
        message_topic = self._topic_id(message)
        if message_topic is not None and message_topic != int(topic_id):
            return False
        if message_topic is None and not (topic_scoped or allow_unknown_topic):
            return False

        caption = str(getattr(message, "message", "") or "").strip()
        if caption != self._publication_text(item):
            return False

        if not getattr(message, "video", None) and not getattr(message, "document", None):
            return False

        return True

    def _find_telegram_publications(self, item: Item) -> list[str] | None:
        """Find an exact publication without trusting incomplete forum metadata.

        A topic-scoped query is authoritative for topic membership. For global
        history/search fallbacks, Telegram may omit reply_to_top_id. In that
        case an exact caption+media match is only a candidate; the candidate's
        message_id is fetched again through _verify_telegram_message(), which
        validates the actual Telegram message before confirmation.
        """
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        record = self._publication(item)
        route = self._route_for(item)
        record_topic = self._publication_field(record, "destination_topic_id")
        topic_id = str(
            record_topic
            if record_topic is not None
            else (route.hub.topic_id if route is not None else (os.getenv("ARMORED_HUB_TOPIC_ID") or ""))
        ).strip()
        if not api_id or not api_hash or not topic_id:
            return None

        try:
            chat_id = (
                str(self._publication_field(record, "destination_chat_id"))
                if record and self._publication_field(record, "destination_chat_id")
                else (str(route.hub.chat_id) if route is not None else self._resolve_destination_chat_id(topic_id))
            )
        except Exception as exc:
            print(
                f"[HUB][VERIFY][UNKNOWN] item={item.content_id} "
                f"destination-resolution: {type(exc).__name__}: {exc}"
            )
            return None
        if not chat_id:
            return None

        affiliate = str(item.affiliate_url or "").strip()
        query = affiliate.rstrip("/").rsplit("/", 1)[-1].strip() if affiliate else ""

        async def find():
            try:
                from telethon import TelegramClient, functions
                from telethon.tl.types import InputMessagesFilterEmpty
            except ImportError:
                return None

            session = self._telegram_session_path()
            session.parent.mkdir(parents=True, exist_ok=True)
            client = TelegramClient(
                str(session),
                int(api_id),
                api_hash,
                request_retries=0,
                connection_retries=0,
            )
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    return None

                entity = await client.get_entity(int(chat_id))
                scoped_matches: list[str] = []
                fallback_candidates: list[str] = []

                async for message in client.iter_messages(
                    entity,
                    limit=int(os.getenv("ARMORED_TELEGRAM_VERIFY_HISTORY_LIMIT", "200")),
                    reply_to=int(topic_id),
                ):
                    if self._telegram_publication_matches(
                        message, item, int(topic_id), topic_scoped=True
                    ):
                        message_id = str(getattr(message, "id", ""))
                        if message_id:
                            scoped_matches.append(message_id)

                if scoped_matches:
                    return sorted(set(scoped_matches))

                if query:
                    async for message in client.iter_messages(
                        entity,
                        search=query,
                        limit=int(os.getenv("ARMORED_TELEGRAM_VERIFY_SEARCH_LIMIT", "100")),
                    ):
                        if self._telegram_publication_matches(
                            message,
                            item,
                            int(topic_id),
                            topic_scoped=False,
                            allow_unknown_topic=True,
                        ):
                            message_id = str(getattr(message, "id", ""))
                            if message_id:
                                fallback_candidates.append(message_id)

                recent_limit = int(os.getenv("ARMORED_TELEGRAM_VERIFY_RECENT_LIMIT", "200"))
                async for message in client.iter_messages(entity, limit=recent_limit):
                    if self._telegram_publication_matches(
                        message,
                        item,
                        int(topic_id),
                        topic_scoped=False,
                        allow_unknown_topic=True,
                    ):
                        message_id = str(getattr(message, "id", ""))
                        if message_id:
                            fallback_candidates.append(message_id)

                candidates = sorted(set(fallback_candidates))
                if len(candidates) > 1:
                    return candidates

                if len(candidates) == 1:
                    # The global search/history result is only a candidate when
                    # Telegram omitted topic metadata. Re-read the exact message
                    # by ID before treating it as proof of publication.
                    return candidates

                if not query:
                    return []

                result = await client(
                    functions.messages.SearchRequest(
                        peer=entity,
                        q=query,
                        from_id=None,
                        top_msg_id=0,
                        filter=InputMessagesFilterEmpty(),
                        min_date=None,
                        max_date=None,
                        offset_id=0,
                        add_offset=0,
                        limit=100,
                        max_id=0,
                        min_id=0,
                        hash=0,
                    )
                )
                for message in getattr(result, "messages", []) or []:
                    if self._telegram_publication_matches(
                        message,
                        item,
                        int(topic_id),
                        topic_scoped=False,
                        allow_unknown_topic=True,
                    ):
                        message_id = str(getattr(message, "id", ""))
                        if message_id:
                            fallback_candidates.append(message_id)

                return sorted(set(fallback_candidates))
            finally:
                if client.is_connected():
                    await client.disconnect()

        try:
            candidates = self._run_async(find())
            count = len(candidates) if candidates is not None else "UNKNOWN"
            print(
                f"[HUB][VERIFY] item={item.content_id} query={query!r} "
                f"topic={topic_id} chat={chat_id} matches={count}"
            )
        except Exception as exc:
            print(
                f"[HUB][VERIFY][UNKNOWN] item={item.content_id} query={query!r}: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

        if candidates is None or len(candidates) != 1:
            return candidates

        # A single unscoped candidate must be verified by its own message ID.
        # This is the positive evidence path when forum reply metadata is absent.
        status = self._verify_telegram_message(str(candidates[0]), item)
        if status is True:
            return [str(candidates[0])]
        if status is False:
            return []
        return None

    def _verify_telegram_message(self, message_id: str, item: Item) -> bool | None:
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        record = self._publication(item)
        route = self._route_for(item)
        record_topic = self._publication_field(record, "destination_topic_id")
        topic_id = str(
            record_topic
            if record_topic is not None
            else (route.hub.topic_id if route is not None else (os.getenv("ARMORED_HUB_TOPIC_ID") or ""))
        ).strip()
        if not message_id or not api_id or not api_hash or not topic_id:
            return None
        try:
            chat_id = (
                str(self._publication_field(record, "destination_chat_id"))
                if record and self._publication_field(record, "destination_chat_id")
                else (str(route.hub.chat_id) if route is not None else self._resolve_destination_chat_id(topic_id))
            )
        except Exception:
            return None
        if not chat_id:
            return None

        async def verify():
            try:
                from telethon import TelegramClient
            except ImportError:
                return None
            session = self._telegram_session_path()
            session.parent.mkdir(parents=True, exist_ok=True)
            client = TelegramClient(str(session), int(api_id), api_hash, request_retries=0, connection_retries=0)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    return None
                message = await client.get_messages(int(chat_id), ids=int(message_id))
                if message is None:
                    return False
                return self._telegram_publication_matches(message, item, int(topic_id), topic_scoped=True)
            finally:
                if client.is_connected():
                    await client.disconnect()

        try:
            result = self._run_async(verify())
            print(f"[HUB][VERIFY] item={item.content_id} message_id={message_id} topic={topic_id} chat={chat_id} result={result}")
            return result
        except Exception as exc:
            print(f"[HUB][VERIFY][UNKNOWN] item={item.content_id} message_id={message_id}: {type(exc).__name__}: {exc}")
            return None

    def publish(self, item: Item) -> PublicationResult:
        self._validate_publication_input(item)
        if self.db is None:
            raise RuntimeError("ArmoredHub exige Database para publicação idempotente")

        existing = self.db.publication(item.item_id)
        if existing and existing["confirmed"]:
            return PublicationResult(True, existing["published_message_id"])

        if existing is None:
            self.db.publication_started(item.item_id)

        output = Path(item.result_path or "")
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("Hub recebeu resultado inexistente/vazio")

        if os.getenv("ARMORED_HUB_DRY_RUN", "0") == "1":
            raise RuntimeError(
                "ARMORED_HUB_DRY_RUN=1: publicação real bloqueada; "
                "nenhum item pode ser marcado como PUBLISHED"
            )

        return self._publish_telegram(item, output)

    @staticmethod
    def _video_metadata(output: Path) -> tuple[int, int, int]:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("Dependência OpenCV ausente para metadados do vídeo") from exc

        capture = cv2.VideoCapture(str(output))
        try:
            if not capture.isOpened():
                raise RuntimeError("Não foi possível abrir o vídeo para leitura de metadados")
            width = int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)))
            height = int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
        finally:
            capture.release()

        if width <= 0 or height <= 0:
            raise RuntimeError("Vídeo possui dimensões inválidas")
        if frame_count <= 0 or fps <= 0:
            raise RuntimeError("Vídeo possui duração inválida")

        duration = max(1, int(round(frame_count / fps)))
        return width, height, duration

    def _publish_telegram(self, item: Item, output: Path) -> PublicationResult:
        token = os.getenv("ARMORED_CREATOR_BOT_TOKEN")
        if not token:
            raise RuntimeError("Telegram Hub exige ARMORED_CREATOR_BOT_TOKEN")
        self._assert_test_destination_lock(item)
        chat_id, topic_id = self._destination_for(item)
        width, height, duration = self._video_metadata(output)
        self.db.publication_started(
            item.item_id,
            destination_chat_id=chat_id,
            destination_topic_id=int(topic_id),
        )

        try:
            from telegram import Bot
            from telegram.error import NetworkError, TimedOut
            from telegram.request import HTTPXRequest
        except ImportError as exc:
            raise RuntimeError(
                "Dependência python-telegram-bot ausente; instale as dependências do Hub."
            ) from exc

        def make_bot():
            request = HTTPXRequest(
                connection_pool_size=int(os.getenv("ARMORED_TELEGRAM_CONNECTION_POOL_SIZE", "4")),
                connect_timeout=float(os.getenv("ARMORED_TELEGRAM_CONNECT_TIMEOUT", "15")),
                read_timeout=float(os.getenv("ARMORED_TELEGRAM_READ_TIMEOUT", "60")),
                write_timeout=float(os.getenv("ARMORED_TELEGRAM_WRITE_TIMEOUT", "180")),
                pool_timeout=float(os.getenv("ARMORED_TELEGRAM_POOL_TIMEOUT", "15")),
            )
            bot_api_url = (os.getenv("ARMORED_TELEGRAM_BOT_API_URL") or "").strip()
            bot_api_file_url = (os.getenv("ARMORED_TELEGRAM_BOT_API_FILE_URL") or "").strip()
            bot_kwargs = {"request": request}
            if bot_api_url:
                bot_kwargs["base_url"] = bot_api_url
            if bot_api_file_url:
                bot_kwargs["base_file_url"] = bot_api_file_url
            return Bot(token=token, **bot_kwargs)

        async def preflight():
            # Validate the configured Telegram Bot API endpoint before entering
            # the durable send side-effect window. A refused local Bot API
            # connection must remain retryable, not SENT_UNVERIFIED forever.
            bot = make_bot()
            try:
                return await bot.get_me()
            finally:
                await bot.shutdown()

        async def send():
            bot = make_bot()
            try:
                with output.open("rb") as handle:
                    return await bot.send_video(
                        chat_id=int(chat_id),
                        message_thread_id=int(topic_id),
                        video=handle,
                        duration=duration,
                        width=width,
                        height=height,
                        caption=self._publication_text(item),
                        supports_streaming=True,
                    )
            finally:
                await bot.shutdown()

        try:
            self._run_async(preflight())
        except (TimedOut, NetworkError) as exc:
            raise RuntimeError(
                "Telegram Bot API indisponível no preflight; o envio do vídeo não foi iniciado"
            ) from exc

        self.db.publication_send_started(item.item_id)

        try:
            message = self._run_async(send())
        except (TimedOut, NetworkError) as exc:
            status = self.check_publication(item)
            print(
                f"[HUB][TELEGRAM][SEND_ERROR] item={item.content_id} "
                f"exception={type(exc).__name__} message={str(exc)!r} "
                f"reconciliation={status.value} decision="
                f"{'CONFIRMED' if status == PublicationCheck.CONFIRMED else 'UNKNOWN'}"
            )
            if status == PublicationCheck.CONFIRMED:
                publication = self._publication(item)
                message_id = publication["published_message_id"] if publication else None
                if message_id:
                    return PublicationResult(True, str(message_id))
            raise PublicationUnknownError("Telegram publication outcome is UNKNOWN") from exc

        message_id = getattr(message, "message_id", None)
        if message_id is None:
            raise RuntimeError("Telegram não retornou message_id")

        self.db.publication_message_sent(item.item_id, str(message_id))

        if os.getenv("ARMORED_TEST_CRASH_AFTER_TELEGRAM_SEND", "0") == "1":
            raise RuntimeError("TEST_CRASH_AFTER_TELEGRAM_SEND")

        self.db.publication_confirmed(item.item_id, str(message_id))
        return PublicationResult(True, str(message_id))


def build(root: Path, db: Database | None = None, **kwargs):
    return ArmoredHub(root, db, routes=kwargs.get("routes", ()))
