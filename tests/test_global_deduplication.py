from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ArmoredSync.service import TelegramSource
from armored_core.database import Database
from armored_core.models import State


def _approved(db: Database, source_id: str, message_id: str, url: str, context: dict, root: Path) -> str:
    content_id = db.reserve_item(
        message_id, source_id=source_id, original_url=url,
        original_path=root / f"{message_id}.mp4",
    )
    db.set_vision(
        content_id, "Produto de teste", "https://affiliate.invalid/product",
        affiliate_urls=("https://affiliate.invalid/product",), ia_context=context,
    )
    return content_id


def test_sync_deduplicates_same_raw_url_across_sources(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    url = "https://s.shopee.com.br/shared-product"
    db.reserve_item("100", source_id="source1", original_url=url, original_path=tmp_path / "source1.mp4")
    source2 = TelegramSource(tmp_path, SimpleNamespace(), db, source="source2-chat", source_id="source2")

    assert source2._shopee_url_exists(url) is True
    db.close()


def test_product_claim_is_unique_across_message_and_source_ids(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    context = {"shopId": "900", "itemId": "111", "productName": "Lamp"}
    first = db.reserve_item(
        "101", source_id="source1", original_url="https://s.shopee.com.br/first",
        original_path=tmp_path / "first.mp4",
    )
    second = db.reserve_item(
        "202", source_id="source2", original_url="https://shopee.com.br/product/900/111",
        original_path=tmp_path / "second.mp4",
    )

    assert db.claim_product_identity(first, context) is None
    assert db.claim_product_identity(second, context) == first
    db.mark_duplicate(second, first, "canonical-product-already-represented")

    row = db.conn.execute(
        "SELECT state, duplicate_of_content_id, last_error, affiliate_url FROM items WHERE content_id=?",
        (second,),
    ).fetchone()
    assert row["state"] == State.DUPLICATE.value
    assert row["duplicate_of_content_id"] == first
    assert row["last_error"].endswith(first)
    assert row["affiliate_url"] is None
    assert db.get(second).state is State.DUPLICATE
    event = db.conn.execute(
        "SELECT old_state, new_state FROM state_events WHERE content_id=? ORDER BY id DESC LIMIT 1",
        (second,),
    ).fetchone()
    assert event["new_state"] == State.DUPLICATE.value
    assert event["old_state"] == State.RECEIVED.value
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    db.close()


def test_product_claim_recognizes_historic_approved_rows_without_rewriting_them(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    ctx = {"shopId": "123", "itemId": "456"}
    existing = _approved(db, "source1", "1", "https://s.shopee.com.br/historic", ctx, tmp_path)
    candidate = db.reserve_item(
        "2", source_id="source2", original_url="https://shopee.com.br/product/123/456",
        original_path=tmp_path / "candidate.mp4",
    )

    before = tuple(db.conn.execute(
        "SELECT state, affiliate_url, ia_context_json FROM items WHERE content_id=?", (existing,)
    ).fetchone())
    assert db.claim_product_identity(candidate, ctx) == existing
    after = tuple(db.conn.execute(
        "SELECT state, affiliate_url, ia_context_json FROM items WHERE content_id=?", (existing,)
    ).fetchone())
    assert after == before
    owner = db.conn.execute(
        "SELECT owner_content_id FROM product_identity_claims WHERE product_key='123:456'"
    ).fetchone()
    assert owner["owner_content_id"] == existing
    db.close()


def test_product_without_exact_canonical_identity_is_not_claimed(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    candidate = db.reserve_item(
        "5", source_id="source1", original_url="https://s.shopee.com.br/short",
        original_path=tmp_path / "candidate.mp4",
    )
    assert db.claim_product_identity(candidate, {"productName": "Unknown"}) is None
    assert db.conn.execute("SELECT COUNT(*) FROM product_identity_claims").fetchone()[0] == 0
    db.close()


def test_product_claim_is_shared_by_independent_database_connections(tmp_path: Path):
    path = tmp_path / "history.sqlite3"
    db1, db2 = Database(path), Database(path)
    first = db1.reserve_item("1", source_id="source1", original_path=tmp_path / "1.mp4")
    second = db2.reserve_item("2", source_id="source2", original_path=tmp_path / "2.mp4")
    ctx = {"shopId": "77", "itemId": "88"}

    assert db1.claim_product_identity(first, ctx) is None
    assert db2.claim_product_identity(second, ctx) == first
    assert db1.conn.execute(
        "SELECT owner_content_id FROM product_identity_claims WHERE product_key='77:88'"
    ).fetchone()["owner_content_id"] == first
    db1.close()
    db2.close()


def test_pipeline_blocks_duplicate_before_ia_and_studio(tmp_path: Path):
    from armored_core.pipeline import Pipeline
    from armored_core.services import VisionResult
    from armored_core.storage import Storage

    root = tmp_path / "lab"
    storage = Storage(root)
    db = Database(storage.database / "lab.sqlite3")
    context = {"shopId": "333", "itemId": "444"}
    owner = db.reserve_item(
        "owner", source_id="source1", original_url="https://s.shopee.com.br/owner",
        original_path=root / "owner.mp4",
    )
    db.set_vision(
        owner, "Owner product", "https://affiliate.invalid/owner",
        affiliate_urls=("https://affiliate.invalid/owner",), ia_context=context,
    )

    candidate_path = root / "candidate.mp4"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_bytes(b"immutable-test-original")
    candidate = db.reserve_item(
        "candidate", source_id="source2", original_url="https://shopee.com.br/product/333/444",
        original_path=candidate_path,
    )
    db.transition(candidate, State.VISION, "fixture-ready-for-vision")

    class Vision:
        def identify(self, _item):
            return VisionResult(
                "Same product", "https://affiliate.invalid/new",
                affiliate_urls=("https://affiliate.invalid/new",), ia_context=context,
            )

    class Studio:
        calls = 0
        def process(self, _item):
            self.calls += 1
            raise AssertionError("duplicate must not reach Studio")

    class IA:
        calls = 0
        def generate_caption(self, _context):
            self.calls += 1
            raise AssertionError("duplicate must not reach IA")

    studio, ia = Studio(), IA()
    Pipeline(db, storage, Vision(), studio, publisher=None, ia=ia).run(candidate)

    assert db.get(candidate).state is State.DUPLICATE
    assert db.get(candidate).duplicate_of_content_id == owner
    assert db.get(candidate).affiliate_url is None
    assert studio.calls == 0
    assert ia.calls == 0
    assert candidate_path.read_bytes() == b"immutable-test-original"
    db.close()
