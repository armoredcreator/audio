import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from armored_core.database import Database
from armored_core.storage import Storage
from ArmoredVision.modules.v1.shopee_api import ShopeeAPIError, ShopeeProductNotFoundError
from ArmoredVision.service import ArmoredVision
from armored_core.services import VisionUnresolvedError


class _CountingNoOfferAPI:
    def __init__(self):
        self.calls = 0

    def get_exact_product(self, shop_id, item_id):
        self.calls += 1
        raise ShopeeProductNotFoundError(f"no offer:{shop_id}:{item_id}")


class _TransientErrorAPI:
    def get_exact_product(self, shop_id, item_id):
        raise ShopeeAPIError("temporary timeout")


class WaitingVisionNegativeCacheTests(unittest.TestCase):
    def test_exact_no_offer_is_persisted_and_suppresses_duplicate_api_call_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td))
            db = Database(storage.database / "db.sqlite")
            original = storage.original("item-1", ".mp4", original_url="https://shopee.com.br/a-i.123.456")
            original.write_bytes(b"fixture")
            item_id = db.create_item(
                telegram_message_id="1",
                original_path=original,
                original_url="https://shopee.com.br/a-i.123.456",
            )
            api = _CountingNoOfferAPI()
            resolved = type("Resolved", (), {"shop_id": "123", "item_id": "456"})()
            try:
                item = db.get(item_id)
                with patch("ArmoredVision.service.resolve_short_url", return_value=resolved):
                    with self.assertRaisesRegex(VisionUnresolvedError, "NO_AFFILIATE_OFFER"):
                        ArmoredVision(api=api, db=db, no_offer_cache_ttl_seconds=3600).identify(item)
                    # A new Vision instance models process restart while using
                    # the same SQLite ledger and therefore makes no second call.
                    with self.assertRaisesRegex(VisionUnresolvedError, "NO_AFFILIATE_OFFER_CACHED"):
                        ArmoredVision(db=db, no_offer_cache_ttl_seconds=3600).identify(item)
                self.assertEqual(api.calls, 1)
                rows = db.conn.execute(
                    "SELECT product_key, content_id, outcome FROM vision_product_lookup_events"
                ).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["product_key"], "123:456")
                self.assertEqual(rows[0]["content_id"], item.content_id)
                self.assertEqual(rows[0]["outcome"], "OFFER_ABSENT")
            finally:
                db.close()

    def test_cache_expires_and_technical_outcomes_are_not_valid_cache_states(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td))
            db = Database(storage.database / "db.sqlite")
            try:
                checked_at = time.time() - 10
                db.record_vision_product_lookup(
                    "123:456", "item-1", "OFFER_ABSENT", ttl_seconds=2, checked_at=checked_at
                )
                self.assertFalse(db.has_active_vision_no_offer("123:456", now=checked_at + 3))
                db.record_vision_product_lookup(
                    "123:456", "item-2", "OFFER_FOUND", ttl_seconds=0, checked_at=checked_at + 4
                )
                self.assertFalse(db.has_active_vision_no_offer("123:456", now=checked_at + 4))
                with self.assertRaises(ValueError):
                    db.record_vision_product_lookup("123:456", "item-3", "TIMEOUT")
            finally:
                db.close()

    def test_transient_api_error_is_not_persisted_as_no_offer(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td))
            db = Database(storage.database / "db.sqlite")
            original = storage.original("item-1", ".mp4", original_url="https://shopee.com.br/a-i.123.456")
            original.write_bytes(b"fixture")
            item_id = db.create_item(
                telegram_message_id="1",
                original_path=original,
                original_url="https://shopee.com.br/a-i.123.456",
            )
            resolved = type("Resolved", (), {"shop_id": "123", "item_id": "456"})()
            try:
                with patch("ArmoredVision.service.resolve_short_url", return_value=resolved):
                    with self.assertRaisesRegex(ShopeeAPIError, "timeout"):
                        ArmoredVision(api=_TransientErrorAPI(), db=db).identify(db.get(item_id))
                self.assertEqual(
                    db.conn.execute("SELECT COUNT(*) FROM vision_product_lookup_events").fetchone()[0],
                    0,
                )
            finally:
                db.close()

    def test_waiting_retry_allowlist_is_exact_and_empty_allowlist_does_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td))
            db = Database(storage.database / "db.sqlite")
            try:
                ids = []
                for n in range(1, 4):
                    original = storage.original(f"item-{n}", ".mp4", original_url=f"https://shopee.com.br/a-i.123.{n}")
                    original.write_bytes(b"fixture")
                    item_id = db.create_item(
                        telegram_message_id=str(n),
                        original_path=original,
                        original_url=f"https://shopee.com.br/a-i.123.{n}",
                    )
                    db.mark_vision_waiting(item_id, "test-waiting")
                    ids.append(item_id)
                selected = db.pending_waiting_vision_retries(limit=None, content_ids=[ids[2], ids[0], ids[2]])
                self.assertEqual({item.content_id for item in selected}, {ids[0], ids[2]})
                self.assertEqual(db.pending_waiting_vision_retries(limit=None, content_ids=[]), [])
                with self.assertRaises(ValueError):
                    db.pending_waiting_vision_retries(limit=None, content_ids=[f"x{i}" for i in range(1001)])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
