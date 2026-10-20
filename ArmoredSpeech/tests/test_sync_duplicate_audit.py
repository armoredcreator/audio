from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.audit_sync_duplicates import (
    AuditError,
    audit_snapshot,
    create_consistent_snapshot,
    main,
    product_identity_from_direct_url,
    write_report,
)


def _database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE items (
                content_id TEXT PRIMARY KEY,
                telegram_message_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                original_url TEXT,
                state TEXT NOT NULL,
                original_path TEXT NOT NULL DEFAULT '',
                affiliate_url TEXT,
                ia_context_json TEXT NOT NULL DEFAULT '{}',
                last_error TEXT
            )
            """
        )
        rows = [
            (
                "item-a", "101", "source1",
                "https://s.shopee.com.br/shared-short",
                "IA", "", "https://affiliate.invalid/a",
                json.dumps({"shopId": "900", "itemId": "111"}), "",
            ),
            (
                "item-b", "202", "source2",
                "https://s.shopee.com.br/shared-short",
                "IA", "", "https://affiliate.invalid/b",
                json.dumps({"shopId": "900", "itemId": "111"}), "",
            ),
            (
                "item-c", "303", "source3",
                "https://shopee.com.br/product/900/111",
                "RECEIVED", "", None,
                json.dumps({"shopId": "900", "itemId": "111"}), "",
            ),
            (
                "item-unlinked", "404", "source3",
                None, "WAITING_VISION", "", None, "{}", "link ausente",
            ),
            (
                "item-other", "505", "source1",
                "https://shopee.com.br/product/900/222",
                "IA", "", "https://affiliate.invalid/other",
                json.dumps({"shopId": "900", "itemId": "222"}), "",
            ),
        ]
        connection.executemany(
            """
            INSERT INTO items (
                content_id, telegram_message_id, source_id, original_url,
                state, original_path, affiliate_url, ia_context_json, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_product_identity_extraction_is_offline_and_host_scoped():
    assert product_identity_from_direct_url(
        "https://shopee.com.br/abajur-i.900.111?utm_source=test"
    ) == "900:111"
    assert product_identity_from_direct_url("https://shopee.co/900/111") == "900:111"
    assert product_identity_from_direct_url("https://s.shopee.com.br/shared-short") is None
    assert product_identity_from_direct_url("https://shopee.com.br.evil.test/900/111") is None
    assert product_identity_from_direct_url("https://example.com/product/900/111") is None


def test_snapshot_audit_detects_global_url_and_product_duplicates_without_writes(tmp_path: Path):
    source = _database(tmp_path / "source.sqlite3")
    before = _sha256(source)
    snapshot = tmp_path / "reports" / "snapshot.sqlite3"

    create_consistent_snapshot(source, snapshot)
    report = audit_snapshot(snapshot)

    assert _sha256(source) == before, "source SQLite bytes must remain unchanged"
    assert report["audit_mode"] == "READ_ONLY_SNAPSHOT"
    assert report["counts"]["items_total"] == 5
    assert report["counts"]["url_duplicate_groups"] == 1
    assert report["counts"]["url_duplicate_items"] == 2
    assert report["counts"]["url_duplicate_groups_with_2plus_vision_approved"] == 1
    assert report["counts"]["canonical_product_duplicate_groups"] == 1
    assert report["counts"]["canonical_product_duplicate_items"] == 3
    assert report["counts"]["canonical_product_duplicate_groups_with_2plus_vision_approved"] == 1
    assert report["counts"]["items_without_original_url"] == 1

    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM items WHERE state='WAITING_VISION'").fetchone()[0] == 1


def test_context_product_identity_groups_distinct_urls_and_sources(tmp_path: Path):
    database = _database(tmp_path / "source.sqlite3")
    report = audit_snapshot(database)

    groups = report["_duplicate_group_rows"]
    canonical = [row for row in groups if row["match_type"] == "canonical_product"]
    assert len(canonical) == 1
    assert canonical[0]["match_key"] == "900:111"
    assert set(canonical[0]["content_ids"].split("|")) == {"item-a", "item-b", "item-c"}
    assert canonical[0]["source_count"] == 3


def test_legacy_schema_without_context_uses_direct_product_url_and_warns(tmp_path: Path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE items (content_id TEXT, telegram_message_id TEXT, original_url TEXT, state TEXT, affiliate_url TEXT)"
        )
        connection.executemany(
            "INSERT INTO items VALUES (?, ?, ?, ?, ?)",
            [
                ("old-a", "1", "https://shopee.com.br/product/900/111", "IA", "https://affiliate.invalid/a"),
                ("old-b", "2", "https://shopee.com.br/item/900/111", "IA", "https://affiliate.invalid/b"),
            ],
        )

    report = audit_snapshot(database)

    assert report["counts"]["canonical_product_duplicate_groups"] == 1
    assert report["counts"]["canonical_product_duplicate_groups_with_2plus_vision_approved"] == 1
    assert any("No Vision context JSON" in warning for warning in report["warnings"])
    assert report["counts"]["items_total"] == 2


def test_write_report_emits_new_json_and_csv_artifacts(tmp_path: Path):
    database = _database(tmp_path / "source.sqlite3")
    report = audit_snapshot(database)
    report_dir = tmp_path / "validation" / "audit"
    snapshot = tmp_path / "snapshot.sqlite3"
    create_consistent_snapshot(database, snapshot)

    write_report(report, report_dir, snapshot)

    assert (report_dir / "duplicate-audit.json").is_file()
    assert (report_dir / "duplicate-groups.csv").is_file()
    assert (report_dir / "duplicate-items.csv").is_file()
    assert (report_dir / "unlinked-items.csv").is_file()
    public = json.loads((report_dir / "duplicate-audit.json").read_text(encoding="utf-8"))
    assert public["snapshot_sha256"] == _sha256(snapshot)
    assert "_duplicate_group_rows" not in public

    with pytest.raises(AuditError, match="refusing to overwrite"):
        write_report(report, report_dir, snapshot)


def test_cli_rejects_report_directory_outside_validation_root(tmp_path: Path):
    root = tmp_path / "lab"
    root.mkdir()
    (root / "storage" / "validation").mkdir(parents=True)
    source = _database(tmp_path / "source.sqlite3")
    outside = tmp_path / "outside"

    result = main([
        "--root", str(root),
        "--database", str(source),
        "--report-dir", str(outside),
    ])

    assert result == 2
    assert not outside.exists()


def test_snapshot_refuses_source_as_destination_and_existing_destination(tmp_path: Path):
    source = _database(tmp_path / "source.sqlite3")
    with pytest.raises(AuditError, match="must differ"):
        create_consistent_snapshot(source, source)

    destination = tmp_path / "existing.sqlite3"
    destination.write_bytes(b"preserve")
    with pytest.raises(AuditError, match="refusing to overwrite"):
        create_consistent_snapshot(source, destination)
    assert destination.read_bytes() == b"preserve"
