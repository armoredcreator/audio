from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from armored_core.models import State
from armored_core.shopee_urls import extract_shopee_product_identity


CONFIRMED_STATUSES = {"CONFIRMED", "VERIFIED", "PUBLISHED", "DELIVERED"}
UNVERIFIED_STATUSES = {"PENDING", "SENT_UNVERIFIED", "UNKNOWN", "SENDING"}
STATE_PROGRESS = {
    "PUBLISHED": 9, "PUBLISHING": 8, "STUDIO_COMPLETE": 7, "STUDIO_BATCH": 6,
    "STUDIO": 5, "SPEECH": 4, "IA": 3, "RECOVERY": 2, "VISION": 1,
    "RECEIVED": 0, "WAITING_VISION": -1, "FAILED": -2, "DUPLICATE": -3,
}


class ReconciliationError(RuntimeError):
    pass


def _yes(value: object) -> bool:
    if value is True or (isinstance(value, (int, float)) and value == 1):
        return True
    return isinstance(value, str) and value.strip().casefold() in {
        "1", "true", "yes", "y", "confirmed",
    }


def _jsonl_manifest(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReconciliationError(f"invalid media manifest at line {line_no}") from exc
                key = str(row.get("content_id") or "").strip()
                if key:
                    result[key] = row
    return result


def _product_identity(row: sqlite3.Row) -> str | None:
    try:
        data = dict(row)
    except (TypeError, ValueError):
        return None
    raw_context = str(data.get("ia_context_json") or "").strip()
    if raw_context:
        try:
            context = json.loads(raw_context)
        except (TypeError, ValueError, json.JSONDecodeError):
            context = None
        if isinstance(context, dict):
            shop_id = str(context.get("shopId") or context.get("shop_id") or "").strip()
            item_id = str(context.get("itemId") or context.get("item_id") or "").strip()
            if shop_id and item_id:
                return f"{shop_id}:{item_id}"
    return extract_shopee_product_identity(data.get("original_url"))


class _UnionFind:
    def __init__(self, values):
        self.parent = {value: value for value in values}

    def find(self, value):
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left, right):
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _publication_evidence(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "publications" not in tables:
        return {}
    cols = {row[1] for row in connection.execute('PRAGMA table_info("publications")')}
    if "content_id" not in cols:
        return {}
    selected = ["content_id"]
    for name, default in (
        ("confirmed", "0"), ("published_message_id", "NULL"),
        ("verification_status", "''"), ("verified_at", "NULL"),
    ):
        selected.append(name if name in cols else f"{default} AS {name}")
    output = {}
    for row in connection.execute("SELECT " + ",".join(selected) + " FROM publications"):
        item = dict(row)
        status = str(item.get("verification_status") or "").strip().upper()
        has_message_id = bool(str(item.get("published_message_id") or "").strip())
        verified = False if status in UNVERIFIED_STATUSES else (
            status in CONFIRMED_STATUSES
            or (_yes(item.get("confirmed")) and has_message_id and not status)
        )
        item["confirmed_evidence"] = verified
        output[str(item["content_id"])] = item
    return output


def _valid_original(content_id: str, row: sqlite3.Row, media: dict[str, dict[str, Any]]) -> bool:
    entry = media.get(content_id)
    if entry is not None:
        match = str(entry.get("sha256_match") or "").upper()
        exists = entry.get("original_exists") is True
        return exists and match == "MATCH" and bool(entry.get("file_stable_during_hash", True))
    path = Path(str(row["original_path"] or ""))
    return path.is_file()


def _speech_complete(connection: sqlite3.Connection, content_id: str) -> bool:
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "state_events" not in tables:
        return False
    for row in connection.execute(
        "SELECT reason FROM state_events WHERE content_id=? ORDER BY id", (content_id,)
    ):
        reason = str(row[0] or "").casefold()
        if "speech-studio-v5-complete" in reason or "speech_studio_v5_complete" in reason:
            return True
    return False


def _valid_result(content_id: str, row: sqlite3.Row, media: dict[str, dict[str, Any]]) -> bool:
    entry = media.get(content_id)
    if entry is not None and "result_path_exists" in entry:
        return entry.get("result_path_exists") is True
    path = Path(str(row["result_path"] or "")) if "result_path" in row.keys() else Path()
    return bool(str(row["result_path"] or "").strip()) and path.is_file()


def build_plan(
    database: Path,
    media_manifest: Path | None = None,
    storage_diagnostic: Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic, mutation-free plan for duplicate and recovery repair."""
    db = sqlite3.connect(f"file:{Path(database).resolve(strict=True).as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "items" not in tables:
            raise ReconciliationError("database has no items table")
        columns = {row[1] for row in db.execute('PRAGMA table_info("items")')}
        required = {"content_id", "state", "original_url", "source_id", "telegram_message_id"}
        if not required.issubset(columns):
            raise ReconciliationError(f"items schema missing: {sorted(required - columns)}")
        rows = db.execute("SELECT * FROM items ORDER BY content_id").fetchall()
        by_id = {str(row["content_id"]): row for row in rows}
        media = _jsonl_manifest(media_manifest)
        missing_originals: dict[str, int] = defaultdict(int)
        hash_mismatches: dict[str, int] = defaultdict(int)
        for item_id, evidence in media.items():
            row = by_id.get(item_id)
            state = str(row["state"] or "UNKNOWN") if row is not None else "UNKNOWN"
            if evidence.get("original_exists") is not True:
                missing_originals[state] += 1
            elif str(evidence.get("sha256_match") or "").upper() != "MATCH":
                hash_mismatches[state] += 1
        storage_report = None
        if storage_diagnostic is not None:
            try:
                storage_report = json.loads(Path(storage_diagnostic).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReconciliationError("storage diagnostic is unreadable") from exc
        unreferenced_media = []
        if isinstance(storage_report, dict):
            for orphan in storage_report.get("mp4_not_referenced_by_original_working_or_result_path", []):
                candidates = orphan.get("same_source_message_id_candidates") or []
                unreferenced_media.append({
                    "path": orphan.get("relative_path") or orphan.get("path"),
                    "sha256": orphan.get("actual_sha256"),
                    "candidate_content_ids": [str(item.get("content_id") or "") for item in candidates],
                    "decision": "unresolved-no-recorded-original-hash" if candidates else "unreferenced-no-message-match",
                })
        publications = _publication_evidence(db)
        identity_groups: dict[str, set[str]] = defaultdict(set)
        url_groups: dict[str, set[str]] = defaultdict(set)
        for item_id, row in by_id.items():
            url = str(row["original_url"] or "").strip().casefold()
            if url:
                url_groups[url].add(item_id)
            key = _product_identity(row)
            if key:
                identity_groups[key].add(item_id)

        union = _UnionFind(by_id)
        raw_groups = []
        for kind, groups in (("url", url_groups), ("product", identity_groups)):
            for key, members in sorted(groups.items()):
                if len(members) < 2:
                    continue
                ordered = sorted(members)
                for content_id in ordered[1:]:
                    union.union(ordered[0], content_id)
                raw_groups.append({"kind": kind, "key": key, "content_ids": ordered})
        components: dict[str, set[str]] = defaultdict(set)
        for content_id in by_id:
            components[union.find(content_id)].add(content_id)

        actions: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        duplicate_components = []
        for members in sorted((m for m in components.values() if len(m) > 1), key=lambda m: sorted(m)):
            component = sorted(members)
            active = [item_id for item_id in component if str(by_id[item_id]["state"]) != State.DUPLICATE.value]
            if not active:
                active = component
            uncertain = []
            for item_id in component:
                row = by_id[item_id]
                pub = publications.get(item_id, {})
                status = str(pub.get("verification_status") or "").upper()
                if (
                    str(row["state"]) == State.PUBLISHING.value
                    and not pub.get("confirmed_evidence")
                    and status not in {"ABSENT", "NOT_FOUND", "FAILED_PRE_SEND"}
                ):
                    uncertain.append(item_id)
                elif pub and not pub.get("confirmed_evidence") and status not in {"ABSENT", "NOT_FOUND", "FAILED_PRE_SEND"}:
                    uncertain.append(item_id)
            if uncertain:
                blocked.append({"reason": "publication-outcome-indeterminate", "content_ids": sorted(set(uncertain)), "component": component})
                continue

            def rank(item_id: str):
                row = by_id[item_id]
                pub = publications.get(item_id, {})
                state = str(row["state"] or "")
                vision = bool(str(row["affiliate_url"] or "").strip()) if "affiliate_url" in columns else False
                created = str(row["created_at"] or "") if "created_at" in columns else ""
                return (
                    -int(bool(pub.get("confirmed_evidence")) or state == State.PUBLISHED.value),
                    -STATE_PROGRESS.get(state, -10),
                    -int(vision),
                    -int(_valid_original(item_id, row, media)),
                    created,
                    item_id,
                )

            owner = min(active, key=rank)
            reasons = sorted({g["kind"] for g in raw_groups if set(g["content_ids"]) & members})
            duplicate_components.append({"canonical_content_id": owner, "content_ids": component, "match_types": reasons})
            for item_id in component:
                if item_id == owner:
                    continue
                row = by_id[item_id]
                state = str(row["state"] or "")
                pub = publications.get(item_id, {})
                is_published = state == State.PUBLISHED.value or bool(pub.get("confirmed_evidence"))
                if is_published:
                    # Proven Telegram publications stay terminal and keep their receipt.
                    if state != State.PUBLISHED.value:
                        actions.append({"kind": "restore-confirmed-publication", "content_id": item_id, "owner_content_id": owner, "from_state": state, "to_state": State.PUBLISHED.value})
                    continue
                duplicate_of = str(row["duplicate_of_content_id"] or "") if "duplicate_of_content_id" in columns else ""
                if state != State.DUPLICATE.value or duplicate_of != owner:
                    actions.append({"kind": "mark-duplicate", "content_id": item_id, "owner_content_id": owner, "from_state": state, "to_state": State.DUPLICATE.value})

        recovery_actions = []
        for item_id, row in by_id.items():
            state = str(row["state"] or "")
            if state not in {State.IA.value, State.SPEECH.value, State.STUDIO_BATCH.value, State.STUDIO.value, State.STUDIO_COMPLETE.value, State.PUBLISHING.value, State.RECOVERY.value, State.FAILED.value}:
                continue
            if any(item_id in component["content_ids"] and item_id != component["canonical_content_id"] for component in duplicate_components):
                continue
            pub = publications.get(item_id, {})
            if state == State.PUBLISHED.value or pub.get("confirmed_evidence"):
                if state != State.PUBLISHED.value:
                    recovery_actions.append({"kind": "restore-confirmed-publication", "content_id": item_id, "from_state": state, "to_state": State.PUBLISHED.value})
                continue
            pub_status = str(pub.get("verification_status") or "").upper()
            if (
                state == State.PUBLISHING.value
                and pub_status not in {"ABSENT", "NOT_FOUND", "FAILED_PRE_SEND"}
            ) or (pub and not pub.get("confirmed_evidence") and pub_status not in {"ABSENT", "NOT_FOUND", "FAILED_PRE_SEND"}):
                blocked.append({"reason": "publication-outcome-indeterminate", "content_ids": [item_id]})
                continue
            if not _valid_original(item_id, row, media):
                blocked.append({"reason": "original-not-verified", "content_ids": [item_id]})
                continue
            if state in {State.IA.value, State.SPEECH.value}:
                target = state
            elif state in {
                State.STUDIO_BATCH.value, State.STUDIO.value, State.STUDIO_COMPLETE.value,
                State.PUBLISHING.value, State.RECOVERY.value, State.FAILED.value,
            }:
                has_caption = (
                    bool(str(row["publication_caption"] or "").strip())
                    if "publication_caption" in columns else False
                )
                if not str(row["affiliate_url"] or "").strip():
                    target = State.VISION.value
                elif not has_caption:
                    target = State.IA.value
                elif _speech_complete(db, item_id) and _valid_result(item_id, row, media):
                    target = State.PUBLISHING.value
                else:
                    # Speech v5 batch resumes its persisted package when available;
                    # a missing/invalid final render must not be sent to Hub.
                    target = State.SPEECH.value
            else:
                target = state
            if target != state:
                recovery_actions.append({"kind": "recover-stage", "content_id": item_id, "from_state": state, "to_state": target})

        path_repairs = []
        for item_id, row in by_id.items():
            for field in ("working_path", "result_path"):
                if field not in columns:
                    continue
                raw = str(row[field] or "").strip()
                if not raw:
                    continue
                path = Path(raw)
                sentinel = path.name.casefold() in {"none", "null", "undefined"}
                if sentinel or not path.is_file():
                    # A vanished derived file is never reused; a literal sentinel is an invalid DB reference.
                    if sentinel:
                        path_repairs.append({"kind": "clear-invalid-derived-reference", "content_id": item_id, "field": field, "value": raw})

        all_actions = actions + recovery_actions
        return {
            "schema_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": str(Path(database).resolve()),
            "item_count": len(rows),
            "source_ids": sorted({str(row["source_id"] or "telegram") for row in rows}),
            "source_count": len({str(row["source_id"] or "telegram") for row in rows}),
            "raw_duplicate_groups": len(raw_groups),
            "duplicate_components": duplicate_components,
            "duplicate_actions": actions,
            "recovery_actions": recovery_actions,
            "path_repairs": path_repairs,
            "storage_audit": {
                "manifest_rows": len(media),
                "missing_originals_by_state": dict(sorted(missing_originals.items())),
                "original_hash_mismatches_by_state": dict(sorted(hash_mismatches.items())),
                "unreferenced_mp4_count": len(unreferenced_media),
                "unreferenced_mp4": unreferenced_media,
                "relinked_media_count": 0,
                "unresolved_policy": "No storage association is changed without exact source/message and matching recorded SHA-256 evidence.",
            },
            "blocked": blocked,
            "action_count": len(all_actions) + len(path_repairs),
            "notes": [
                "Only exact normalized source URLs and exact shop_id:item_id are merged.",
                "Published items retain publication receipts and terminal PUBLISHED state.",
                "Unknown or sent-unverified Telegram outcomes block automatic recovery.",
                "No checkpoint, media bytes, or publication rows are changed.",
                "Storage path repair is limited to clearing invalid literal sentinels; ambiguous media relinking is reported, never guessed.",
            ],
        }
    finally:
        db.close()


def _is_protected_operational_path(database: Path) -> bool:
    value = str(Path(database).resolve()).replace("/", "\\").casefold()
    return "\\armoredcreator-final\\storage\\" in value or Path(database).name.casefold() == "armoredcreator.db"


def apply_plan(database: Path, plan: dict[str, Any], copy_root: Path) -> dict[str, Any]:
    database = Path(database).resolve(strict=True)
    root = Path(copy_root).resolve(strict=True)
    try:
        database.relative_to(root)
    except ValueError as exc:
        raise ReconciliationError("apply target must be inside the explicit copy root") from exc
    if _is_protected_operational_path(database):
        raise ReconciliationError("refusing to mutate an operational ArmoredCreator database")
    check = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        result = [row[0] for row in check.execute("PRAGMA quick_check")]
        if result != ["ok"]:
            raise ReconciliationError(f"copy quick_check failed: {result[:5]}")
    finally:
        check.close()

    changes = []
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        item_columns = {row[1] for row in connection.execute('PRAGMA table_info("items")')}
        if "duplicate_of_content_id" not in item_columns:
            connection.execute("ALTER TABLE items ADD COLUMN duplicate_of_content_id TEXT")
        connection.execute("""CREATE TABLE IF NOT EXISTS reconciliation_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            content_id TEXT NOT NULL,
            field_group TEXT NOT NULL, old_values_json TEXT NOT NULL,
            new_values_json TEXT NOT NULL, reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        change_columns = {row[1] for row in connection.execute('PRAGMA table_info("reconciliation_changes")')}
        if "run_id" not in change_columns:
            connection.execute("ALTER TABLE reconciliation_changes ADD COLUMN run_id TEXT")
        connection.execute("""CREATE TABLE IF NOT EXISTS reconciliation_runs (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL,
            planned_at TEXT NOT NULL, finished_at TEXT
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS reconciliation_meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        now_run = str(plan.get("created_at_utc") or "")
        pending_count = len(plan.get("duplicate_actions", [])) + len(plan.get("recovery_actions", [])) + len(plan.get("path_repairs", []))
        run_id = uuid.uuid4().hex if pending_count else None
        if run_id:
            connection.execute(
                "INSERT INTO reconciliation_runs(run_id,status,planned_at) VALUES(?,?,?)",
                (run_id, "APPLYING", now_run),
            )
        for action in plan.get("duplicate_actions", []) + plan.get("recovery_actions", []):
            item_id = action["content_id"]
            row = connection.execute("SELECT * FROM items WHERE content_id=?", (item_id,)).fetchone()
            if row is None:
                raise ReconciliationError(f"item disappeared from copy: {item_id}")
            state = str(row["state"] or "")
            if action["kind"] == "mark-duplicate":
                new_state = State.DUPLICATE.value
                owner = action["owner_content_id"]
                last_error = f"historical-reconcile:{owner}"
            elif action["kind"] == "restore-confirmed-publication":
                new_state = State.PUBLISHED.value
                owner = row["duplicate_of_content_id"] if "duplicate_of_content_id" in row.keys() else None
                last_error = row["last_error"] if "last_error" in row.keys() else None
            else:
                new_state = action["to_state"]
                owner = row["duplicate_of_content_id"] if "duplicate_of_content_id" in row.keys() else None
                last_error = row["last_error"] if "last_error" in row.keys() else None
            old = {key: row[key] for key in ("state", "duplicate_of_content_id", "last_error", "updated_at") if key in row.keys()}
            new = {"state": new_state, "duplicate_of_content_id": owner, "last_error": last_error}
            if old == new:
                continue
            connection.execute(
                "INSERT INTO reconciliation_changes(run_id,content_id,field_group,old_values_json,new_values_json,reason) VALUES(?,?,?,?,?,?)",
                (run_id, item_id, "item-state", json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), action["kind"]),
            )
            connection.execute(
                "UPDATE items SET state=?,duplicate_of_content_id=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE content_id=?",
                (new_state, owner, last_error, item_id),
            )
            if state != new_state:
                connection.execute(
                    "INSERT INTO state_events(content_id,old_state,new_state,reason) VALUES(?,?,?,?)",
                    (item_id, state, new_state, f"historical-reconcile:{action['kind']}:{owner or item_id}"),
                )
            changes.append({"content_id": item_id, "kind": action["kind"], "from_state": state, "to_state": new_state, "old_values": old, "new_values": new})

        for action in plan.get("path_repairs", []):
            item_id, field = action["content_id"], action["field"]
            row = connection.execute(f"SELECT {field} FROM items WHERE content_id=?", (item_id,)).fetchone()
            old_value = row[0] if row else None
            if old_value is None or not str(old_value).strip():
                continue
            updated_row = connection.execute("SELECT updated_at FROM items WHERE content_id=?", (item_id,)).fetchone()
            old_values = {field: old_value}
            if updated_row and "updated_at" in updated_row.keys():
                old_values["updated_at"] = updated_row["updated_at"]
            connection.execute(
                "INSERT INTO reconciliation_changes(run_id,content_id,field_group,old_values_json,new_values_json,reason) VALUES(?,?,?,?,?,?)",
                (run_id, item_id, "storage-reference", json.dumps(old_values), json.dumps({field: None}), "clear-invalid-literal-path"),
            )
            connection.execute(f"UPDATE items SET {field}=NULL,updated_at=CURRENT_TIMESTAMP WHERE content_id=?", (item_id,))
            changes.append({"content_id": item_id, "kind": "clear-invalid-derived-reference", "field": field, "old_values": old_values, "new_values": {field: None}})
        if changes:
            connection.execute(
                "UPDATE reconciliation_runs SET status='APPLIED',finished_at=CURRENT_TIMESTAMP WHERE run_id=?",
                (run_id,),
            )
            connection.execute(
                "INSERT INTO reconciliation_meta(key,value) VALUES('last_run',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now_run,),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"run_id": run_id, "applied_count": len(changes), "changes": changes}


def rollback_run(database: Path, run_id: str, copy_root: Path) -> dict[str, Any]:
    """Restore recorded pre-images on a disposable database copy, keeping event history append-only."""
    database = Path(database).resolve(strict=True)
    root = Path(copy_root).resolve(strict=True)
    try:
        database.relative_to(root)
    except ValueError as exc:
        raise ReconciliationError("rollback target must be inside the explicit copy root") from exc
    if _is_protected_operational_path(database):
        raise ReconciliationError("refusing to roll back an operational ArmoredCreator database")
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    restored = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        run = connection.execute("SELECT status FROM reconciliation_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ReconciliationError(f"unknown reconciliation run: {run_id}")
        if run["status"] == "ROLLED_BACK":
            connection.commit()
            return {"run_id": run_id, "restored_count": 0, "already_rolled_back": True, "changes": []}
        if run["status"] != "APPLIED":
            raise ReconciliationError(f"run is not rollback eligible: {run['status']}")
        records = connection.execute(
            "SELECT * FROM reconciliation_changes WHERE run_id=? ORDER BY id DESC", (run_id,)
        ).fetchall()
        allowed = {"state", "duplicate_of_content_id", "last_error", "working_path", "result_path", "updated_at"}
        for record in records:
            old = json.loads(record["old_values_json"])
            keys = [key for key in old if key in allowed]
            if not keys:
                continue
            assignments = ",".join(f'"{key}"=?' for key in keys)
            if "updated_at" not in keys:
                assignments += ",updated_at=CURRENT_TIMESTAMP"
            connection.execute(
                f"UPDATE items SET {assignments} WHERE content_id=?",
                [old[key] for key in keys] + [record["content_id"]],
            )
            if "state" in old:
                new_values = json.loads(record["new_values_json"])
                connection.execute(
                    "INSERT INTO state_events(content_id,old_state,new_state,reason) VALUES(?,?,?,?)",
                    (record["content_id"], new_values.get("state"), old["state"], f"historical-reconcile-rollback:{run_id}"),
                )
            restored.append(record["content_id"])
        connection.execute(
            "UPDATE reconciliation_runs SET status='ROLLED_BACK',finished_at=CURRENT_TIMESTAMP WHERE run_id=?",
            (run_id,),
        )
        connection.commit()
        return {"run_id": run_id, "restored_count": len(restored), "already_rolled_back": False, "changes": restored}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def database_summary(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{Path(database).resolve().as_posix()}?mode=ro", uri=True)
    try:
        result = {"quick_check": [row[0] for row in connection.execute("PRAGMA quick_check")], "item_count": connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]}
        result["states"] = dict(connection.execute("SELECT state,COUNT(*) FROM items GROUP BY state").fetchall())
        result["duplicate_links"] = connection.execute("SELECT COUNT(*) FROM items WHERE state='DUPLICATE' AND duplicate_of_content_id IS NOT NULL").fetchone()[0] if "duplicate_of_content_id" in {r[1] for r in connection.execute('PRAGMA table_info("items")')} else 0
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def table_evidence(table: str) -> dict[str, Any]:
            rows = [list(row) for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()]
            rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")))
            payload = json.dumps(rows, ensure_ascii=False, default=str, separators=(",", ":"))
            return {"count": len(rows), "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}

        result["state_events"] = table_evidence("state_events") if "state_events" in tables else {"count": 0, "sha256": None}
        result["publications"] = table_evidence("publications") if "publications" in tables else {"count": 0, "sha256": None}
        result["checkpoints"] = {
            table: table_evidence(table)
            for table in ("sync_state", "sync_topics", "sync_source_topics", "sync_sources")
            if table in tables
        }
        return result
    finally:
        connection.close()
