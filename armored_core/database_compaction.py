"""Reversible physical compaction of already-reconciled duplicate rows.

This maintenance operation is intentionally separate from normal startup and
requires an explicit disposable-copy root. Event, publication, checkpoint,
caption and reconciliation history tables are never deleted or rewritten.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CompactionError(RuntimeError):
    pass


_HISTORY_TABLES = {
    "state_events", "caption_candidates", "reconciliation_changes",
    "reconciliation_runs", "reconciliation_meta", "reconciliation_archived_items",
    "reconciliation_compaction_runs",
}


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    resolved = Path(path).resolve(strict=True)
    mode = "ro" if readonly else "rw"
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode={mode}", uri=True)
    connection.row_factory = sqlite3.Row
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__sqlite_blob_base64__": __import__("base64").b64encode(value).decode("ascii")}
    raise TypeError(f"unsupported SQLite value type: {type(value).__name__}")


def _dump_row(row: sqlite3.Row) -> str:
    return json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_value)


def _restore_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"__sqlite_blob_base64__"}:
        return __import__("base64").b64decode(value["__sqlite_blob_base64__"])
    return value


def _load_row(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise CompactionError("archived item is not a JSON object")
    return {key: _restore_value(item) for key, item in value.items()}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _snapshot(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    columns = _columns(connection, table)
    if not columns:
        return {"count": 0, "sha256": hashlib.sha256(b"[]").hexdigest()}
    quoted = ",".join(f'"{name}"' for name in columns)
    rows = [_dump_row(row) for row in connection.execute(f'SELECT {quoted} FROM "{table}"')]
    rows.sort()
    payload = "[" + ",".join(rows) + "]"
    return {"count": len(rows), "sha256": _sha(payload)}


def inspect_compaction(database: Path, *, allow_existing_archive: bool = True) -> dict[str, Any]:
    """Read-only eligibility and preservation report for reconciled duplicates."""
    connection = _connect(database, readonly=True)
    try:
        if "items" not in _table_names(connection):
            raise CompactionError("database has no items table")
        integrity = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        if integrity != ["ok"]:
            raise CompactionError(f"SQLite quick_check failed: {integrity[:5]}")
        item_columns = set(_columns(connection, "items"))
        required = {"content_id", "state", "duplicate_of_content_id"}
        if not required.issubset(item_columns):
            raise CompactionError("items table lacks reconciled duplicate columns")
        tables = _table_names(connection)
        if "reconciliation_archived_items" in tables and not allow_existing_archive:
            raise CompactionError("archive table already exists; inspect previous compaction run")
        if "reconciliation_archived_items" in tables:
            archive_columns = set(_columns(connection, "reconciliation_archived_items"))
            run_columns = set(_columns(connection, "reconciliation_compaction_runs")) if "reconciliation_compaction_runs" in tables else set()
            if not {"content_id", "run_id", "item_json", "item_sha256", "archived_at"}.issubset(archive_columns):
                raise CompactionError("existing duplicate archive schema is incompatible")
            if not {"run_id", "status", "archived_count", "created_at", "rolled_back_at"}.issubset(run_columns):
                raise CompactionError("existing compaction run ledger schema is incompatible")
        publication_ids: set[str] = set()
        if "publications" in tables and "content_id" in _columns(connection, "publications"):
            publication_ids = {str(row[0]) for row in connection.execute("SELECT content_id FROM publications")}
        item_rows = connection.execute("SELECT * FROM items ORDER BY content_id").fetchall()
        by_id = {str(row["content_id"]): row for row in item_rows}
        candidates: list[str] = []
        blocked: list[dict[str, str]] = []
        unknown_references = []
        candidate_ids = [
            str(row["content_id"]) for row in item_rows
            if str(row["state"]) == "DUPLICATE"
        ]
        for table in sorted(tables):
            if table in {"items", "publications", *_HISTORY_TABLES}:
                continue
            if "content_id" in _columns(connection, table):
                if candidate_ids:
                    count = 0
                    for offset in range(0, len(candidate_ids), 500):
                        batch = candidate_ids[offset:offset + 500]
                        placeholders = ",".join("?" for _ in batch)
                        count += connection.execute(
                            f'SELECT COUNT(*) FROM "{table}" WHERE content_id IN ({placeholders})',
                            batch,
                        ).fetchone()[0]
                    if count:
                        unknown_references.append(table)
        for row in item_rows:
            item_id = str(row["content_id"])
            if str(row["state"]) != "DUPLICATE":
                continue
            owner_id = str(row["duplicate_of_content_id"] or "")
            owner = by_id.get(owner_id)
            if not owner_id or owner is None or str(owner["state"]) == "DUPLICATE":
                blocked.append({"content_id": item_id, "reason": "canonical-owner-missing-or-duplicate"})
            elif item_id in publication_ids:
                blocked.append({"content_id": item_id, "reason": "publication-record-preserved"})
            elif unknown_references:
                blocked.append({"content_id": item_id, "reason": "unreviewed-content-id-reference-table"})
            else:
                candidates.append(item_id)
        preserved_tables = [table for table in (
            "publications", "state_events", "caption_candidates", "reconciliation_changes",
            "sync_state", "sync_topics", "sync_source_topics", "sync_sources",
        ) if table in tables]
        return {
            "database": str(Path(database).resolve()),
            "quick_check": integrity,
            "item_count_before": len(item_rows),
            "duplicate_count": sum(str(row["state"]) == "DUPLICATE" for row in item_rows),
            "compactable_content_ids": candidates,
            "compactable_count": len(candidates),
            "blocked": blocked,
            "unknown_content_id_reference_tables": unknown_references,
            "preserved_tables_before": {table: _snapshot(connection, table) for table in preserved_tables},
            "foreign_key_violations_before": [list(row) for row in connection.execute("PRAGMA foreign_key_check")],
            "note": "Only items rows are moved to an archive table; media bytes and all other tables remain unchanged.",
        }
    finally:
        connection.close()


def compact_duplicates(database: Path, copy_root: Path) -> dict[str, Any]:
    database = Path(database).resolve(strict=True)
    root = Path(copy_root).resolve(strict=True)
    try:
        database.relative_to(root)
    except ValueError as exc:
        raise CompactionError("database must be inside the explicit copy root") from exc
    normalized = str(database).replace("/", "\\").casefold()
    if "\\armoredcreator-final\\storage\\" in normalized:
        raise CompactionError("refusing to compact the operational database")
    plan = inspect_compaction(database, allow_existing_archive=True)
    if plan["blocked"]:
        raise CompactionError(f"compaction has blocked rows: {len(plan['blocked'])}")
    if not plan["compactable_count"]:
        return {"run_id": None, "archived_count": 0, "already_compact": True, "plan": plan}

    run_id = str(uuid.uuid4())
    connection = _connect(database, readonly=False)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE IF NOT EXISTS reconciliation_archived_items (
            content_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, item_json TEXT NOT NULL,
            item_sha256 TEXT NOT NULL, archived_at TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS reconciliation_compaction_runs (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL, archived_count INTEGER NOT NULL,
            created_at TEXT NOT NULL, rolled_back_at TEXT
        )""")
        item_columns = _columns(connection, "items")
        selected_columns = ",".join(f'"{column}"' for column in item_columns)
        for item_id in plan["compactable_content_ids"]:
            row = connection.execute(
                f'SELECT {selected_columns} FROM items WHERE content_id=? AND state=?',
                (item_id, "DUPLICATE"),
            ).fetchone()
            if row is None:
                raise CompactionError(f"duplicate changed during compaction: {item_id}")
            payload = _dump_row(row)
            connection.execute(
                "INSERT INTO reconciliation_archived_items VALUES(?,?,?,?,?)",
                (item_id, run_id, payload, _sha(payload), datetime.now(timezone.utc).isoformat()),
            )
        for offset in range(0, len(plan["compactable_content_ids"]), 500):
            batch = plan["compactable_content_ids"][offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            connection.execute(f"DELETE FROM items WHERE content_id IN ({placeholders})", batch)
        connection.execute(
            "INSERT INTO reconciliation_compaction_runs(run_id,status,archived_count,created_at) VALUES(?,?,?,?)",
            (run_id, "APPLIED", len(plan["compactable_content_ids"]), datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    after = inspect_compaction(database, allow_existing_archive=True)
    for table, before in plan["preserved_tables_before"].items():
        if after["preserved_tables_before"].get(table) != before:
            raise CompactionError(f"preserved historical table changed during compaction: {table}")
    check = _connect(database, readonly=True)
    try:
        archived = check.execute("SELECT COUNT(*) FROM reconciliation_archived_items WHERE run_id=?", (run_id,)).fetchone()[0]
        unresolved = []
        for table in ("state_events", "caption_candidates", "reconciliation_changes"):
            if table in _table_names(check) and "content_id" in _columns(check, table):
                unresolved.append({"table": table, "count": check.execute(
                    f'''SELECT COUNT(*) FROM "{table}" h
                        LEFT JOIN items i ON i.content_id=h.content_id
                        LEFT JOIN reconciliation_archived_items a ON a.content_id=h.content_id
                        WHERE i.content_id IS NULL AND a.content_id IS NULL'''
                ).fetchone()[0]})
        integrity = [row[0] for row in check.execute("PRAGMA quick_check")]
    finally:
        check.close()
    if archived != len(plan["compactable_content_ids"]) or any(row["count"] for row in unresolved) or integrity != ["ok"]:
        raise CompactionError("post-compaction preservation check failed")
    return {
        "run_id": run_id,
        "archived_count": archived,
        "item_count_before": plan["item_count_before"],
        "item_count_after": after["item_count_before"],
        "duplicate_count_after": after["duplicate_count"],
        "preserved_tables_before": plan["preserved_tables_before"],
        "preserved_tables_after": after["preserved_tables_before"],
        "historical_reference_orphans": unresolved,
        "quick_check": integrity,
    }


def rollback_compaction(database: Path, copy_root: Path, run_id: str) -> dict[str, Any]:
    database = Path(database).resolve(strict=True)
    root = Path(copy_root).resolve(strict=True)
    try:
        database.relative_to(root)
    except ValueError as exc:
        raise CompactionError("database must be inside the explicit copy root") from exc
    connection = _connect(database, readonly=False)
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = _table_names(connection)
        if not {"reconciliation_archived_items", "reconciliation_compaction_runs"}.issubset(tables):
            raise CompactionError("no compaction archive exists")
        run = connection.execute(
            "SELECT status,archived_count FROM reconciliation_compaction_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise CompactionError("compaction run not found")
        if run["status"] == "ROLLED_BACK":
            connection.rollback()
            return {"run_id": run_id, "restored_count": 0, "already_rolled_back": True}
        archived = connection.execute(
            "SELECT content_id,item_json,item_sha256 FROM reconciliation_archived_items WHERE run_id=? ORDER BY content_id",
            (run_id,),
        ).fetchall()
        if len(archived) != int(run["archived_count"]):
            raise CompactionError("archive count does not match run manifest")
        columns = _columns(connection, "items")
        quoted = ",".join(f'"{column}"' for column in columns)
        placeholders = ",".join("?" for _ in columns)
        for row in archived:
            payload = str(row["item_json"])
            if _sha(payload) != row["item_sha256"]:
                raise CompactionError(f"archive hash mismatch: {row['content_id']}")
            if connection.execute("SELECT 1 FROM items WHERE content_id=?", (row["content_id"],)).fetchone():
                raise CompactionError(f"item id is already present; refusing overwrite: {row['content_id']}")
            values = _load_row(payload)
            if set(values) != set(columns):
                raise CompactionError("items schema changed since compaction")
            connection.execute(f"INSERT INTO items ({quoted}) VALUES ({placeholders})", [values[c] for c in columns])
        connection.execute(
            "UPDATE reconciliation_compaction_runs SET status='ROLLED_BACK', rolled_back_at=? WHERE run_id=?",
            (datetime.now(timezone.utc).isoformat(), run_id),
        )
        connection.commit()
        return {"run_id": run_id, "restored_count": len(archived), "already_rolled_back": False}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
