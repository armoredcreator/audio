from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from armored_core.database import Database
from armored_core.historical_reconciliation import (
    ReconciliationError,
    apply_plan,
    build_plan,
    database_summary,
    rollback_run,
)
from armored_core.models import State
from scripts.audit_sync_duplicates import product_identity_from_direct_url


def _media_manifest(path: Path, content_id: str, original: Path) -> Path:
    path.write_text(json.dumps({
        "content_id": content_id,
        "original_path": str(original),
        "original_exists": True,
        "file_stable_during_hash": True,
        "sha256_match": "MATCH",
        "result_path_exists": False,
    }) + "\n", encoding="utf-8")
    return path


def test_direct_shopee_identity_recognizes_supported_short_domain():
    assert product_identity_from_direct_url("https://shope.ee/product/123/456") == "123:456"


def test_duplicate_reconciliation_is_reversible_and_second_pass_is_noop(tmp_path: Path):
    db_path = tmp_path / "copy.sqlite3"
    db = Database(db_path)
    owner = db.reserve_item("100", "source-a", original_url="https://shopee.com.br/shared")
    duplicate = db.reserve_item("200", "source-b", original_url="https://shopee.com.br/other")
    db.set_vision(owner, "owner", "https://affiliate.invalid/owner", ia_context={"shopId": "1", "itemId": "2"})
    db.conn.execute("UPDATE items SET original_url='https://shopee.com.br/shared' WHERE content_id=?", (duplicate,))
    db.conn.commit()
    event_count_before = db.conn.execute("SELECT COUNT(*) FROM state_events").fetchone()[0]
    db.close()

    plan = build_plan(db_path)
    assert plan["action_count"] == 1
    applied = apply_plan(db_path, plan, tmp_path)
    assert applied["applied_count"] == 1
    assert applied["run_id"]

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT state,duplicate_of_content_id FROM items WHERE content_id=?", (duplicate,)).fetchone()
    assert row == (State.DUPLICATE.value, owner)
    assert conn.execute("SELECT COUNT(*) FROM state_events").fetchone()[0] == event_count_before + 1
    assert conn.execute("SELECT COUNT(*) FROM reconciliation_changes").fetchone()[0] == 1
    conn.close()

    second_plan = build_plan(db_path)
    assert second_plan["action_count"] == 0
    assert apply_plan(db_path, second_plan, tmp_path)["applied_count"] == 0

    rollback = rollback_run(db_path, applied["run_id"], tmp_path)
    assert rollback["restored_count"] == 1
    assert rollback_run(db_path, applied["run_id"], tmp_path)["already_rolled_back"] is True
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT state,duplicate_of_content_id FROM items WHERE content_id=?", (duplicate,)).fetchone() == ("RECEIVED", None)
    conn.close()


def test_confirmed_publication_is_preserved_and_unknown_outcome_blocks_component(tmp_path: Path):
    db_path = tmp_path / "copy.sqlite3"
    db = Database(db_path)
    published = db.reserve_item("1", "one", original_url="https://shopee.com.br/group")
    uncertain = db.reserve_item("2", "two", original_url="https://shopee.com.br/group")
    other = db.reserve_item("3", "three", original_url="https://shopee.com.br/group")
    db.conn.execute("UPDATE items SET state='PUBLISHED' WHERE content_id=?", (published,))
    db.conn.execute("UPDATE items SET state='PUBLISHING' WHERE content_id=?", (uncertain,))
    db.conn.execute("UPDATE items SET state='RECEIVED',duplicate_of_content_id=NULL WHERE content_id=?", (other,))
    db.conn.execute(
        "INSERT INTO publications(content_id,idempotency_key,published_message_id,confirmed,verification_status) VALUES(?,?,?,?,?)",
        (published, "published-key", "987", 1, "CONFIRMED"),
    )
    db.conn.execute(
        "INSERT INTO publications(content_id,idempotency_key,confirmed,verification_status) VALUES(?,?,?,?)",
        # Conflicting legacy flag must not override the explicit unresolved status.
        (uncertain, "unknown-key", 1, "SENT_UNVERIFIED"),
    )
    db.conn.commit()
    db.close()

    plan = build_plan(db_path)
    assert any(row["reason"] == "publication-outcome-indeterminate" for row in plan["blocked"])
    assert not plan["duplicate_actions"]
    assert apply_plan(db_path, plan, tmp_path)["applied_count"] == 0
    summary = database_summary(db_path)
    assert summary["states"]["PUBLISHED"] == 1
    assert summary["states"]["PUBLISHING"] == 1
    assert summary["states"]["RECEIVED"] == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT published_message_id,confirmed FROM publications WHERE content_id=?", (published,)).fetchone() == ("987", 1)
    conn.close()


