from __future__ import annotations
import inspect
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol
from .database import Database
from .models import Item, PublicationCheck, State
from .integrity import sha256_file


class VisionUnresolvedError(RuntimeError):
    """Vision V1 could not resolve the product with enough certainty."""


class VisionDuplicateError(RuntimeError):
    """An exact product identity is already approved; skip external lookup."""

    def __init__(self, owner_content_id: str) -> None:
        self.owner_content_id = str(owner_content_id)
        super().__init__(f"canonical-product-already-represented:{self.owner_content_id}")


class PublicationUnknownError(RuntimeError):
    """Telegram publication outcome is unresolved and must enter recovery.

    This is deliberately distinct from a normal pipeline failure: a timeout
    can happen after Telegram has accepted the upload, so the item must remain
    recoverable and must never be treated as a terminal FAILED item.
    """


class PublicationDeferredError(RuntimeError):
    """Publication was intentionally disabled; durable media is ready to resume."""

from .storage import Storage

@dataclass(frozen=True)
class VisionResult:
    affiliate_name: str
    affiliate_url: str
    affiliate_urls: tuple[str, ...] = ()
    publication_caption: str | None = None
    ia_context: dict[str, Any] | None = None

@dataclass(frozen=True)
class StudioResult:
    working_path: Path | None
    result_path: Path

@dataclass(frozen=True)
class PublicationResult:
    confirmed: bool
    message_id: str | None

class VisionService(Protocol):
    def identify(self, item: Item) -> VisionResult: ...

class StudioService(Protocol):
    def process(self, item: Item) -> StudioResult: ...

class AIService(Protocol):
    def generate_caption(self, context: dict[str, Any]) -> str: ...

class Publisher(Protocol):
    def check_publication(self, item: Item) -> PublicationCheck: ...
    def publish(self, item: Item) -> PublicationResult: ...

@dataclass(frozen=True)
class IngestMessage:
    telegram_message_id: str
    source_id: str = "telegram"
    topic_id: int | None = None
    topic_name: str | None = None
    original_url: str | None = None
    source_path: Path | None = None
    materialize: Callable[[Path], None] | Callable[[Path], Awaitable[None]] | None = None

