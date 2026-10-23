from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from armored_core.database import Database
from armored_core.storage_reconciliation import (
    apply_storage_copy_plan,
    build_storage_copy_plan,
    rollback_storage_copy,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path):
    storage = tmp_path / "source-storage"
    canonical = storage / "Videos GRUPO_FONTE_1" / "3" / "canonical.mp4"
    backup = storage / "database" / "backups" / "3" / "backup.mp4"
    canonical.parent.mkdir(parents=True)
    backup.parent.mkdir(parents=True)
    payload = b"byte-identical media evidence"
    canonical.write_bytes(payload)
    backup.write_bytes(payload)
    database = tmp_path / "copy.sqlite3"
    db = Database(database)
    item_id = db.reserve_item("3", "source", original_url="https://shopee.com.br/p/1/3", original_path=canonical)
    db.conn.execute("UPDATE items SET original_sha256=? WHERE content_id=?", (_sha(canonical), item_id))
    db.conn.commit()
    db.close()
    diagnostic = tmp_path / "diagnostic.json"
    diagnostic.write_text(json.dumps({
        "mp4_not_referenced_by_original_working_or_result_path": [{
            "class": "PRESERVED_BACKUP_MEDIA",
            "relative_path": str(backup.relative_to(storage)),
            "actual_sha256": _sha(backup),
            "size_bytes": len(payload),
            "same_sha256_as_database_originals": [item_id],
            "same_source_message_id_candidates": [{
                "content_id": item_id,
                "original_path": str(canonical),
                "original_sha256": _sha(canonical),
            }],
        }],
    }), encoding="utf-8")
    return storage, canonical, backup, database, diagnostic, item_id


def test_storage_quarantine_only_moves_verified_copies_and_is_reversible(tmp_path: Path):
    storage, canonical, backup, database, diagnostic, item_id = _fixture(tmp_path)
    before = (_sha(canonical), _sha(backup))
    copy_root = tmp_path / "storage-copy"
    copy_root.mkdir()
    plan = build_storage_copy_plan(database, storage, diagnostic, copy_root)
    assert plan["candidate_count"] == 1
    result = apply_storage_copy_plan(database, storage, diagnostic, copy_root)
    assert result["quarantined_in_copy"] == 1
    assert apply_storage_copy_plan(database, storage, diagnostic, copy_root)["already_applied"] is True
    record = result["records"][0]
    assert record["content_id"] == item_id
    assert Path(record["canonical_copy"]).is_file()
    assert Path(record["quarantined_copy"]).is_file()
    assert _sha(canonical) == before[0] and _sha(backup) == before[1]

    manifest = tmp_path / "storage-manifest.json"
    manifest.write_text(json.dumps(result), encoding="utf-8")
    rollback = rollback_storage_copy(manifest, copy_root)
    assert rollback["restored_count"] == 1
    assert rollback_storage_copy(manifest, copy_root)["already_rolled_back"] is True
    assert (Path(result["storage_copy_root"]) / "files" / "redundant" / Path(record["quarantined_copy"]).name).is_file()
    assert _sha(canonical) == before[0] and _sha(backup) == before[1]


def test_storage_copy_plan_requires_exact_hash_and_unique_identity(tmp_path: Path):
    storage, canonical, backup, database, diagnostic, _ = _fixture(tmp_path)
    data = json.loads(diagnostic.read_text(encoding="utf-8"))
    data["mp4_not_referenced_by_original_working_or_result_path"][0]["actual_sha256"] = "0" * 64
    diagnostic.write_text(json.dumps(data), encoding="utf-8")
    root = tmp_path / "copy"
    root.mkdir()
    plan = build_storage_copy_plan(database, storage, diagnostic, root)
    assert plan["candidate_count"] == 0
    assert canonical.is_file() and backup.is_file()


def test_storage_copy_plan_refuses_media_referenced_by_current_database(tmp_path: Path):
    storage, canonical, backup, database, diagnostic, item_id = _fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE items SET working_path=? WHERE content_id=?", (str(backup), item_id))
    root = tmp_path / "copy"
    root.mkdir()
    plan = build_storage_copy_plan(database, storage, diagnostic, root)
    assert plan["candidate_count"] == 0
    assert backup.is_file() and canonical.is_file()


def test_storage_copy_plan_never_allows_output_inside_source_storage(tmp_path: Path):
    storage, _, _, database, diagnostic, _ = _fixture(tmp_path)
    nested = storage / "copy"
    nested.mkdir()
    try:
        apply_storage_copy_plan(database, storage, diagnostic, nested)
    except Exception as exc:
        assert "inside source storage" in str(exc)
    else:
        raise AssertionError("expected copy-root safety guard")