def test_unfinished_stage_routes_to_earliest_missing_stage_and_clears_bad_path(tmp_path: Path):
    db_path = tmp_path / "copy.sqlite3"
    original = tmp_path / "original.mp4"
    original.write_bytes(b"verified original")
    db = Database(db_path)
    item = db.reserve_item("391", "source", original_url="https://shopee.com.br/product/10/20", original_path=original)
    db.set_vision(item, "product", "https://affiliate.invalid/item", ia_context={"shopId": "10", "itemId": "20"})
    db.conn.execute(
        "UPDATE items SET state='STUDIO',publication_caption=NULL,result_path=?,working_path=? WHERE content_id=?",
        (str(tmp_path / "None"), str(tmp_path / "None"), item),
    )
    db.conn.commit()
    db.close()
    manifest = _media_manifest(tmp_path / "media.jsonl", item, original)
    storage_diagnostic = tmp_path / "storage.json"
    storage_diagnostic.write_text(json.dumps({
        "mp4_not_referenced_by_original_working_or_result_path": [{
            "relative_path": "backup/candidate.mp4",
            "actual_sha256": "a" * 64,
            "same_source_message_id_candidates": [{"content_id": item, "recorded_sha256": None}],
        }],
    }), encoding="utf-8")

    plan = build_plan(db_path, manifest, storage_diagnostic)
    recovery = next(action for action in plan["recovery_actions"] if action["content_id"] == item)
    assert recovery["to_state"] == State.IA.value
    assert len(plan["path_repairs"]) == 2
    assert plan["storage_audit"]["unreferenced_mp4_count"] == 1
    assert plan["storage_audit"]["relinked_media_count"] == 0
    assert apply_plan(db_path, plan, tmp_path)["applied_count"] == 3

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT state,result_path,working_path FROM items WHERE content_id=?", (item,)).fetchone() == ("IA", None, None)
    conn.close()
    assert build_plan(db_path, manifest, storage_diagnostic)["action_count"] == 0


def test_apply_refuses_database_outside_copy_root(tmp_path: Path):
    outside = tmp_path / "outside.sqlite3"
    db = Database(outside)
    db.close()
    plan = build_plan(outside)
    other_root = tmp_path / "other"
    other_root.mkdir()
    with pytest.raises(ReconciliationError, match="inside the explicit copy root"):
        apply_plan(outside, plan, other_root)


def test_apply_refuses_operational_database_even_when_copy_root_contains_it(tmp_path: Path):
    operational_shaped = tmp_path / "ArmoredCreator-final" / "storage" / "armoredcreator.db"
    db = Database(operational_shaped)
    db.close()
    plan = build_plan(operational_shaped)
    with pytest.raises(ReconciliationError, match="operational ArmoredCreator database"):
        apply_plan(operational_shaped, plan, tmp_path)


def test_publishing_without_definitive_telegram_result_is_blocked_but_absent_is_recoverable(tmp_path: Path):
    db_path = tmp_path / "copy.sqlite3"
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original")
    manifest = _media_manifest(tmp_path / "media.jsonl", "source_1", original)
    db = Database(db_path)
    unknown = db.reserve_item("1", "source", original_url="https://shopee.com.br/unknown", original_path=original)
    absent = db.reserve_item("2", "source", original_url="https://shopee.com.br/absent", original_path=original)
    for item_id in (unknown, absent):
        db.set_vision(item_id, "product", "https://affiliate.invalid/item", ia_context={"shopId": item_id, "itemId": "2"})
        db.set_caption(item_id, "Teste produto 🧡 #teste")
        db.conn.execute("UPDATE items SET state='PUBLISHING' WHERE content_id=?", (item_id,))
    db.conn.execute(
        "INSERT INTO publications(content_id,idempotency_key,confirmed,verification_status) VALUES(?,?,?,?)",
        (absent, "absent-key", 0, "ABSENT"),
    )
    db.conn.commit()
    db.close()

    plan = build_plan(db_path, manifest)
    assert any(row["content_ids"] == [unknown] for row in plan["blocked"])
    action = next(row for row in plan["recovery_actions"] if row["content_id"] == absent)
    assert action["to_state"] == State.SPEECH.value


@pytest.mark.parametrize(
    ("has_result", "expected_state"),
    [(True, State.PUBLISHING.value), (False, State.SPEECH.value)],
)
def test_speech_v5_proof_preserves_audio_and_routes_only_valid_render_to_hub(
    tmp_path: Path, has_result: bool, expected_state: str,
):
    db_path = tmp_path / "copy.sqlite3"
    original = tmp_path / "original.mp4"
    original.write_bytes(b"original")
    result = tmp_path / "final.mp4"
    if has_result:
        result.write_bytes(b"render")
    item_manifest = tmp_path / "media.jsonl"
    item_manifest.write_text(json.dumps({
        "content_id": "source_9", "original_path": str(original),
        "original_exists": True, "file_stable_during_hash": True,
        "sha256_match": "MATCH", "result_path_exists": has_result,
    }) + "\n", encoding="utf-8")
    db = Database(db_path)
    item = db.reserve_item("9", "source", original_url="https://shopee.com.br/product/90/20", original_path=original)
    db.set_vision(item, "product", "https://affiliate.invalid/item", ia_context={"shopId": "90", "itemId": "20"})
    db.set_caption(item, "Teste produto 🧡 #teste")
    db.conn.execute(
        "UPDATE items SET state='STUDIO_COMPLETE',result_path=? WHERE content_id=?",
        (str(result) if has_result else None, item),
    )
    db.conn.execute(
        "INSERT INTO state_events(content_id,old_state,new_state,reason) VALUES(?,?,?,?)",
        (item, "STUDIO", "STUDIO_COMPLETE", "speech-studio-v5-complete"),
    )
    db.conn.commit()
    db.close()

    plan = build_plan(db_path, item_manifest)
    action = next(row for row in plan["recovery_actions"] if row["content_id"] == item)
    assert action["to_state"] == expected_state

