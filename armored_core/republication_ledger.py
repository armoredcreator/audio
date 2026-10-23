"""Durable, separate SQLite ledger for explicitly approved replay publications."""
from __future__ import annotations

import sqlite3
from pathlib import Path


class VersionedPublicationLedger:
    """One-shot publication runs stored outside the operational item database."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS publication_runs (
                run_id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                execution_version INTEGER NOT NULL CHECK(execution_version > 0),
                canonical_content_id TEXT NOT NULL,
                prior_publication_message_id TEXT NOT NULL,
                output_sha256 TEXT NOT NULL CHECK(length(output_sha256)=64),
                idempotency_key TEXT NOT NULL UNIQUE,
                destination_chat_id TEXT NOT NULL,
                destination_topic_id INTEGER NOT NULL CHECK(destination_topic_id > 0),
                status TEXT NOT NULL CHECK(status IN (
                    'PREPARED','SEND_STARTED','SENT_UNVERIFIED','CONFIRMED','INDETERMINATE'
                )),
                published_message_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(execution_id, execution_version, canonical_content_id)
            );
            CREATE INDEX IF NOT EXISTS idx_republication_execution
                ON publication_runs(execution_id, execution_version);
            """
        )

    def close(self) -> None:
        self.conn.close()

    def prepare(
        self,
        *,
        run_id: str,
        execution_id: str,
        execution_version: int,
        canonical_content_id: str,
        prior_publication_message_id: str,
        output_sha256: str,
        idempotency_key: str,
        destination_chat_id: str,
        destination_topic_id: int,
    ):
        values = (
            str(run_id).strip(), str(execution_id).strip(), int(execution_version),
            str(canonical_content_id).strip(), str(prior_publication_message_id).strip(),
            str(output_sha256).strip().lower(), str(idempotency_key).strip(),
            str(destination_chat_id).strip(), int(destination_topic_id),
        )
        run_id, execution_id, version, content_id, prior_id, digest, key, chat, topic = values
        if not all((run_id, execution_id, content_id, prior_id, key, chat)) or version < 1 or topic < 1:
            raise ValueError("invalid-versioned-publication-run")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("invalid-versioned-publication-sha256")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self.conn.execute(
                "SELECT * FROM publication_runs WHERE run_id=? OR idempotency_key=? "
                "OR (execution_id=? AND execution_version=? AND canonical_content_id=?) LIMIT 1",
                (run_id, key, execution_id, version, content_id),
            ).fetchone()
            immutable = (
                execution_id, version, content_id, prior_id, digest, key, chat, topic,
            )
            if existing:
                actual = (
                    str(existing["execution_id"]), int(existing["execution_version"]),
                    str(existing["canonical_content_id"]),
                    str(existing["prior_publication_message_id"]), str(existing["output_sha256"]),
                    str(existing["idempotency_key"]), str(existing["destination_chat_id"]),
                    int(existing["destination_topic_id"]),
                )
                if str(existing["run_id"]) != run_id or actual != immutable:
                    raise ValueError("publication-run-identity-conflict")
                self.conn.commit()
                return existing
            self.conn.execute(
                "INSERT INTO publication_runs(run_id,execution_id,execution_version,"
                "canonical_content_id,prior_publication_message_id,output_sha256,idempotency_key,"
                "destination_chat_id,destination_topic_id,status) VALUES(?,?,?,?,?,?,?,?,?, 'PREPARED')",
                values,
            )
            self.conn.commit()
            return self.get(run_id)
        except BaseException:
            self.conn.rollback()
            raise

    def get(self, run_id: str):
        return self.conn.execute(
            "SELECT * FROM publication_runs WHERE run_id=?", (str(run_id),)
        ).fetchone()

    def list_execution(self, execution_id: str, version: int):
        return self.conn.execute(
            "SELECT * FROM publication_runs WHERE execution_id=? AND execution_version=? "
            "ORDER BY canonical_content_id",
            (str(execution_id), int(version)),
        ).fetchall()

    def mark_send_started(self, run_id: str) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                "UPDATE publication_runs SET status='SEND_STARTED',updated_at=CURRENT_TIMESTAMP "
                "WHERE run_id=? AND status='PREPARED'",
                (str(run_id),),
            )
            if cur.rowcount != 1:
                raise ValueError("publication-run-is-not-retryable")
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def mark_message_sent(self, run_id: str, message_id: str) -> None:
        cur = self.conn.execute(
            "UPDATE publication_runs SET status='SENT_UNVERIFIED',published_message_id=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND status='SEND_STARTED'",
            (str(message_id), str(run_id)),
        )
        if cur.rowcount != 1:
            raise ValueError("publication-run-send-state-changed")

    def mark_confirmed(self, run_id: str, message_id: str) -> None:
        cur = self.conn.execute(
            "UPDATE publication_runs SET status='CONFIRMED',published_message_id=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND status='SENT_UNVERIFIED'",
            (str(message_id), str(run_id)),
        )
        if cur.rowcount != 1:
            raise ValueError("publication-run-confirmation-state-changed")

    def mark_indeterminate(self, run_id: str) -> None:
        self.conn.execute(
            "UPDATE publication_runs SET status='INDETERMINATE',updated_at=CURRENT_TIMESTAMP "
            "WHERE run_id=? AND status IN ('SEND_STARTED','SENT_UNVERIFIED')",
            (str(run_id),),
        )
