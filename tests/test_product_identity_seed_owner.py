from __future__ import annotations

import hashlib
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


def test_seed_prefers_original_with_matching_sha_over_forged_hash(tmp_path):
    db = Database(tmp_path / "hash-verification.sqlite3")
    try:
        forged = tmp_path / "earlier-original.mp4"
        verified = tmp_path / "later-original.mp4"
        forged.write_bytes(b"content does not match recorded hash")
        verified.write_bytes(b"verified original content")

        _insert_approved(
            db, content_id="source-seed-test_forged", message_id="201",
            state=State.RECEIVED, path=forged, sha="f" * 64,
            shop_id="555", item_id="666", created_at="2026-01-01T00:00:00Z",
        )
        _insert_approved(
            db, content_id="source-seed-test_verified", message_id="202",
            state=State.RECEIVED, path=verified,
            sha=hashlib.sha256(verified.read_bytes()).hexdigest(),
            shop_id="555", item_id="666", created_at="2026-02-01T00:00:00Z",
        )

        rows = _seed(db)
        assert [(row["product_key"], row["owner_content_id"]) for row in rows] == [
            ("555:666", "source-seed-test_verified")
        ]
        assert db.get("source-seed-test_forged").state is State.RECEIVED
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


def test_seed_does_not_treat_sha256_shape_as_integrity_evidence(tmp_path):
    db = Database(tmp_path / "unverified-sha-history.sqlite3")
    try:
        earlier = tmp_path / "earlier-original.mp4"
        later = tmp_path / "later-original.mp4"
        earlier.write_bytes(b"earlier content")
        later.write_bytes(b"later content with a false declared digest")
        _insert_approved(
            db, content_id="source-seed-test_earlier", message_id="201",
            state=State.RECEIVED, path=earlier, sha=None, shop_id="555",
            item_id="666", created_at="2026-01-01T00:00:00Z",
        )
        # 64 hexadecimal characters are syntactically plausible but do not
        # match this file. They must not make a later record the canonical owner.
        _insert_approved(
            db, content_id="source-seed-test_later", message_id="202",
            state=State.RECEIVED, path=later, sha="a" * 64, shop_id="555",
            item_id="666", created_at="2026-02-01T00:00:00Z",
        )

        rows = _seed(db)
        assert [(row["product_key"], row["owner_content_id"]) for row in rows] == [
            ("555:666", "source-seed-test_earlier")
        ]
        assert db.get("source-seed-test_earlier").state is State.RECEIVED
        assert db.get("source-seed-test_later").state is State.RECEIVED
    finally:
        db.close()


def test_seed_does_not_treat_text_false_publication_flag_as_confirmed(tmp_path):
    db = Database(tmp_path / "false-publication-history.sqlite3")
    try:
        earlier = tmp_path / "earlier-original.mp4"
        later = tmp_path / "later-original.mp4"
        earlier.write_bytes(b"earlier")
        later.write_bytes(b"later")
        _insert_approved(
            db, content_id="source-seed-test_earlier-pub", message_id="301",
            state=State.RECEIVED, path=earlier, sha=None, shop_id="901",
            item_id="902", created_at="2026-01-01T00:00:00Z",
        )
        _insert_approved(
            db, content_id="source-seed-test_later-pub", message_id="302",
            state=State.RECEIVED, path=later, sha=None, shop_id="901",
            item_id="902", created_at="2026-02-01T00:00:00Z",
        )
        db.conn.execute(
            "INSERT INTO publications (content_id, idempotency_key, confirmed, verification_status) "
            "VALUES (?, ?, ?, 'PENDING')",
            ("source-seed-test_later-pub", "seed-test-false-text", "false"),
        )
        db.conn.commit()

        rows = _seed(db)
        assert [(row["product_key"], row["owner_content_id"]) for row in rows] == [
            ("901:902", "source-seed-test_earlier-pub")
        ]
    finally:
        db.close()


def test_seed_resolves_relative_original_paths_from_database_root_not_cwd(tmp_path, monkeypatch):
    lab_root = tmp_path / "lab"
    db_path = lab_root / "storage" / "database" / "history.sqlite3"
    db = Database(db_path)
    try:
        missing = lab_root / "missing-original.mp4"
        relative_original = lab_root / "Videos" / "original.mp4"
        relative_original.parent.mkdir(parents=True)
        relative_original.write_bytes(b"relative original")

        _insert_approved(
            db, content_id="source-seed-test_missing-relative", message_id="401",
            state=State.RECEIVED, path=missing, sha=None, shop_id="903",
            item_id="904", created_at="2026-01-01T00:00:00Z",
        )
        _insert_approved(
            db, content_id="source-seed-test_present-relative", message_id="402",
            state=State.RECEIVED, path=Path("Videos/original.mp4"), sha=None, shop_id="903",
            item_id="904", created_at="2026-02-01T00:00:00Z",
        )
        unrelated_cwd = tmp_path / "unrelated-cwd"
        unrelated_cwd.mkdir()
        monkeypatch.chdir(unrelated_cwd)

        rows = _seed(db)
        assert [(row["product_key"], row["owner_content_id"]) for row in rows] == [
            ("903:904", "source-seed-test_present-relative")
        ]
    finally:
        db.close()
