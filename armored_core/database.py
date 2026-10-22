from __future__ import annotations
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .models import Item, State
from .integrity import sha256_file

class Database:
    def __init__(self, path: Path, *, initialize: bool = True) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        if initialize:
            self._init()

    def initialize(self) -> None:
        """Initialize/migrate the schema after a staged runtime lease is held."""
        self._init()

    def _init(self) -> None:
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS items (
            content_id TEXT PRIMARY KEY,
            telegram_message_id TEXT NOT NULL,
            source_id TEXT NOT NULL DEFAULT 'telegram',
            topic_id INTEGER,
            topic_name TEXT,
            original_url TEXT,
            state TEXT NOT NULL,
            original_path TEXT NOT NULL,
            original_sha256 TEXT,
            working_path TEXT,
            result_path TEXT,
            affiliate_name TEXT,
            affiliate_url TEXT,
            publication_caption TEXT,
            affiliate_urls_json TEXT NOT NULL DEFAULT '[]',
            attempts INTEGER NOT NULL DEFAULT 0,
            recovery_count INTEGER NOT NULL DEFAULT 0,
            cleanup_completed INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            duplicate_of_content_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS state_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id TEXT NOT NULL,
            old_state TEXT,
            new_state TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS product_identity_claims (
            product_key TEXT PRIMARY KEY,
            owner_content_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS product_identity_claim_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vision_product_lookup_events (
            lookup_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_key TEXT NOT NULL,
            content_id TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('OFFER_FOUND','OFFER_ABSENT')),
            checked_at REAL NOT NULL,
            suppress_until REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_vision_product_lookup_key_id
            ON vision_product_lookup_events(product_key, lookup_id DESC);
        CREATE TABLE IF NOT EXISTS publications (
            content_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            published_message_id TEXT,
            confirmed INTEGER NOT NULL DEFAULT 0,
            verification_status TEXT NOT NULL DEFAULT 'PENDING',
            destination_chat_id TEXT,
            destination_topic_id INTEGER,
            verified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sync_topics (
            topic_id INTEGER PRIMARY KEY,
            topic_name TEXT NOT NULL,
            last_seen_message_id INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sync_source_topics (
            source_id TEXT NOT NULL,
            topic_id INTEGER NOT NULL,
            topic_name TEXT NOT NULL,
            last_seen_message_id INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(source_id, topic_id)
        );
        CREATE TABLE IF NOT EXISTS sync_sources (
            source_id TEXT PRIMARY KEY,
            historical_complete INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS caption_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            candidate_index INTEGER NOT NULL,
            caption TEXT NOT NULL,
            policy_valid INTEGER NOT NULL,
            rejection_reason TEXT,
            score REAL NOT NULL DEFAULT 0,
            selected INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(content_id, batch_id, candidate_index)
        );
        CREATE TABLE IF NOT EXISTS ia_generation_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT,
            status TEXT NOT NULL,
            error_type TEXT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_caption_candidates_content
            ON caption_candidates(content_id, created_at);
        CREATE TABLE IF NOT EXISTS runtime_locks (
            name TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        );
        """)
        self.conn.commit()
        self._migrate_columns()
        self._migrate_telegram_identity()
        # Non-unique expression index accelerates conservative URL identity checks.
        # It is intentionally not UNIQUE: historic collisions are evidence to audit,
        # not rows to delete or prevent schema migration.
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_normalized_original_url "
            "ON items(LOWER(TRIM(original_url)))"
        )
        self.conn.commit()

    def _migrate_columns(self) -> None:
        # Legacy Vision evidence tables, when present in an existing database,
        # are preserved. Migrations must never destroy historical audit data.
        migrations = {
            "items": [
                ("source_id", "ALTER TABLE items ADD COLUMN source_id TEXT NOT NULL DEFAULT 'telegram'"),
                ("topic_id", "ALTER TABLE items ADD COLUMN topic_id INTEGER"),
                ("topic_name", "ALTER TABLE items ADD COLUMN topic_name TEXT"),
                ("original_url", "ALTER TABLE items ADD COLUMN original_url TEXT"),
                ("original_sha256", "ALTER TABLE items ADD COLUMN original_sha256 TEXT"),
                ("publication_caption", "ALTER TABLE items ADD COLUMN publication_caption TEXT"),
                ("ia_context_json", "ALTER TABLE items ADD COLUMN ia_context_json TEXT NOT NULL DEFAULT '{}'"),
                ("affiliate_urls_json", "ALTER TABLE items ADD COLUMN affiliate_urls_json TEXT NOT NULL DEFAULT '[]'"),
                ("attempts", "ALTER TABLE items ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"),
                ("recovery_count", "ALTER TABLE items ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0"),
                ("cleanup_completed", "ALTER TABLE items ADD COLUMN cleanup_completed INTEGER NOT NULL DEFAULT 0"),
                ("duplicate_of_content_id", "ALTER TABLE items ADD COLUMN duplicate_of_content_id TEXT"),
            ],
            "publications": [
                ("verification_status", "ALTER TABLE publications ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'PENDING'"),
                ("destination_chat_id", "ALTER TABLE publications ADD COLUMN destination_chat_id TEXT"),
                ("destination_topic_id", "ALTER TABLE publications ADD COLUMN destination_topic_id INTEGER"),
                ("verified_at", "ALTER TABLE publications ADD COLUMN verified_at TEXT"),
            ],
        }
        for table, columns in migrations.items():
            existing = {
                row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, sql in columns:
                if name not in existing:
                    self.conn.execute(sql)
        self.conn.commit()

    def _migrate_telegram_identity(self) -> None:
        """Replace global Telegram message uniqueness with source-scoped identity."""
        indexes = self.conn.execute("PRAGMA index_list(items)").fetchall()
        legacy_unique = False
        for row in indexes:
            if int(row["unique"] or 0) != 1:
                continue
            cols = self.conn.execute(f'PRAGMA index_info("{row["name"]}")').fetchall()
            if [str(col["name"]) for col in cols] == ["telegram_message_id"]:
                legacy_unique = True
                break
        if legacy_unique:
            self.conn.execute("""
                CREATE TABLE items_new (
                    content_id TEXT PRIMARY KEY,
                    telegram_message_id TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT 'telegram',
                    topic_id INTEGER,
                    topic_name TEXT,
                    original_url TEXT,
                    state TEXT NOT NULL,
                    original_path TEXT NOT NULL,
                    original_sha256 TEXT,
                    working_path TEXT,
                    result_path TEXT,
                    affiliate_name TEXT,
                    affiliate_url TEXT,
                    publication_caption TEXT,
                    ia_context_json TEXT NOT NULL DEFAULT '{}',
                    affiliate_urls_json TEXT NOT NULL DEFAULT '[]',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    cleanup_completed INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    duplicate_of_content_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            columns = [
                "content_id","telegram_message_id","source_id","topic_id","topic_name",
                "original_url","state","original_path","original_sha256","working_path",
                "result_path","affiliate_name","affiliate_url","publication_caption",
                "ia_context_json","affiliate_urls_json","attempts","recovery_count",
                "cleanup_completed","last_error","duplicate_of_content_id","created_at","updated_at"
            ]
            self.conn.execute(
                f"INSERT INTO items_new ({','.join(columns)}) SELECT {','.join(columns)} FROM items"
            )
            self.conn.execute("DROP TABLE items")
            self.conn.execute("ALTER TABLE items_new RENAME TO items")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_items_source_message "
            "ON items(source_id, telegram_message_id)"
        )
        self.conn.commit()

    @staticmethod
    def content_id_for(telegram_message_id: str, source_id: str = "telegram") -> str:
        source = str(source_id)
        message = str(telegram_message_id)
        if source in {"telegram", "local"}:
            return message
        return f"{source}_{message}"

    def initialize_source_state(self, source_id: str, legacy_source_id: str | None = None) -> None:
        """Initialize a source without resetting the approved legacy Source 1 state."""
        source_id = str(source_id)
        exists = self.conn.execute(
            "SELECT 1 FROM sync_sources WHERE source_id=?",
            (source_id,),
        ).fetchone()
        if exists:
            return
        legacy = legacy_source_id is not None and source_id == str(legacy_source_id)
        historical_complete = 1 if legacy and self.historical_complete() else 0
        self.conn.execute(
            "INSERT INTO sync_sources(source_id,historical_complete) VALUES(?,?)",
            (source_id, historical_complete),
        )
        if legacy:
            rows = self.conn.execute(
                "SELECT topic_id,topic_name,last_seen_message_id FROM sync_topics"
            ).fetchall()
            for row in rows:
                self.conn.execute(
                    "INSERT OR IGNORE INTO sync_source_topics("
                    "source_id,topic_id,topic_name,last_seen_message_id"
                    ") VALUES(?,?,?,?)",
                    (source_id, int(row["topic_id"]), str(row["topic_name"]), int(row["last_seen_message_id"])),
                )
        self.conn.commit()

    def source_sync_mode(self, source_id: str) -> str:
        row = self.conn.execute(
            "SELECT historical_complete FROM sync_sources WHERE source_id=?",
            (str(source_id),),
        ).fetchone()
        return "LIVE" if row and int(row["historical_complete"]) else "CATCH_UP"

    def complete_source_historical_sync(self, source_id: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_sources(source_id,historical_complete) VALUES(?,1) "
            "ON CONFLICT(source_id) DO UPDATE SET historical_complete=1, updated_at=CURRENT_TIMESTAMP",
            (str(source_id),),
        )
        self.conn.commit()

    def reset_source_historical_sync(self, source_id: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_sources(source_id,historical_complete) VALUES(?,0) "
            "ON CONFLICT(source_id) DO UPDATE SET historical_complete=0, updated_at=CURRENT_TIMESTAMP",
            (str(source_id),),
        )
        self.conn.commit()

    def all_sources_historical_complete(self, source_ids) -> bool:
        ids = [str(value) for value in source_ids]
        if not ids:
            return False
        rows = self.conn.execute(
            f"SELECT source_id,historical_complete FROM sync_sources "
            f"WHERE source_id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        return len(rows) == len(ids) and all(int(row["historical_complete"]) == 1 for row in rows)

    def source_sync_topic_checkpoint(self, source_id: str, topic_id: int) -> int:
        row = self.conn.execute(
            "SELECT last_seen_message_id FROM sync_source_topics WHERE source_id=? AND topic_id=?",
            (str(source_id), int(topic_id)),
        ).fetchone()
        return int(row["last_seen_message_id"]) if row else 0

    def set_source_sync_topic_checkpoint(self, source_id: str, topic_id: int, topic_name: str, message_id: int) -> None:
        self.conn.execute(
            "INSERT INTO sync_source_topics(source_id,topic_id,topic_name,last_seen_message_id) VALUES(?,?,?,?) "
            "ON CONFLICT(source_id,topic_id) DO UPDATE SET topic_name=excluded.topic_name, "
            "last_seen_message_id=MAX(sync_source_topics.last_seen_message_id, excluded.last_seen_message_id), "
            "updated_at=CURRENT_TIMESTAMP",
            (str(source_id), int(topic_id), str(topic_name), int(message_id)),
        )
        self.conn.commit()

    def sync_mode(self) -> str:
        row = self.conn.execute(
            "SELECT value FROM sync_state WHERE key='mode'"
        ).fetchone()
        return str(row["value"]) if row else "CATCH_UP"

    def set_sync_mode(self, mode: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_state(key,value) VALUES('mode',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (str(mode),),
        )
        self.conn.commit()

    def historical_complete(self) -> bool:
        return self.sync_mode() == "LIVE"

    def has_sync_checkpoints(self) -> bool:
        row = self.conn.execute("SELECT 1 FROM sync_topics LIMIT 1").fetchone()
        return row is not None

    def complete_historical_sync(self) -> None:
        self.set_sync_mode("LIVE")

    def sync_topic_checkpoint(self, topic_id: int) -> int:
        row = self.conn.execute(
            "SELECT last_seen_message_id FROM sync_topics WHERE topic_id=?",
            (int(topic_id),),
        ).fetchone()
        return int(row["last_seen_message_id"]) if row else 0

    def set_sync_topic_checkpoint(self, topic_id: int, topic_name: str, message_id: int) -> None:
        self.conn.execute(
            "INSERT INTO sync_topics(topic_id,topic_name,last_seen_message_id) VALUES(?,?,?) "
            "ON CONFLICT(topic_id) DO UPDATE SET topic_name=excluded.topic_name, "
            "last_seen_message_id=MAX(sync_topics.last_seen_message_id, excluded.last_seen_message_id), "
            "updated_at=CURRENT_TIMESTAMP",
            (int(topic_id), str(topic_name), int(message_id)),
        )
        self.conn.commit()

    def reserve_item(
        self,
        telegram_message_id: str,
        source_id: str = "telegram",
        topic_id: int | None = None,
        topic_name: str | None = None,
        original_url: str | None = None,
        original_path: Path | None = None,
    ) -> str:
        """Atomically reserve message identity and suppress global raw-URL duplicates.

        Existing (source_id, telegram_message_id) remains the idempotency key.
        A new source message with a raw URL already represented by another row
        is retained as DUPLICATE, linked to its durable owner, and cannot enter
        Vision/Stock. Existing historic rows are never rewritten by this check.
        """
        source = str(source_id)
        message = str(telegram_message_id)
        url = str(original_url or "").strip() or None
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self.conn.execute(
                "SELECT content_id FROM items WHERE source_id=? AND telegram_message_id=?",
                (source, message),
            ).fetchone()
            if existing:
                self.conn.commit()
                return str(existing["content_id"])

            content_id = self.content_id_for(message, source)
            duplicate_owner = None
            if url:
                prior = self.conn.execute(
                    "SELECT content_id, duplicate_of_content_id "
                    "FROM items WHERE LOWER(TRIM(original_url))=LOWER(TRIM(?)) "
                    "ORDER BY CASE WHEN state='DUPLICATE' THEN 1 ELSE 0 END, "
                    "created_at, content_id LIMIT 1",
                    (url,),
                ).fetchone()
                if prior:
                    duplicate_owner = str(
                        prior["duplicate_of_content_id"] or prior["content_id"]
                    )
                    if duplicate_owner == content_id:
                        duplicate_owner = None

            state = State.DUPLICATE if duplicate_owner else State.RECEIVED
            reason = (
                f"duplicate-url-global:{duplicate_owner}"
                if duplicate_owner
                else "ingest-reserved"
            )
            self.conn.execute(
                "INSERT INTO items (content_id,telegram_message_id,source_id,topic_id,"
                "topic_name,original_url,state,original_path,duplicate_of_content_id,last_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    content_id, message, source, topic_id, topic_name, url,
                    state.value, str(original_path or ""), duplicate_owner,
                    reason if duplicate_owner else None,
                ),
            )
            self.conn.execute(
                "INSERT INTO state_events(content_id,old_state,new_state,reason) "
                "VALUES (?,NULL,?,?)",
                (content_id, state.value, reason),
            )
            self.conn.commit()
            return content_id
        except BaseException:
            self.conn.rollback()
            raise

    def update_original_url(self, item_id: str, original_url: str) -> str | None:
        """Enrich the same message reservation without reopening a duplicate URL.

        Return the existing URL owner if the newly discovered URL is already
        represented elsewhere. The existing message row is retained and marked
        DUPLICATE atomically; no second reservation or historical deletion occurs.
        """
        value = str(original_url or "").strip()
        if not value:
            return None
        content_id = str(item_id)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            current = self.conn.execute(
                "SELECT state, original_url, affiliate_url FROM items WHERE content_id=?",
                (content_id,),
            ).fetchone()
            if current is None:
                self.conn.commit()
                return None
            if str(current["original_url"] or "").strip():
                self.conn.commit()
                return None

            prior = self.conn.execute(
                "SELECT content_id, duplicate_of_content_id, state FROM items "
                "WHERE content_id<>? AND LOWER(TRIM(original_url))=LOWER(TRIM(?)) "
                "ORDER BY CASE WHEN state='DUPLICATE' THEN 1 ELSE 0 END, "
                "created_at, content_id LIMIT 1",
                (content_id, value),
            ).fetchone()
            owner = None
            if (
                prior is not None
                and not str(current["affiliate_url"] or "").strip()
                and str(current["state"]) != State.DUPLICATE.value
            ):
                owner = str(prior["duplicate_of_content_id"] or prior["content_id"])
                if owner == content_id:
                    owner = None

            if owner:
                reason = f"duplicate-url-discovered-after-reservation:{owner}"
                old_state = str(current["state"])
                self.conn.execute(
                    "UPDATE items SET original_url=?,state=?,duplicate_of_content_id=?,last_error=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
                    (value, State.DUPLICATE.value, owner, reason, content_id),
                )
                self.conn.execute(
                    "INSERT INTO state_events(content_id,old_state,new_state,reason) VALUES(?,?,?,?)",
                    (content_id, old_state, State.DUPLICATE.value, reason),
                )
                self.conn.commit()
                return owner

            self.conn.execute(
                "UPDATE items SET original_url=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE content_id=? AND (original_url IS NULL OR TRIM(original_url)='')",
                (value, content_id),
            )
            self.conn.commit()
            return None
        except BaseException:
            self.conn.rollback()
            raise

    def repair_original_path(self, item_id: str, path: Path) -> None:
        """Repair a legacy/incomplete row without changing its pipeline state."""
        self.conn.execute(
            "UPDATE items SET original_path=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (str(path), str(item_id)),
        )
        self.conn.commit()

    def finalize_original_path(self, item_id: str, path: Path, sha256: str) -> None:
        self.conn.execute(
            "UPDATE items SET original_path=?, original_sha256=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (str(path), sha256, item_id),
        )
        self.conn.commit()

    def rollback_ingest(self) -> None:
        self.conn.rollback()

    def create_item(
        self, telegram_message_id: str, original_path: Path,
        source_id: str = "telegram", topic_id: int | None = None,
        topic_name: str | None = None, original_url: str | None = None,
    ) -> str:
        """Compatibility alias that obeys the same atomic ingest identity rules."""
        return self.reserve_item(
            telegram_message_id,
            source_id=source_id,
            topic_id=topic_id,
            topic_name=topic_name,
            original_url=original_url,
            original_path=original_path,
        )

    @staticmethod
    def _optional_path(value) -> Path | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() == "none":
            return None
        return Path(text)

    def get(self, item_id: str) -> Item:
        content_id = str(item_id)
        row = self.conn.execute("SELECT * FROM items WHERE content_id=?", (content_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return Item(
            row["content_id"], row["telegram_message_id"], State(row["state"]),
            Path(row["original_path"]).parent, Path(row["original_path"]),
            self._optional_path(row["working_path"]),
            self._optional_path(row["result_path"]),
            row["affiliate_name"], row["affiliate_url"],
            row["source_id"], row["original_url"], row["topic_id"], row["topic_name"],
            row["original_sha256"], row["attempts"], row["recovery_count"], bool(row["cleanup_completed"]),
            row["publication_caption"],
            tuple(json.loads(row["affiliate_urls_json"] or "[]")),
            json.loads(row["ia_context_json"] or "{}"),
            row["duplicate_of_content_id"],
        )

    def pending_vision_approved_items(self) -> list[Item]:
        """Return all durable Vision-approved RECEIVED rows for staged catch-up.

        Some rows may already have an ORIGINAL because the process stopped
        between materialization and pipeline execution. Returning those too is
        essential: discovery suppresses represented URLs, so they must be
        resumed from SQLite rather than rediscovered from Telegram.
        """
        rows = self.conn.execute(
            "SELECT content_id FROM items "
            "WHERE state=? AND affiliate_url IS NOT NULL AND TRIM(affiliate_url)<>'' "
            "ORDER BY source_id, COALESCE(topic_id, 0), "
            "CAST(telegram_message_id AS INTEGER), created_at, content_id",
            (State.RECEIVED.value,),
        ).fetchall()
        return [self.get(str(row["content_id"])) for row in rows]

    def pre_download_recovery_items(self) -> list[Item]:
        """Return RECOVERY rows that violate the immutable-ORIGINAL invariant."""
        rows = self.conn.execute(
            "SELECT content_id FROM items WHERE state=? "
            "ORDER BY source_id, COALESCE(topic_id, 0), "
            "CAST(telegram_message_id AS INTEGER), created_at, content_id",
            (State.RECOVERY.value,),
        ).fetchall()
        items = [self.get(str(row["content_id"])) for row in rows]
        return [item for item in items if not item.original_path.is_file()]

    def vision_approved_interrupted_items(self) -> list[Item]:
        """Find Vision rows where evidence was committed before a crash.

        Pipeline persists affiliate_url before transitioning VISION back to
        RECEIVED in pre-download mode. A process crash between those commits
        must not strand the approved candidate outside both Vision and Stock.
        """
        rows = self.conn.execute(
            "SELECT content_id FROM items WHERE state=? "
            "AND affiliate_url IS NOT NULL AND TRIM(affiliate_url)<>'' "
            "ORDER BY source_id, COALESCE(topic_id, 0), "
            "CAST(telegram_message_id AS INTEGER), created_at, content_id",
            (State.VISION.value,),
        ).fetchall()
        return [self.get(str(row["content_id"])) for row in rows]

    def pending_waiting_vision_retries(
        self,
        *,
        limit: int | None = 100,
        content_ids: list[str] | tuple[str, ...] | None = None,
    ) -> list[Item]:
        """Return unresolved Vision rows with a source URL and no approval.

        Includes WAITING_VISION decisions and VISION rows left behind by an
        interrupted/technical attempt. Positive limits are capped at 1000.
        ``limit=None`` or ``limit=0`` snapshots every eligible row unless an
        explicit content-id allowlist is supplied. Rows without source URLs and
        rows already approved remain untouched. Allowlist batches are capped at
        1000 IDs to prevent accidental unbounded recovery calls.
        """
        query = (
            "SELECT content_id FROM items "
            "WHERE state IN (?, ?) AND original_url IS NOT NULL AND TRIM(original_url)<>'' "
            "AND (affiliate_url IS NULL OR TRIM(affiliate_url)='') "
        )
        retry_states = (State.WAITING_VISION.value, State.VISION.value)
        params: tuple = retry_states
        if content_ids is not None:
            unique_ids = list(dict.fromkeys(str(value).strip() for value in content_ids if str(value).strip()))
            if not unique_ids:
                return []
            if len(unique_ids) > 1000:
                raise ValueError("WAITING_VISION retry allowlist is capped at 1000 items")
            placeholders = ",".join("?" for _ in unique_ids)
            query += f"AND content_id IN ({placeholders}) "
            params = (*params, *unique_ids)
        query += (
            "ORDER BY source_id, COALESCE(topic_id, 0), "
            "CAST(telegram_message_id AS INTEGER), created_at, content_id"
        )
        if limit not in (None, 0):
            bounded_limit = max(1, min(int(limit), 1000))
            query += " LIMIT ?"
            params = (*retry_states, bounded_limit)
            if content_ids is not None:
                params = (*retry_states, *unique_ids, bounded_limit)
        rows = self.conn.execute(query, params).fetchall()
        return [self.get(str(row["content_id"])) for row in rows]

    def has_active_vision_no_offer(self, product_key: str, *, now: float | None = None) -> bool:
        """Return whether an exact product has a still-active no-offer result."""
        key = str(product_key or "").strip()
        if not key:
            return False
        current_time = time.time() if now is None else float(now)
        row = self.conn.execute(
            "SELECT outcome, suppress_until FROM vision_product_lookup_events "
            "WHERE product_key=? ORDER BY lookup_id DESC LIMIT 1",
            (key,),
        ).fetchone()
        return bool(
            row
            and str(row["outcome"]) == "OFFER_ABSENT"
            and float(row["suppress_until"] or 0) > current_time
        )

    def record_vision_product_lookup(
        self,
        product_key: str,
        content_id: str,
        outcome: str,
        *,
        ttl_seconds: int = 21600,
        checked_at: float | None = None,
    ) -> None:
        """Append an exact Vision offer lookup result without rewriting history."""
        key = str(product_key or "").strip()
        item_id = str(content_id or "").strip()
        status = str(outcome or "").strip().upper()
        if not key or not item_id:
            raise ValueError("product_key and content_id are required")
        if status not in {"OFFER_FOUND", "OFFER_ABSENT"}:
            raise ValueError("Vision lookup outcome must be OFFER_FOUND or OFFER_ABSENT")
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds cannot be negative")
        timestamp = time.time() if checked_at is None else float(checked_at)
        suppress_until = timestamp + int(ttl_seconds) if status == "OFFER_ABSENT" else 0
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT INTO vision_product_lookup_events "
                "(product_key, content_id, outcome, checked_at, suppress_until) "
                "VALUES(?,?,?,?,?)",
                (key, item_id, status, timestamp, suppress_until),
            )
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
    def pending_vision_candidates(self) -> list[Item]:
        """Return reserved rows whose Vision gate has not produced a decision."""
        rows = self.conn.execute(
            "SELECT content_id FROM items "
            "WHERE state IN (?, ?) AND (affiliate_url IS NULL OR TRIM(affiliate_url)='') "
            "ORDER BY source_id, COALESCE(topic_id, 0), "
            "CAST(telegram_message_id AS INTEGER), created_at, content_id",
            (State.RECEIVED.value, State.VISION.value),
        ).fetchall()
        return [self.get(str(row["content_id"])) for row in rows]

    def catch_up_production_items(self) -> list[Item]:
        """Return durable items that can resume production without re-running Vision."""
        rows = self.conn.execute(
            "SELECT content_id FROM items WHERE "
            "(state=? AND affiliate_url IS NOT NULL AND TRIM(affiliate_url)<>'') "
            "OR state IN (?, ?, ?, ?, ?, ?) "
            "OR (state=? AND cleanup_completed=0) "
            "ORDER BY source_id, COALESCE(topic_id, 0), "
            "CAST(telegram_message_id AS INTEGER), created_at, content_id",
            (
                State.RECEIVED.value,
                State.IA.value,
                State.SPEECH.value,
                State.STUDIO_BATCH.value,
                State.STUDIO.value,
                State.PUBLISHING.value,
                State.RECOVERY.value,
                State.PUBLISHED.value,
            ),
        ).fetchall()
        return [self.get(str(row["content_id"])) for row in rows]

    def last_state_event(self, item_id: str):
        row = self.conn.execute(
            "SELECT old_state, new_state, reason, created_at "
            "FROM state_events WHERE content_id=? ORDER BY id DESC LIMIT 1",
            (str(item_id),),
        ).fetchone()
        return row

    def last_error(self, item_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT last_error FROM items WHERE content_id=?", (str(item_id),)
        ).fetchone()
        if row is None:
            raise KeyError(item_id)
        value = row["last_error"]
        return str(value) if value is not None else None

    def record_attempt(self, item_id: str) -> None:
        self.conn.execute(
            "UPDATE items SET attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (item_id,),
        )
        self.conn.commit()

    def record_recovery(self, item_id: str) -> None:
        self.conn.execute(
            "UPDATE items SET recovery_count=recovery_count+1, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (item_id,),
        )
        self.conn.commit()

    def transition(self, item_id: str, new_state: State, reason: str = "") -> None:
        old = self.get(item_id).state
        self.conn.execute(
            "UPDATE items SET state=?, last_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (new_state.value, item_id),
        )
        self.conn.execute(
            "INSERT INTO state_events (content_id,old_state,new_state,reason) VALUES (?,?,?,?)",
            (item_id, old.value, new_state.value, reason),
        )
        self.conn.commit()

    def mark_vision_waiting(self, item_id: str, reason: str) -> None:
        old = self.get(item_id).state
        self.conn.execute(
            "UPDATE items SET state=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (State.WAITING_VISION.value, reason, item_id),
        )
        self.conn.execute(
            "INSERT INTO state_events (content_id,old_state,new_state,reason) VALUES (?,?,?,?)",
            (item_id, old.value, State.WAITING_VISION.value, reason),
        )
        self.conn.commit()

    @staticmethod
    def _canonical_product_key(context: dict | None) -> str | None:
        """Build product identity only when Vision supplied both exact IDs."""
        if not isinstance(context, dict):
            return None
        shop_id = str(context.get("shopId") or context.get("shop_id") or "").strip()
        item_id = str(context.get("itemId") or context.get("item_id") or "").strip()
        if not shop_id or not item_id:
            return None
        return f"{shop_id}:{item_id}"

    def _seed_historical_product_claims_locked(self) -> None:
        """Seed product claims from history without rewriting historical items.

        The caller holds a write transaction. Owner selection is deterministic:
        confirmed/publication evidence first, then the furthest durable pipeline
        progress, an existing rendered result, and then ORIGINAL evidence. An
        ORIGINAL with a SHA-256 that matches its bytes outranks one without a
        verified digest; an unverified checksum string never adds weight.
        """
        seeded = self.conn.execute(
            "SELECT value FROM product_identity_claim_meta WHERE key='historical_seeded'"
        ).fetchone()
        if seeded:
            return

        def affirmative_flag(value: object) -> bool:
            """Parse SQLite legacy booleans explicitly; never treat 'false' as true."""
            if value is True:
                return True
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value == 1
            if isinstance(value, str):
                return value.strip().casefold() in {"1", "true", "yes", "y", "confirmed"}
            return False

        # Relative historical paths must not change rank when the process CWD
        # changes. A canonical DB path is <project>/storage/database/file.sqlite;
        # tiny test/standalone databases use their own parent as the anchor.
        database_path = Path(self.path).expanduser().resolve()
        db_parent = database_path.parent
        if (
            db_parent.name.casefold() == "database"
            and db_parent.parent.name.casefold() == "storage"
        ):
            relative_path_base = db_parent.parent.parent
        else:
            relative_path_base = db_parent

        publication_evidence: set[str] = set()
        publication_columns = {
            str(row[1]) for row in self.conn.execute("PRAGMA table_info(publications)").fetchall()
        }
        if "content_id" in publication_columns:
            selected = ["content_id"]
            selected.append("confirmed" if "confirmed" in publication_columns else "0 AS confirmed")
            selected.append(
                "verification_status" if "verification_status" in publication_columns
                else "'' AS verification_status"
            )
            selected.append(
                "published_message_id" if "published_message_id" in publication_columns
                else "NULL AS published_message_id"
            )
            for publication in self.conn.execute(
                "SELECT " + ", ".join(selected) + " FROM publications"
            ).fetchall():
                status = str(publication["verification_status"] or "").strip().upper()
                if (
                    affirmative_flag(publication["confirmed"])
                    or bool(str(publication["published_message_id"] or "").strip())
                    or status in {"CONFIRMED", "VERIFIED", "PUBLISHED", "DELIVERED"}
                ):
                    publication_evidence.add(str(publication["content_id"]))

        rows = self.conn.execute(
            "SELECT content_id, ia_context_json, state, original_path, original_sha256, "
            "result_path, created_at FROM items "
            "WHERE state<>? AND affiliate_url IS NOT NULL AND TRIM(affiliate_url)<>'' "
            "ORDER BY created_at, content_id",
            (State.DUPLICATE.value,),
        ).fetchall()

        # Higher tuple values win; created_at/content_id provide stable tie-breaks.
        state_progress = {
            "PUBLISHED": 7,
            "PUBLISHING": 6,
            "RECOVERY": 5,
            "STUDIO_COMPLETE": 4,
            "STUDIO_BATCH": 4,
            "STUDIO": 3,
            "SPEECH": 3,
            "IA": 2,
            "RECEIVED": 1,
            "VISION": 1,
            "WAITING_VISION": 0,
        }

        def existing_file(value: object) -> Path | None:
            raw = str(value or "").strip()
            if not raw:
                return None
            try:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    path = relative_path_base / path
                path = path.resolve(strict=True)
                return path if path.is_file() else None
            except (OSError, ValueError, RuntimeError):
                return None

        def valid_sha(value: object, path: Path | None) -> bool:
            expected = str(value or "").strip()
            if (
                path is None
                or len(expected) != 64
                or any(char not in "0123456789abcdefABCDEF" for char in expected)
            ):
                return False
            try:
                return sha256_file(path).lower() == expected.lower()
            except (OSError, ValueError, RuntimeError):
                return False

        candidates: dict[str, list[tuple[tuple[int, ...], str, str]]] = {}
        for row in rows:
            try:
                prior_context = json.loads(row["ia_context_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            product_key = self._canonical_product_key(prior_context)
            if not product_key:
                continue

            original_path = existing_file(row["original_path"])
            result_path = existing_file(row["result_path"])
            original_exists = original_path is not None
            result_exists = result_path is not None
            sha_is_valid = valid_sha(row["original_sha256"], original_path)
            score = (
                int(str(row["content_id"]) in publication_evidence),
                int(state_progress.get(str(row["state"] or "").upper(), 0)),
                int(result_exists),
                int(original_exists and sha_is_valid),
                int(original_exists),
            )
            candidates.setdefault(product_key, []).append(
                (score, str(row["created_at"] or ""), str(row["content_id"]))
            )

        owners: dict[str, str] = {}
        for product_key, entries in candidates.items():
            # Score descending, then preserve earliest durable historical row.
            winner = sorted(entries, key=lambda entry: (
                tuple(-value for value in entry[0]), entry[1], entry[2]
            ))[0]
            owners[product_key] = winner[2]

        self.conn.executemany(
            "INSERT OR IGNORE INTO product_identity_claims(product_key, owner_content_id) VALUES(?, ?)",
            sorted(owners.items()),
        )
        self.conn.execute(
            "INSERT INTO product_identity_claim_meta(key,value) VALUES('historical_seeded','1')"
        )

    def _resolve_valid_product_owner_locked(
        self, product_key: str, owner_content_id: str
    ) -> str | None:
        """Resolve a ledger owner through duplicate links and verify its evidence."""
        current_id = str(owner_content_id or "")
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            row = self.conn.execute(
                "SELECT state, duplicate_of_content_id, affiliate_url, ia_context_json "
                "FROM items WHERE content_id=?",
                (current_id,),
            ).fetchone()
            if row is None:
                return None
            if str(row["state"]) == State.DUPLICATE.value:
                current_id = str(row["duplicate_of_content_id"] or "")
                continue
            if not str(row["affiliate_url"] or "").strip():
                return None
            try:
                row_context = json.loads(row["ia_context_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            return current_id if self._canonical_product_key(row_context) == product_key else None
        return None

    def _find_historical_product_owner_locked(
        self, product_key: str, *, exclude_content_id: str
    ) -> str | None:
        """Find the earliest approved non-duplicate row with exact V1 product IDs."""
        rows = self.conn.execute(
            "SELECT content_id, ia_context_json FROM items "
            "WHERE content_id<>? AND state<>? "
            "AND affiliate_url IS NOT NULL AND TRIM(affiliate_url)<>'' "
            "ORDER BY created_at, content_id",
            (str(exclude_content_id), State.DUPLICATE.value),
        ).fetchall()
        for row in rows:
            try:
                context = json.loads(row["ia_context_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if self._canonical_product_key(context) == product_key:
                return str(row["content_id"])
        return None

    def claim_product_identity(self, item_id: str, context: dict | None) -> str | None:
        """Atomically reserve a canonical product identity for compatibility callers.

        The production Vision path uses commit_vision_result(), which makes the
        claim and persisted Vision evidence one transaction. This method remains
        for existing callers/tests that intentionally reserve identity alone.
        """
        product_key = self._canonical_product_key(context)
        if not product_key:
            return None
        content_id = str(item_id)

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self._seed_historical_product_claims_locked()
            claim = self.conn.execute(
                "SELECT owner_content_id FROM product_identity_claims WHERE product_key=?",
                (product_key,),
            ).fetchone()
            if claim:
                owner = str(claim["owner_content_id"])
                self.conn.commit()
                return None if owner == content_id else owner

            self.conn.execute(
                "INSERT INTO product_identity_claims(product_key, owner_content_id) VALUES(?, ?)",
                (product_key, content_id),
            )
            self.conn.commit()
            return None
        except BaseException:
            self.conn.rollback()
            raise

    def has_speech_studio_v5_completion(self, item_id: str) -> bool:
        """Durable render boundary shared by Coordinator and publication."""
        return self.conn.execute(
            "SELECT 1 FROM state_events WHERE content_id=? AND new_state=? AND reason=? LIMIT 1",
            (str(item_id), State.PUBLISHING.value, "speech-studio-v5-complete"),
        ).fetchone() is not None

    def commit_vision_result(
        self,
        item_id: str,
        affiliate_name: str,
        affiliate_url: str,
        affiliate_urls=(),
        publication_caption: str | None = None,
        ia_context: dict | None = None,
    ) -> str | None:
        """Atomically persist Vision approval or mark the item as a duplicate.

        Product identity claims, approval metadata, duplicate state, and its state
        event are committed together. A crash cannot leave a product claim pointing
        to an item whose Vision evidence was never persisted.
        """
        content_id = str(item_id)
        context = ia_context if isinstance(ia_context, dict) else {}
        product_key = self._canonical_product_key(context)
        links = list(dict.fromkeys(
            str(link).strip()
            for link in (affiliate_urls or ())
            if str(link).strip()
        ))
        if not links and str(affiliate_url or "").strip():
            links = [str(affiliate_url).strip()]

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            current = self.conn.execute(
                "SELECT state, duplicate_of_content_id FROM items WHERE content_id=?",
                (content_id,),
            ).fetchone()
            if current is None:
                raise KeyError(f"unknown-content-id:{content_id}")
            if str(current["state"]) == State.DUPLICATE.value:
                owner = str(current["duplicate_of_content_id"] or "").strip() or None
                self.conn.commit()
                return owner

            duplicate_owner: str | None = None
            if product_key:
                self._seed_historical_product_claims_locked()
                claim = self.conn.execute(
                    "SELECT owner_content_id FROM product_identity_claims WHERE product_key=?",
                    (product_key,),
                ).fetchone()
                if claim:
                    recorded_owner = str(claim["owner_content_id"])
                    if recorded_owner != content_id:
                        duplicate_owner = self._resolve_valid_product_owner_locked(
                            product_key, recorded_owner
                        )
                        if duplicate_owner is None:
                            # Repair a stale claim left by older non-atomic
                            # versions, if a different approved historical row
                            # provides valid evidence for the exact same product.
                            duplicate_owner = self._find_historical_product_owner_locked(
                                product_key, exclude_content_id=content_id
                            )
                        if duplicate_owner:
                            self.conn.execute(
                                "UPDATE product_identity_claims SET owner_content_id=? "
                                "WHERE product_key=?",
                                (duplicate_owner, product_key),
                            )
                        else:
                            # The former owner has no durable, matching Vision
                            # approval. Transfer the claim to this newly resolved
                            # product instead of suppressing it behind stale data.
                            self.conn.execute(
                                "DELETE FROM product_identity_claims WHERE product_key=?",
                                (product_key,),
                            )
                            self.conn.execute(
                                "INSERT INTO product_identity_claims(product_key, owner_content_id) "
                                "VALUES(?, ?)",
                                (product_key, content_id),
                            )
                    # A claim owned by the current item is completed below in the
                    # same transaction as the approved Vision metadata.
                else:
                    self.conn.execute(
                        "INSERT INTO product_identity_claims(product_key, owner_content_id) "
                        "VALUES(?, ?)",
                        (product_key, content_id),
                    )

            if duplicate_owner and duplicate_owner != content_id:
                old_state = str(current["state"])
                detail = f"canonical-product-already-represented:{duplicate_owner}"
                self.conn.execute(
                    "UPDATE items SET state=?, duplicate_of_content_id=?, last_error=?, "
                    "affiliate_name=NULL, affiliate_url=NULL, affiliate_urls_json='[]', "
                    "publication_caption=NULL, ia_context_json=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE content_id=?",
                    (
                        State.DUPLICATE.value,
                        duplicate_owner,
                        detail,
                        json.dumps(context, ensure_ascii=False, default=str),
                        content_id,
                    ),
                )
                self.conn.execute(
                    "INSERT INTO state_events(content_id,old_state,new_state,reason) "
                    "VALUES(?,?,?,?)",
                    (content_id, old_state, State.DUPLICATE.value, detail),
                )
                self.conn.commit()
                return duplicate_owner

            self.conn.execute(
                "UPDATE items SET affiliate_name=?, affiliate_url=?, publication_caption=?, "
                "affiliate_urls_json=?, ia_context_json=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE content_id=?",
                (
                    str(affiliate_name or ""),
                    str(affiliate_url or ""),
                    publication_caption,
                    json.dumps(links, ensure_ascii=False),
                    json.dumps(context, ensure_ascii=False, default=str),
                    content_id,
                ),
            )
            self.conn.commit()
            return None
        except BaseException:
            self.conn.rollback()
            raise

    def mark_duplicate(self, item_id: str, owner_content_id: str, reason: str = "") -> None:
        """Preserve the incoming message but keep a duplicate out of downstream stages."""
        content_id, owner = str(item_id), str(owner_content_id)
        if content_id == owner:
            raise ValueError("duplicate owner must be a different content_id")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            old_state = self.get(content_id).state
            detail = f"{reason or 'canonical-product-already-represented'}:{owner}"
            self.conn.execute(
                "UPDATE items SET state=?, duplicate_of_content_id=?, last_error=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
                (State.DUPLICATE.value, owner, detail, content_id),
            )
            self.conn.execute(
                "INSERT INTO state_events(content_id,old_state,new_state,reason) VALUES(?,?,?,?)",
                (content_id, old_state.value, State.DUPLICATE.value, detail),
            )
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def set_vision(
        self,
        item_id: str,
        affiliate_name: str,
        affiliate_url: str,
        affiliate_urls=(),
        publication_caption: str | None = None,
        ia_context: dict | None = None,
    ) -> None:
        links = [str(link).strip() for link in (affiliate_urls or ()) if str(link).strip()]
        if not links and affiliate_url:
            links = [str(affiliate_url).strip()]

        self.conn.execute(
            "UPDATE items SET affiliate_name=?, affiliate_url=?, publication_caption=?, "
            "affiliate_urls_json=?, ia_context_json=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (
                affiliate_name,
                affiliate_url,
                publication_caption,
                json.dumps(list(dict.fromkeys(links)), ensure_ascii=False),
                json.dumps(ia_context or {}, ensure_ascii=False, default=str),
                item_id,
            ),
        )
        self.conn.commit()


    def set_ia_context(self, item_id: str, context: dict | None) -> None:
        self.conn.execute(
            "UPDATE items SET ia_context_json=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (json.dumps(context or {}, ensure_ascii=False, default=str), str(item_id)),
        )
        self.conn.commit()

    @contextmanager
    def ia_generation_attempt(self, item_id: str, *, provider: str, model: str | None):
        """Record configured identity before I/O; interrupted runs stay STARTED."""
        attempt_id = self.conn.execute(
            "INSERT INTO ia_generation_attempts(content_id,provider,model,status) VALUES(?,?,?,'STARTED')",
            (str(item_id), provider, model),
        ).lastrowid
        self.conn.commit()
        try:
            yield attempt_id
        except BaseException as exc:
            status = "INTERRUPTED" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "FAILED"
            self.conn.execute(
                "UPDATE ia_generation_attempts SET status=?,error_type=?,finished_at=CURRENT_TIMESTAMP WHERE attempt_id=?",
                (status, type(exc).__name__, attempt_id),
            )
            self.conn.commit()
            raise
        else:
            self.conn.execute(
                "UPDATE ia_generation_attempts SET status='COMPLETE',finished_at=CURRENT_TIMESTAMP WHERE attempt_id=?",
                (attempt_id,),
            )
            self.conn.commit()

    def set_caption(self, item_id: str, caption: str) -> None:
        self.conn.execute(
            "UPDATE items SET publication_caption=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (str(caption), str(item_id)),
        )
        self.conn.commit()

    def record_caption_candidates(self, item_id: str, batch_id: str, evaluations) -> None:
        """Persist every caption candidate and its local Policy/ranking decision."""
        self.conn.executemany(
            "INSERT OR REPLACE INTO caption_candidates "
            "(content_id,batch_id,candidate_index,caption,policy_valid,rejection_reason,score,selected) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    str(item_id),
                    str(batch_id),
                    int(evaluation.index),
                    str(evaluation.caption),
                    1 if evaluation.policy_valid else 0,
                    evaluation.rejection_reason,
                    float(evaluation.score),
                    1 if evaluation.selected else 0,
                )
                for evaluation in evaluations
            ],
        )
        self.conn.commit()

    def caption_candidates(self, item_id: str, batch_id: str | None = None):
        if batch_id is None:
            return self.conn.execute(
                "SELECT * FROM caption_candidates "
                "WHERE content_id=? ORDER BY created_at, candidate_index",
                (str(item_id),),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM caption_candidates "
            "WHERE content_id=? AND batch_id=? ORDER BY candidate_index",
            (str(item_id), str(batch_id)),
        ).fetchall()

    def set_working(self, item_id: str, path: Path | None) -> None:
        self.conn.execute(
            "UPDATE items SET working_path=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (str(path), item_id),
        )
        self.conn.commit()


    def complete_speech_studio_batch(self, results: dict[str, Path]) -> None:
        """Atomically persist the complete, hash-validated Speech v5 render batch."""
        if not isinstance(results, dict) or not results:
            raise ValueError("speech-studio-results-required")
        prepared = {
            str(item_id): str(Path(path).resolve(strict=True))
            for item_id, path in results.items()
        }
        if len(prepared) != len(results):
            raise ValueError("duplicate-content-id-in-speech-studio-results")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for item_id in sorted(prepared):
                row = self.conn.execute(
                    "SELECT state FROM items WHERE content_id=?",
                    (item_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(item_id)
                old_state = State(str(row["state"]))
                if old_state is not State.STUDIO_BATCH:
                    raise RuntimeError(
                        f"speech-studio-batch-state-mismatch:{item_id}:{old_state.value}"
                    )
                self.conn.execute(
                    "UPDATE items SET result_path=?, state=?, last_error=NULL, "
                    "updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
                    (prepared[item_id], State.PUBLISHING.value, item_id),
                )
                self.conn.execute(
                    "INSERT INTO state_events (content_id,old_state,new_state,reason) "
                    "VALUES (?,?,?,?)",
                    (
                        item_id,
                        State.STUDIO_BATCH.value,
                        State.PUBLISHING.value,
                        "speech-studio-v5-complete",
                    ),
                )
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def set_result(self, item_id: str, path: Path) -> None:
        self.conn.execute(
            "UPDATE items SET result_path=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (str(path), item_id),
        )
        self.conn.commit()

    def record_retryable_error(self, item_id: str, error: str) -> None:
        """Persist a technical error without moving a pre-download item to RECOVERY."""
        current = self.get(item_id)
        self.conn.execute(
            "UPDATE items SET last_error=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (str(error), str(item_id)),
        )
        self.conn.execute(
            "INSERT INTO state_events (content_id,old_state,new_state,reason) VALUES (?,?,?,?)",
            (str(item_id), current.state.value, current.state.value, str(error)),
        )
        self.conn.commit()

    def fail(self, item_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE items SET state=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (State.FAILED.value, error, item_id),
        )
        self.conn.execute(
            "INSERT INTO state_events (content_id,new_state,reason) VALUES (?,?,?)",
            (item_id, State.FAILED.value, error),
        )
        self.conn.commit()

    def mark_cleanup_completed(self, item_id: str) -> None:
        self.conn.execute(
            "UPDATE items SET cleanup_completed=1, updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (item_id,),
        )
        self.conn.commit()

    def publication_started(
        self,
        item_id: str,
        destination_chat_id: str | None = None,
        destination_topic_id: int | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO publications(content_id,idempotency_key,destination_chat_id,destination_topic_id) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(content_id) DO UPDATE SET "
            "destination_chat_id=COALESCE(excluded.destination_chat_id, publications.destination_chat_id), "
            "destination_topic_id=COALESCE(excluded.destination_topic_id, publications.destination_topic_id), "
            "updated_at=CURRENT_TIMESTAMP",
            (str(item_id), f"armoredcreator:content:{str(item_id)}",
             destination_chat_id, destination_topic_id),
        )
        self.conn.commit()

    def publication_send_started(self, item_id: str) -> None:
        """Persist that the external Telegram send has entered its side-effect window."""
        self.conn.execute(
            "UPDATE publications SET verification_status='SENT_UNVERIFIED', updated_at=CURRENT_TIMESTAMP "
            "WHERE content_id=?",
            (str(item_id),),
        )
        self.conn.commit()

    def publication_message_sent(self, item_id: str, message_id: str) -> None:
        """Persist the Telegram message ID before any post-send failure window."""
        self.conn.execute(
            "UPDATE publications SET published_message_id=?, confirmed=0, "
            "verification_status='SENT_UNVERIFIED', updated_at=CURRENT_TIMESTAMP "
            "WHERE content_id=?",
            (str(message_id), str(item_id)),
        )
        self.conn.commit()

    def publication_confirmed(self, item_id: str, message_id: str) -> None:
        self.conn.execute(
            "UPDATE publications SET published_message_id=?, confirmed=1, "
            "verification_status='CONFIRMED', verified_at=CURRENT_TIMESTAMP, "
            "updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
            (str(message_id), str(item_id)),
        )
        self.conn.commit()

    def publication(self, item_id: int):
        return self.conn.execute(
            "SELECT * FROM publications WHERE content_id=?", (item_id,)
        ).fetchone()

    def acquire_runtime_lock(self, name: str = "coordinator") -> None:
        """Atomically acquire the single-process lease stored in SQLite.

        BEGIN IMMEDIATE serializes the check-and-claim operation across
        processes. A crashed process leaves its row behind; a dead PID can be
        replaced, while a live PID blocks a second Coordinator.
        """
        import errno
        import os
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS runtime_locks (
                    name TEXT PRIMARY KEY,
                    pid INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                )"""
            )
            row = self.conn.execute(
                "SELECT pid FROM runtime_locks WHERE name=?", (str(name),)
            ).fetchone()
            current_pid = os.getpid()
            if row is not None:
                pid = int(row["pid"])
                # Same-process ownership is still exclusive: a second
                # Coordinator object must not share this runtime lease.
                alive = pid == current_pid
                if not alive:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except PermissionError:
                        alive = True
                    except ProcessLookupError:
                        alive = False
                    except OSError as exc:
                        # Windows reports an absent process as WinError 87;
                        # ESRCH is the corresponding POSIX result. Other OS
                        # errors are treated conservatively as a live process.
                        alive = not (
                            getattr(exc, "errno", None) == errno.ESRCH
                            or getattr(exc, "winerror", None) == 87
                        )
                if alive:
                    raise RuntimeError(
                        f"runtime-lock-active: {name} is already owned by PID {pid}"
                    )

            self.conn.execute(
                "INSERT INTO runtime_locks(name,pid,started_at,heartbeat_at) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET pid=excluded.pid, "
                "started_at=excluded.started_at, heartbeat_at=excluded.heartbeat_at",
                (str(name), current_pid, now, now),
            )
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def heartbeat_runtime_lock(self, name: str = "coordinator") -> None:
        import os
        self.conn.execute(
            "UPDATE runtime_locks SET heartbeat_at=CURRENT_TIMESTAMP "
            "WHERE name=? AND pid=?",
            (str(name), os.getpid()),
        )
        self.conn.commit()

    def release_runtime_lock(self, name: str = "coordinator") -> None:
        import os
        self.conn.execute(
            "DELETE FROM runtime_locks WHERE name=? AND pid=?",
            (str(name), os.getpid()),
        )
        self.conn.commit()

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.commit()
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                # Another SQLite connection may still be open during shutdown.
                # Closing this connection is safe; the remaining connection can
                # perform the checkpoint later.
                pass
        finally:
            self.conn.close()
            self.conn = None
