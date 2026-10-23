from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from armored_core.database import Database
from armored_core.database_compaction import (
    CompactionError,
    compact_duplicates,
    inspect_compaction,
    rollback_compaction,
)


def _reconciled_db(path: Path) -> tuple[str, str]:
    db = Database(path)
    owner = db.reserve_item("1", "source-a", original_url="https://shopee.com.br/p/1/2")
    duplicate = db.reserve_item("2", "source-b", original_url="https://shopee.com.br/p/1/2")
    db.conn.execute(
        "UPDATE items SET state='DUPLICATE',duplicate_of_content_id=? WHERE content_id=?",
        (owner, duplicate),
    )
    db.conn.execute(
        "INSERT INTO state_events(content_id,old_state,new_state,reason) VALUES(?,?,?,?)",
        (duplicate, "RECEIVED", "DUPLICATE", "test-reconcile"),
    )
    db.conn.commit()
    db.close()
    return owner, duplicate


def test_compaction_archives_duplicate_row_and_rolls_back_without_losing_history(tmp_path: Path):
    database = tmp_path / "copy.sqlite3"
    owner, duplicate = _reconciled_db(database)
    connection = sqlite3.connect(database)
    original = connection.execute("SELECT * FROM items WHERE content_id=?", (duplicate,)).fetchone()
    events_before = connection.execute("SELECT * FROM state_events ORDER BY id").fetchall()
    connection.close()

    plan = inspect_compaction(database)
    assert plan["compactable_content_ids"] == [duplicate]
    applied = compact_duplicates(database, tmp_path)
    assert applied["archived_count"] == 1
    assert applied["item_count_after"] == applied["item_count_before"] - 1
    assert all(row["count"] == 0 for row in applied["historical_reference_orphans"])
    assert applied["quick_check"] == ["ok"]

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM items WHERE content_id=?", (duplicate,)).fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM items WHERE content_id=?", (owner,)).fetchone()[0] == 1
    archived = connection.execute(
        "SELECT item_json,item_sha256 FROM reconciliation_archived_items WHERE content_id=?",
        (duplicate,),
    ).fetchone()
    assert archived is not None and duplicate in archived[0] and archived[1]
    assert connection.execute("SELECT * FROM state_events ORDER BY id").fetchall() == events_before
    connection.close()

    second_apply = compact_duplicates(database, tmp_path)
    assert second_apply["already_compact"] is True
    assert second_apply["archived_count"] == 0

    restored = rollback_compaction(database, tmp_path, applied["run_id"])
    assert restored["restored_count"] == 1
    assert rollback_compaction(database, tmp_path, applied["run_id"])["already_rolled_back"] is True
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT * FROM items WHERE content_id=?", (duplicate,)).fetchone() == original
    assert connection.execute("SELECT * FROM state_events ORDER BY id").fetchall() == events_before
    connection.close()


def test_compaction_refuses_publication_rows_and_unreviewed_content_references(tmp_path: Path):
    database = tmp_path / "copy.sqlite3"
    owner, duplicate = _reconciled_db(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO publications(content_id,idempotency_key,verification_status) VALUES(?,?,?)",
        (duplicate, "duplicate-publication-key", "SENT_UNVERIFIED"),
    )
    connection.commit()
    connection.close()
    report = inspect_compaction(database)
    assert report["compactable_count"] == 0
    assert {row["reason"] for row in report["blocked"]} == {"publication-record-preserved"}
    with pytest.raises(CompactionError, match="blocked rows"):
        compact_duplicates(database, tmp_path)

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM publications WHERE content_id=?", (duplicate,))
    connection.execute("CREATE TABLE external_history (content_id TEXT NOT NULL)")
    connection.execute("INSERT INTO external_history VALUES(?)", (duplicate,))
    connection.commit()
    connection.close()
    report = inspect_compaction(database)
    assert report["unknown_content_id_reference_tables"] == ["external_history"]
    assert any(row["reason"] == "unreviewed-content-id-reference-table" for row in report["blocked"])


def test_compaction_requires_explicit_copy_root_and_refuses_operational_path(tmp_path: Path):
    database = tmp_path / "outside.sqlite3"
    _reconciled_db(database)
    other_root = tmp_path / "elsewhere"
    other_root.mkdir()
    with pytest.raises(CompactionError, match="inside the explicit copy root"):
        compact_duplicates(database, other_root)

    operational = tmp_path / "ArmoredCreator-final" / "storage" / "armoredcreator.db"
    _reconciled_db(operational)
    with pytest.raises(CompactionError, match="operational database"):
        compact_duplicates(operational, tmp_path)
