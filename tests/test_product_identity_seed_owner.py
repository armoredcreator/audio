from __future__ import annotations

import json
from pathlib import Path

from armored_core.database import Database
from armored_core.models import State


def _insert_approved(db, *, content_id, message_id, state, path, sha, shop_id, item_id, created_at):
    db.conn.execute(
        "INSERT INTO items (content_id, telegram_message_id, source_id, state, original_path, "
        "original_sha256, affiliate_url, ia_context_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            content_id, str(message_id), "source-seed-test", state.value, str(path), sha,
            "https://affiliate.invalid/item", json.dumps({"shopId": shop_id, "itemId": item_id}),
            created_at,
        ),
    )


def _seed(db):
    # Commit fixture inserts before explicitly acquiring the writer transaction.
    db.conn.commit()
    db.conn.execute("BEGIN IMMEDIATE")
    db._seed_historical_product_claims_locked()
    db.conn.commit()
    return db.conn.execute(
        "SELECT product_key, owner_content_id FROM product_identity_claims ORDER BY product_key"
    ).fetchall()


def test_seed_prefers_verified_original_over_earlier_missing_received_row(tmp_path):
    db = Database(tmp_path / "history.sqlite3")
    try:
        missing = tmp_path / "missing-original.mp4"
        valid = tmp_path / "valid-original.mp4"
        valid.write_bytes(b"immutable-original")
        _insert_approved(
            db, content_id="source-seed-test_104718", message_id="104718",
            state=State.RECEIVED, path=missing, sha=None, shop_id="784000629",
            item_id="22993003920", created_at="2026-01-01T00:00:00Z",
        )
        _insert_approved(
            db, content_id="source-seed-test_3342", message_id="3342",
            state=State.RECEIVED, path=valid, sha="a" * 64, shop_id="784000629",
            item_id="22993003920", created_at="2026-02-01T00:00:00Z",
        )

        rows = _seed(db)
        assert [(row["product_key"], row["owner_content_id"]) for row in rows] == [
            ("784000629:22993003920", "source-seed-test_3342")
        ]
        # Seeding claims does not alter historical item states or Vision evidence.
        assert db.get("source-seed-test_104718").state is State.RECEIVED
        assert db.get("source-seed-test_3342").state is State.RECEIVED
        assert db.get("source-seed-test_104718").affiliate_url == "https://affiliate.invalid/item"
    finally:
        db.close()


def test_seed_preserves_confirmed_published_owner_even_if_original_is_missing(tmp_path):
    db = Database(tmp_path / "published-history.sqlite3")
    try:
        available = tmp_path / "available-received.mp4"
        available.write_bytes(b"available-original")
        _insert_approved(
            db, content_id="source-seed-test_received", message_id="101",
            state=State.RECEIVED, path=available, sha="b" * 64, shop_id="777",
            item_id="888", created_at="2026-01-01T00:00:00Z",
        )
        _insert_approved(
            db, content_id="source-seed-test_published", message_id="102",
            state=State.PUBLISHED, path=tmp_path / "gone.mp4", sha=None,
            shop_id="777", item_id="888", created_at="2026-02-01T00:00:00Z",
        )
        db.conn.execute(
            "INSERT INTO publications (content_id, idempotency_key, confirmed, verification_status) "
            "VALUES (?, ?, 1, 'CONFIRMED')",
            ("source-seed-test_published", "seed-test-published-key"),
        )
        db.conn.commit()

        rows = _seed(db)
        assert [(row["product_key"], row["owner_content_id"]) for row in rows] == [
            ("777:888", "source-seed-test_published")
        ]
        assert db.get("source-seed-test_received").state is State.RECEIVED
        assert db.get("source-seed-test_published").state is State.PUBLISHED
    finally:
        db.close()