class SyncService:
    def __init__(self, db: Database, storage: Storage) -> None:
        self.db, self.storage = db, storage

    def _prepare_ingest(self, message: IngestMessage):
        if message.source_path is None and message.materialize is None:
            raise ValueError("ingest-message-requires-source-path-or-materializer")
        existing = self.db.conn.execute(
            "SELECT content_id, original_path, original_url FROM items WHERE source_id=? AND telegram_message_id=?",
            (message.source_id, message.telegram_message_id),
        ).fetchone()
        if existing:
            item_id = str(existing["content_id"])
            stored_path = str(existing["original_path"] or "").strip()
            if stored_path:
                original = Path(stored_path)
                if not original.name:
                    stored_path = ""
            if not stored_path:
                # Repair legacy rows deterministically from the stable Telegram ID.
                original = self.storage.original(
                    item_id,
                    ".mp4",
                    original_url=existing["original_url"] or message.original_url,
                    source_id=message.source_id,
                )
                self.storage.preserve_artifacts(list(original.parent.glob("*.part")),
                    workspace=original.parent, content_id=item_id, reason="legacy-incomplete-download")
                self.db.repair_original_path(item_id, original)
            partial = original.with_suffix(original.suffix + ".part")
            return item_id, original, partial
        suffix = (
            message.source_path.suffix
            if message.source_path is not None and message.source_path.suffix
            else ".mp4"
        )
        item_id = self.db.content_id_for(message.telegram_message_id, message.source_id)
        original = self.storage.original(item_id, suffix, original_url=message.original_url, source_id=message.source_id)
        self.db.reserve_item(
            message.telegram_message_id,
            source_id=message.source_id,
            topic_id=message.topic_id,
            topic_name=message.topic_name,
            original_url=message.original_url,
            original_path=original,
        )
        partial = original.with_suffix(original.suffix + ".part")
        return item_id, original, partial

    @staticmethod
    def _sha256(path: Path) -> str:
        return sha256_file(path)

    def _finish_ingest(self, content_id: str, original: Path, partial: Path) -> int:
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise IOError("original-materialization-empty")
        partial.replace(original)
        self.db.finalize_original_path(content_id, original, self._sha256(original))
        return content_id

    def _reuse_existing_original(self, content_id: str, original: Path) -> bool:
        """Reuse an existing original only after checking its durable hash.

        A previous process may have completed the atomic rename but failed
        before persisting the SHA-256. Preserve those bytes and finish the
        metadata write on retry instead of deleting or downloading over them.
        """
        if not original.exists():
            return False
        if not original.is_file() or original.stat().st_size <= 0:
            raise IOError(f"existing-original-is-not-a-nonempty-file:{original}")

        before = original.stat()
        digest = self._sha256(original)
        after = original.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise IOError(f"existing-original-changed-during-hash:{original}")

        row = self.db.conn.execute(
            "SELECT original_sha256 FROM items WHERE content_id=?",
            (str(content_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"existing-original-has-no-item-row:{content_id}")
        expected = str(row["original_sha256"] or "").strip().lower()
        if expected and digest.lower() != expected:
            raise IOError(f"existing-original-hash-mismatch:{content_id}")
        if not expected:
            self.db.finalize_original_path(content_id, original, digest)
        return True

    def _cleanup_ingest_files(self, original: Path, partial: Path, *, content_id: str | None = None) -> None:
        # The final path may already contain a complete original if a failure
        # occurred after the atomic rename but before SQLite committed its
        # hash. Keep it for an idempotent retry; archive unfinished bytes too.
        if partial.exists() or partial.is_symlink():
            self.storage.preserve_artifacts([partial], workspace=partial.parent,
                content_id=content_id, reason="interrupted-original-download")

    def reserve_message(self, message: IngestMessage) -> str:
        """Reserve one candidate in SQLite without downloading its media."""
        if message.source_path is None and message.materialize is None:
            raise ValueError("ingest-message-requires-source-path-or-materializer")
        existing = self.db.conn.execute(
            "SELECT content_id, original_path, original_url, state, affiliate_url "
            "FROM items WHERE source_id=? AND telegram_message_id=?",
            (message.source_id, message.telegram_message_id),
        ).fetchone()
        if existing:
            item_id = str(existing["content_id"])
            stored_url = str(existing["original_url"] or "").strip()
            incoming_url = str(message.original_url or "").strip()
            if not stored_url and incoming_url:
                duplicate_owner = self.db.update_original_url(item_id, incoming_url)
                if (
                    duplicate_owner is None
                    and str(existing["state"]) == State.WAITING_VISION.value
                    and not str(existing["affiliate_url"] or "").strip()
                ):
                    self.db.transition(
                        item_id,
                        State.RECEIVED,
                        "source-link-discovered-after-waiting-vision",
                    )
            if not str(existing["original_path"] or "").strip():
                original = self.storage.original(
                    item_id,
                    ".mp4",
                    original_url=stored_url or incoming_url,
                    source_id=message.source_id,
                )
                self.db.repair_original_path(item_id, original)
            return item_id
        suffix = message.source_path.suffix if message.source_path is not None and message.source_path.suffix else ".mp4"
        item_id = self.db.content_id_for(message.telegram_message_id, message.source_id)
        original = self.storage.original(item_id, suffix, original_url=message.original_url, source_id=message.source_id)
        self.db.reserve_item(message.telegram_message_id, source_id=message.source_id, topic_id=message.topic_id,
                             topic_name=message.topic_name, original_url=message.original_url, original_path=original)
        return item_id

    async def materialize_message_async(self, message: IngestMessage) -> int:
        """Materialize an already-reserved candidate into its immutable path."""
        return await self.ingest_message_async(message)

    def ingest_message(self, message: IngestMessage) -> int:
        item_id, original, partial = self._prepare_ingest(message)
        if self._reuse_existing_original(item_id, original):
            return item_id
        try:
            self._cleanup_ingest_files(original, partial, content_id=item_id)
            if message.materialize is not None:
                result = message.materialize(partial)
                if inspect.isawaitable(result):
                    raise TypeError("async-materializer-requires-ingest-message-async")
            else:
                source = message.source_path.resolve()
                if not source.is_file():
                    raise FileNotFoundError(source)
                shutil.copy2(source, partial)
            return self._finish_ingest(item_id, original, partial)
        except Exception:
            self._cleanup_ingest_files(original, partial, content_id=item_id)
            self.db.rollback_ingest()
            raise

    async def ingest_message_async(self, message: IngestMessage) -> int:
        item_id, original, partial = self._prepare_ingest(message)
        if self._reuse_existing_original(item_id, original):
            return item_id
        try:
            self._cleanup_ingest_files(original, partial, content_id=item_id)
            if message.materialize is not None:
                result = message.materialize(partial)
                if inspect.isawaitable(result):
                    await result
            else:
                source = message.source_path.resolve()
                if not source.is_file():
                    raise FileNotFoundError(source)
                shutil.copy2(source, partial)
            return self._finish_ingest(item_id, original, partial)
        except Exception:
            self._cleanup_ingest_files(original, partial, content_id=item_id)
            self.db.rollback_ingest()
            raise

    def ingest(
        self,
        source: Path,
        telegram_message_id: str,
        source_id: str = "telegram",
        topic_id: int | None = None,
        topic_name: str | None = None,
        original_url: str | None = None,
    ) -> int:
        return self.ingest_message(
            IngestMessage(
                telegram_message_id=telegram_message_id,
                source_id=source_id,
                topic_id=topic_id,
                topic_name=topic_name,
                original_url=original_url,
                source_path=Path(source),
            )
        )
