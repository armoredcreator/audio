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


def test_sync_allows_cross_source_url_to_reach_durable_reservation(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    url = "https://s.shopee.com.br/shared-product"
    owner = db.reserve_item("100", source_id="source1", original_url=url, original_path=tmp_path / "source1.mp4")
    source2 = TelegramSource(tmp_path, SimpleNamespace(), db, source="source2-chat", source_id="source2")

    # The pre-check must not silently discard a different source/message identity.
    assert source2._shopee_url_exists(url) is False
    duplicate = db.reserve_item(
        "200", source_id="source2", original_url=url,
        original_path=tmp_path / "source2.mp4",
    )
    row = db.conn.execute(
        "SELECT state, duplicate_of_content_id FROM items WHERE content_id=?", (duplicate,)
    ).fetchone()
    assert duplicate != owner
    assert row["state"] == State.DUPLICATE.value
    assert row["duplicate_of_content_id"] == owner
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
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


def test_exact_product_url_is_deduplicated_before_shopee_api_lookup(tmp_path: Path):
    from ArmoredVision.service import ArmoredVision
    from armored_core.pipeline import Pipeline
    from armored_core.storage import Storage

    class ApiMustNotBeCalled:
        calls = 0

        def get_exact_product(self, shop_id, item_id):
            self.calls += 1
            raise AssertionError("known exact product must be blocked before Shopee API")

    db = Database(tmp_path / "history.sqlite3")
    context = {"shopId": "900", "itemId": "111"}
    owner = _approved(
        db, "source1", "101", "https://shopee.com.br/product/900/111", context, tmp_path
    )
    candidate = db.reserve_item(
        "202", source_id="source2",
        original_url="https://shopee.com.br/product/900/111?variant=2",
        original_path=tmp_path / "candidate.mp4",
    )
    (tmp_path / "candidate.mp4").write_bytes(b"original")
    api = ApiMustNotBeCalled()
    class NoDownstream:
        def process(self, item):
            raise AssertionError("duplicate must not reach Studio")

        def publish(self, item):
            raise AssertionError("duplicate must not reach Hub")

    Pipeline(
        db,
        Storage(tmp_path),
        ArmoredVision(api=api, db=db),
        NoDownstream(),
        NoDownstream(),
    ).run(candidate)

    assert api.calls == 0
    assert db.get(candidate).state is State.DUPLICATE
    assert db.get(candidate).duplicate_of_content_id == owner
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


def test_global_raw_url_reservation_preserves_duplicate_message_without_vision_approval(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    url = "https://s.shopee.com.br/shared-raw-url"
    owner = db.reserve_item(
        "100", source_id="source1", original_url=url,
        original_path=tmp_path / "owner.mp4",
    )
    duplicate = db.reserve_item(
        "200", source_id="source2", original_url="  " + url.upper() + " ",
        original_path=tmp_path / "duplicate.mp4",
    )

    assert duplicate != owner
    row = db.conn.execute(
        "SELECT state, duplicate_of_content_id, affiliate_url FROM items WHERE content_id=?",
        (duplicate,),
    ).fetchone()
    assert row["state"] == State.DUPLICATE.value
    assert row["duplicate_of_content_id"] == owner
    assert row["affiliate_url"] is None
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    assert db.get(duplicate).state is State.DUPLICATE
    db.close()


def test_canonical_claim_seeds_historical_identity_ledger_only_once(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    first_context = {"shopId": "1", "itemId": "10"}
    second_context = {"shopId": "2", "itemId": "20"}
    owner1 = _approved(db, "source1", "1", "https://shopee.co/first", first_context, tmp_path)
    owner2 = _approved(db, "source1", "2", "https://shopee.co/second", second_context, tmp_path)
    candidate = db.reserve_item(
        "3", source_id="source2", original_url="https://shopee.co/candidate",
        original_path=tmp_path / "candidate.mp4",
    )

    assert db.claim_product_identity(candidate, first_context) == owner1
    claim_count = db.conn.execute("SELECT COUNT(*) FROM product_identity_claims").fetchone()[0]
    assert claim_count == 2
    assert db.claim_product_identity(candidate, {"shopId": "3", "itemId": "30"}) is None
    assert db.conn.execute("SELECT COUNT(*) FROM product_identity_claims").fetchone()[0] == 3
    assert db.conn.execute(
        "SELECT value FROM product_identity_claim_meta WHERE key='historical_seeded'"
    ).fetchone()["value"] == "1"
    assert db.conn.execute("SELECT state FROM items WHERE content_id=?", (owner1,)).fetchone()["state"] == State.RECEIVED.value
    assert db.conn.execute("SELECT state FROM items WHERE content_id=?", (owner2,)).fetchone()["state"] == State.RECEIVED.value
    db.close()


def test_late_link_enrichment_reuses_waiting_reservation_and_does_not_resurrect_duplicate(tmp_path: Path):
    from armored_core.services import IngestMessage, SyncService
    from armored_core.storage import Storage

    db = Database(tmp_path / "history.sqlite3")
    storage = Storage(tmp_path)
    owner = db.reserve_item(
        "101", source_id="source1",
        original_url="https://s.shopee.com.br/already-known",
        original_path=tmp_path / "owner.mp4",
    )
    waiting = db.reserve_item(
        "202", source_id="source2",
        original_url=None,
        original_path=tmp_path / "waiting.mp4",
    )
    db.transition(waiting, State.WAITING_VISION, "initial-link-missing")
    content_before = db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    video = tmp_path / "incoming.mp4"
    video.write_bytes(b"fixture")
    sync = SyncService(db, storage)

    returned = sync.reserve_message(IngestMessage(
        telegram_message_id="202",
        source_id="source2",
        original_url="  HTTPS://S.SHOPEE.COM.BR/ALREADY-KNOWN  ",
        source_path=video,
    ))

    current = db.get(waiting)
    assert returned == waiting
    assert current.content_id == waiting
    assert current.state is State.DUPLICATE
    assert current.duplicate_of_content_id == owner
    assert current.original_url == "HTTPS://S.SHOPEE.COM.BR/ALREADY-KNOWN"
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == content_before
    assert db.conn.execute(
        "SELECT COUNT(*) FROM state_events WHERE content_id=? AND new_state=?",
        (waiting, State.DUPLICATE.value),
    ).fetchone()[0] == 1
    db.close()



def test_historical_identity_seed_skips_duplicate_rows(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    context = {"shopId": "555", "itemId": "666"}
    stale_duplicate = _approved(
        db, "source1", "1", "https://shopee.co/stale", context, tmp_path
    )
    canonical = _approved(
        db, "source1", "2", "https://shopee.co/canonical", context, tmp_path
    )
    db.mark_duplicate(stale_duplicate, canonical, "fixture-duplicate")

    candidate = db.reserve_item(
        "3", source_id="source2", original_url="https://shopee.co/new",
        original_path=tmp_path / "candidate.mp4",
    )

    assert db.claim_product_identity(candidate, context) == canonical
    stored_owner = db.conn.execute(
        "SELECT owner_content_id FROM product_identity_claims WHERE product_key='555:666'"
    ).fetchone()["owner_content_id"]
    assert stored_owner == canonical
    # Historical source rows remain intact apart from the explicit test duplicate.
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
    db.close()


def test_vision_claim_and_approval_are_committed_together(tmp_path: Path):
    import json

    db = Database(tmp_path / "history.sqlite3")
    context = {"shopId": "777", "itemId": "888", "productName": "Fixture product"}
    owner = db.reserve_item(
        "10", source_id="source1", original_url="https://shopee.co/owner",
        original_path=tmp_path / "owner.mp4",
    )
    candidate = db.reserve_item(
        "20", source_id="source2", original_url="https://shopee.co/candidate",
        original_path=tmp_path / "candidate.mp4",
    )
    db.transition(owner, State.VISION, "test-vision")
    db.transition(candidate, State.VISION, "test-vision")

    assert db.commit_vision_result(
        owner, "Fixture product", "https://affiliate.invalid/owner",
        affiliate_urls=("https://affiliate.invalid/owner",), ia_context=context,
    ) is None

    owner_row = db.conn.execute(
        "SELECT affiliate_url, ia_context_json FROM items WHERE content_id=?", (owner,)
    ).fetchone()
    assert owner_row["affiliate_url"] == "https://affiliate.invalid/owner"
    assert json.loads(owner_row["ia_context_json"])["shopId"] == "777"

    duplicate_owner = db.commit_vision_result(
        candidate, "Fixture product", "https://affiliate.invalid/candidate",
        affiliate_urls=("https://affiliate.invalid/candidate",), ia_context=context,
    )
    assert duplicate_owner == owner

    duplicate_row = db.conn.execute(
        "SELECT state, duplicate_of_content_id, affiliate_url, affiliate_urls_json, "
        "ia_context_json FROM items WHERE content_id=?",
        (candidate,),
    ).fetchone()
    assert duplicate_row["state"] == State.DUPLICATE.value
    assert duplicate_row["duplicate_of_content_id"] == owner
    assert duplicate_row["affiliate_url"] is None
    assert duplicate_row["affiliate_urls_json"] == "[]"
    assert json.loads(duplicate_row["ia_context_json"])["itemId"] == "888"
    assert db.conn.execute(
        "SELECT COUNT(*) FROM state_events WHERE content_id=? AND new_state=?",
        (candidate, State.DUPLICATE.value),
    ).fetchone()[0] == 1
    db.close()


def test_vision_result_repairs_stale_unapproved_identity_claim(tmp_path: Path):
    db = Database(tmp_path / "history.sqlite3")
    context = {"shopId": "9000", "itemId": "10000"}
    stale = db.reserve_item(
        "1", source_id="source1", original_url="https://shopee.co/stale-claim",
        original_path=tmp_path / "stale.mp4",
    )
    candidate = db.reserve_item(
        "2", source_id="source2", original_url="https://shopee.co/candidate",
        original_path=tmp_path / "candidate.mp4",
    )

    # Reproduce a claim written by the older two-transaction implementation,
    # where the Vision result was never persisted afterward.
    assert db.claim_product_identity(stale, context) is None
    db.transition(candidate, State.VISION, "test-vision")

    assert db.commit_vision_result(
        candidate, "Resolved product", "https://affiliate.invalid/resolved",
        affiliate_urls=("https://affiliate.invalid/resolved",), ia_context=context,
    ) is None
    assert db.get(candidate).state is State.VISION
    assert db.get(candidate).affiliate_url == "https://affiliate.invalid/resolved"

    claim = db.conn.execute(
        "SELECT owner_content_id FROM product_identity_claims WHERE product_key='9000:10000'"
    ).fetchone()
    assert claim["owner_content_id"] == candidate
    assert db.get(stale).state is State.RECEIVED
    assert db.get(stale).affiliate_url is None
    db.close()


def test_failed_vision_commit_does_not_leave_product_claim(tmp_path: Path):
    import pytest

    db = Database(tmp_path / "history.sqlite3")
    with pytest.raises(KeyError, match="unknown-content-id"):
        db.commit_vision_result(
            "missing-content", "Product", "https://affiliate.invalid/product",
            ia_context={"shopId": "99", "itemId": "123"},
        )
    assert db.conn.execute("SELECT COUNT(*) FROM product_identity_claims").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM product_identity_claim_meta").fetchone()[0] == 0
    db.close()
