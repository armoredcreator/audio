from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class PublicationQuarantine:
    """Durable, additive fence for publication outcomes that remain UNKNOWN.

    This ledger never edits the operational publications table and has no
    operation that turns an unknown outcome into absence or confirmation.
    Reconciliation is an explicit, separate workflow.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS quarantined_publications ("
                " content_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,"
                " original_status TEXT NOT NULL, original_message_id TEXT,"
                " original_confirmed INTEGER NOT NULL, destination_chat_id TEXT,"
                " destination_topic_id INTEGER, first_observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
                "CREATE TABLE IF NOT EXISTS quarantine_evidence ("
                " evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, content_id TEXT NOT NULL,"
                " observed_outcome TEXT NOT NULL CHECK(observed_outcome='UNKNOWN'),"
                " evidence_json TEXT NOT NULL, observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                " FOREIGN KEY(content_id) REFERENCES quarantined_publications(content_id));"
                "CREATE INDEX IF NOT EXISTS idx_quarantine_evidence_content"
                " ON quarantine_evidence(content_id,evidence_id);"
            )
            self._ids = {
                str(row[0])
                for row in conn.execute("SELECT content_id FROM quarantined_publications")
            }
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def contains(self, content_id: str) -> bool:
        return str(content_id) in self._ids

    def quarantined_ids(self) -> set[str]:
        return set(self._ids)

    def unresolved_count(self) -> int:
        return len(self._ids)

    def observed_outcomes(self) -> set[str]:
        conn = self._connect()
        try:
            return {
                str(row[0])
                for row in conn.execute("SELECT DISTINCT observed_outcome FROM quarantine_evidence")
            }
        finally:
            conn.close()

    def quarantine_unknown(
        self,
        item_id: str,
        publication: Any,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        """Append an UNKNOWN observation while preserving the first receipt snapshot."""
        content_id = str(item_id)
        row = dict(publication)
        status = str(row.get("verification_status") or "UNKNOWN")
        if status == "CONFIRMED":
            raise ValueError("confirmed-publication-cannot-enter-unknown-quarantine")
        key = str(row.get("idempotency_key") or "").strip()
        if not key:
            raise ValueError("quarantine-requires-existing-publication-idempotency-key")
        snapshot = {
            "reason": "telegram-publication-outcome-unknown",
            "item_state": (evidence or {}).get("item_state"),
            "last_state_event": (evidence or {}).get("last_state_event"),
            "publication_check": "UNKNOWN",
            "original_receipt": {
                "idempotency_key": key,
                "verification_status": status,
                "published_message_id": row.get("published_message_id"),
                "confirmed": int(bool(row.get("confirmed"))),
                "destination_chat_id": row.get("destination_chat_id"),
                "destination_topic_id": row.get("destination_topic_id"),
            },
        }
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO quarantined_publications "
                "(content_id,idempotency_key,original_status,original_message_id,"
                "original_confirmed,destination_chat_id,destination_topic_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    content_id, key, status, row.get("published_message_id"),
                    int(bool(row.get("confirmed"))), row.get("destination_chat_id"),
                    row.get("destination_topic_id"),
                ),
            )
            existing = conn.execute(
                "SELECT idempotency_key,original_status,original_message_id,original_confirmed,"
                "destination_chat_id,destination_topic_id FROM quarantined_publications "
                "WHERE content_id=?", (content_id,),
            ).fetchone()
            expected = (
                key, status, row.get("published_message_id"), int(bool(row.get("confirmed"))),
                row.get("destination_chat_id"), row.get("destination_topic_id"),
            )
            if tuple(existing) != expected:
                raise RuntimeError("quarantined-publication-receipt-snapshot-changed")
            conn.execute(
                "INSERT INTO quarantine_evidence(content_id,observed_outcome,evidence_json) "
                "VALUES(?, 'UNKNOWN', ?)",
                (content_id, json.dumps(snapshot, ensure_ascii=False, sort_keys=True)),
            )
            conn.commit()
            self._ids.add(content_id)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def close(self) -> None:
        # Connections are deliberately short-lived to avoid Windows file locks.
        return None
