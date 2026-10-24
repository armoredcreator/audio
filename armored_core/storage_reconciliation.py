"""Evidence-gated, reversible storage quarantine for an isolated storage copy."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StorageReconciliationError(RuntimeError):
    pass


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (ValueError, OSError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_path(value: object, root: Path) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        return os.path.normcase(os.path.normpath(str(path.resolve(strict=False))))
    except (OSError, RuntimeError, ValueError):
        return None


def _state_file(root: Path) -> Path:
    return root / "storage_reconciliation_state.json"


def _write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_inputs(database: Path, storage_root: Path, diagnostic: Path) -> list[dict[str, Any]]:
    database = Path(database).resolve(strict=True)
    storage_root = Path(storage_root).resolve(strict=True)
    report = json.loads(Path(diagnostic).read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in connection.execute('PRAGMA table_info("items")')}
        path_columns = [name for name in ("original_path", "working_path", "result_path") if name in columns]
        referenced_paths: set[str] = set()
        if path_columns:
            selected = ",".join(f'"{name}"' for name in path_columns)
            for item in connection.execute(f"SELECT {selected} FROM items"):
                referenced_paths.update(
                    value for name in path_columns
                    if (value := _normalized_path(item[name], storage_root)) is not None
                )
        rows = []
        for orphan in report.get("mp4_not_referenced_by_original_working_or_result_path", []):
            if orphan.get("class") != "PRESERVED_BACKUP_MEDIA":
                continue
            content_ids = orphan.get("same_sha256_as_database_originals") or []
            candidates = orphan.get("same_source_message_id_candidates") or []
            if len(content_ids) != 1:
                continue
            content_id = str(content_ids[0])
            matching_candidates = [x for x in candidates if str(x.get("content_id")) == content_id]
            if len(matching_candidates) != 1:
                continue
            item = connection.execute(
                "SELECT content_id,original_path,original_sha256 FROM items WHERE content_id=?",
                (content_id,),
            ).fetchone()
            if item is None:
                continue
            expected_sha = str(item["original_sha256"] or "").casefold()
            candidate_sha = str(orphan.get("actual_sha256") or "").casefold()
            if len(expected_sha) != 64 or candidate_sha != expected_sha:
                continue
            backup = (storage_root / str(orphan.get("relative_path") or "")).resolve(strict=True)
            original_raw = Path(str(item["original_path"] or ""))
            original = original_raw.resolve(strict=True)
            if not _under(backup, storage_root) or not _under(original, storage_root):
                continue
            if not backup.is_file() or not original.is_file():
                continue
            if _normalized_path(backup, storage_root) not in referenced_paths:
                rows.append({
                    "content_id": content_id,
                    "backup_source": str(backup),
                    "canonical_source": str(original),
                    "sha256": expected_sha,
                    "size_bytes": int(orphan.get("size_bytes") or backup.stat().st_size),
                    "class": "confirmed-byte-identical-unreferenced-backup",
                })
        rows.sort(key=lambda row: (row["content_id"], row["backup_source"]))
        return rows
    finally:
        connection.close()


def build_storage_copy_plan(database: Path, source_storage: Path, diagnostic: Path, copy_root: Path) -> dict[str, Any]:
    """Plan copies/quarantine candidates; never changes source storage."""
    source = Path(source_storage).resolve(strict=True)
    root = Path(copy_root).resolve(strict=True)
    return {
        "schema_version": 1,
        "plan_id": str(uuid.uuid4()),
        "source_storage": str(source),
        "copy_root": str(root),
        "candidate_count": len(_load_inputs(database, source, diagnostic)),
        "candidates": _load_inputs(database, source, diagnostic),
        "operation": "copy canonical + redundant file into copy-root, verify both, then move redundant copy to reversible quarantine",
        "source_mutations": 0,
    }


def apply_storage_copy_plan(database: Path, source_storage: Path, diagnostic: Path, copy_root: Path) -> dict[str, Any]:
    source = Path(source_storage).resolve(strict=True)
    root = Path(copy_root).resolve(strict=True)
    if source == root or _under(root, source):
        raise StorageReconciliationError("copy-root must not be inside source storage")
    database = Path(database).resolve(strict=True)
    if not database.is_file():
        raise StorageReconciliationError("database copy is unavailable")
    if "\\armoredcreator-final\\storage\\" in str(database).replace("/", "\\").casefold():
        raise StorageReconciliationError("refusing to read an operational database for storage reconciliation")
    candidates = _load_inputs(database, source, diagnostic)
    signature = [
        {key: row[key] for key in ("content_id", "backup_source", "canonical_source", "sha256")}
        for row in candidates
    ]
    state_path = _state_file(root)
    state = {"schema_version": 1, "source_storage": str(source), "copy_root": str(root), "runs": []}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageReconciliationError("storage reconciliation ledger is unreadable") from exc
        if state.get("source_storage") != str(source) or state.get("copy_root") != str(root):
            raise StorageReconciliationError("storage reconciliation ledger belongs to another root")
        for previous in reversed(state.get("runs", [])):
            if previous.get("status") == "APPLIED" and previous.get("signature") == signature:
                return {**previous["result"], "already_applied": True}
    if any(root.glob(".storage-reconcile-staging-*")):
        raise StorageReconciliationError("incomplete staging output exists; inspect it before retry")
    plan_id = str(uuid.uuid4())
    staging = root / f".storage-reconcile-staging-{plan_id}"
    final_root = root / f"storage-reconciliation-{plan_id}"
    if staging.exists() or final_root.exists():
        raise StorageReconciliationError("storage-copy output already exists")
    records = []
    staging.mkdir()
    try:
        for index, candidate in enumerate(candidates):
            canonical_source = Path(candidate["canonical_source"])
            backup_source = Path(candidate["backup_source"])
            if _sha256(canonical_source) != candidate["sha256"] or _sha256(backup_source) != candidate["sha256"]:
                raise StorageReconciliationError(f"source hash changed since inventory for {candidate['content_id']}")
            safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in candidate["content_id"])
            canonical_copy = staging / "files" / "canonical" / f"{safe_id}-{index}.mp4"
            redundant_copy = staging / "files" / "redundant" / f"{safe_id}-{index}.mp4"
            quarantine_copy = staging / "quarantine" / f"{safe_id}-{index}.mp4"
            canonical_copy.parent.mkdir(parents=True, exist_ok=True)
            redundant_copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(canonical_source, canonical_copy)
            shutil.copy2(backup_source, redundant_copy)
            if _sha256(canonical_copy) != candidate["sha256"] or _sha256(redundant_copy) != candidate["sha256"]:
                raise StorageReconciliationError(f"copied hash verification failed for {candidate['content_id']}")
            quarantine_copy.parent.mkdir(parents=True, exist_ok=True)
            os.replace(redundant_copy, quarantine_copy)
            if _sha256(quarantine_copy) != candidate["sha256"] or not canonical_copy.is_file():
                raise StorageReconciliationError(f"quarantine verification failed for {candidate['content_id']}")
            records.append({
                **candidate,
                "canonical_copy": str(canonical_copy),
                "quarantined_copy": str(quarantine_copy),
                "canonical_sha256_after": _sha256(canonical_copy),
                "quarantined_sha256_after": _sha256(quarantine_copy),
                "source_files_modified": False,
            })
        if any(_sha256(Path(row[key])) != row["sha256"] for row in candidates for key in ("canonical_source", "backup_source")):
            raise StorageReconciliationError("source storage changed during isolated copy operation")
        os.replace(staging, final_root)
    except Exception:
        # Keep the uniquely named staging directory as a recoverable diagnostic artifact.
        raise
    for record in records:
        record["canonical_copy"] = str(final_root / Path(record["canonical_copy"]).relative_to(staging))
        record["quarantined_copy"] = str(final_root / Path(record["quarantined_copy"]).relative_to(staging))
    result = {
        "plan_id": plan_id,
        "source_storage": str(source),
        "copy_root": str(root),
        "storage_copy_root": str(final_root),
        "candidate_count": len(candidates),
        "quarantined_in_copy": len(records),
        "records": records,
        "source_mutations": 0,
    }
    state.setdefault("runs", []).append({"plan_id": plan_id, "status": "APPLIED", "signature": signature, "result": result})
    _write_state(state_path, state)
    return result


def rollback_storage_copy(manifest: Path, copy_root: Path) -> dict[str, Any]:
    root = Path(copy_root).resolve(strict=True)
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data = data["result"]
    state_path = _state_file(root)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else None
    run_id = str(data.get("plan_id") or "")
    if state is not None:
        run = next((row for row in state.get("runs", []) if row.get("plan_id") == run_id), None)
        if run is None:
            raise StorageReconciliationError("storage copy run is absent from its ledger")
        if run.get("status") == "ROLLED_BACK":
            return {"plan_id": run_id, "restored_count": 0, "already_rolled_back": True, "source_mutations": 0}
    storage_copy_root = Path(data.get("storage_copy_root") or "").resolve(strict=True)
    if not _under(storage_copy_root, root):
        raise StorageReconciliationError("storage copy in manifest is outside copy-root")
    restored = 0
    for record in data.get("records", []):
        quarantined = Path(record["quarantined_copy"]).resolve(strict=True)
        canonical = Path(record["canonical_copy"]).resolve(strict=True)
        redundant = storage_copy_root / "files" / "redundant" / quarantined.name
        if not _under(quarantined, storage_copy_root) or not _under(canonical, storage_copy_root) or redundant.exists():
            raise StorageReconciliationError("rollback path or destination validation failed")
        if _sha256(quarantined) != record["sha256"] or _sha256(canonical) != record["sha256"]:
            raise StorageReconciliationError("rollback source hash does not match manifest")
        redundant.parent.mkdir(parents=True, exist_ok=True)
        os.replace(quarantined, redundant)
        if _sha256(redundant) != record["sha256"]:
            raise StorageReconciliationError("rollback verification failed")
        restored += 1
    if state is not None:
        run["status"] = "ROLLED_BACK"
        run["rolled_back_count"] = restored
        _write_state(state_path, state)
    return {"plan_id": run_id, "restored_count": restored, "already_rolled_back": False, "source_mutations": 0, "copy_root": str(root)}
