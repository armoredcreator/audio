from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ArmoredStock.service import ArmoredStock
from armored_core.models import State
from armored_core.storage import Storage


class FakeDatabase:
    def __init__(self, *, pending_vision=None):
        self._pending_vision = pending_vision or []

    def pending_vision_candidates(self):
        return self._pending_vision

    def vision_approved_interrupted_items(self):
        return []

    def pre_download_recovery_items(self):
        return []

    def pending_vision_approved_items(self):
        return []


def coordinator_with_sources(count=3, *, pending_vision=None):
    routes = tuple(
        type("Route", (), {"source_id": f"source-{i}"})()
        for i in range(count)
    )
    source = type("Source", (), {"sources": routes})()
    return type(
        "Coordinator",
        (),
        {"source": source, "db": FakeDatabase(pending_vision=pending_vision)},
    )()


class ArmoredStockTests(unittest.TestCase):
    def test_requires_exactly_three_configured_sources(self):
        coordinator = coordinator_with_sources(2)
        with self.assertRaisesRegex(RuntimeError, "exatamente três fontes"):
            asyncio.run(ArmoredStock(coordinator).run())

    def test_blocks_downloads_while_vision_decisions_are_pending(self):
        coordinator = coordinator_with_sources(
            3, pending_vision=[object()]
        )
        with self.assertRaisesRegex(RuntimeError, "Vision ainda tem candidatos"):
            asyncio.run(ArmoredStock(coordinator).run())

    def test_returns_empty_report_when_no_approved_originals_are_pending(self):
        coordinator = coordinator_with_sources(3)
        report = asyncio.run(ArmoredStock(coordinator).run())
        self.assertEqual(report["tool"], "ArmoredStock")
        self.assertEqual(report["processed"], 0)
        self.assertEqual(report["outcomes"]["downloaded"], 0)
        self.assertEqual(report["outcomes"]["approved_missing_original"], 0)
        self.assertEqual(report["errors"], [])

    def test_continues_to_next_approved_item_after_one_download_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            failed = SimpleNamespace(
                content_id="source-1_100",
                telegram_message_id="100",
                source_id="source-1",
                state=State.RECEIVED,
                affiliate_url="https://affiliate.invalid/100",
                original_path=root / "100.mp4",
            )
            succeeding_path = root / "101.mp4"
            succeeding = SimpleNamespace(
                content_id="source-1_101",
                telegram_message_id="101",
                source_id="source-1",
                state=State.RECEIVED,
                affiliate_url="https://affiliate.invalid/101",
                original_path=succeeding_path,
            )

            class MutableDatabase(FakeDatabase):
                def pending_vision_approved_items(self):
                    return [failed, succeeding]

                def get(self, content_id):
                    return {
                        failed.content_id: failed,
                        succeeding.content_id: succeeding,
                    }[content_id]

            coordinator = coordinator_with_sources(3)
            coordinator.db = MutableDatabase()
            stock = ArmoredStock(coordinator)
            attempted = []

            async def fake_materialize(item, **_kwargs):
                attempted.append(item.content_id)
                if item.content_id == failed.content_id:
                    raise TimeoutError("Telegram media unavailable")
                succeeding_path.write_bytes(b"video")
                return "downloaded"

            stock.materialize_item = fake_materialize
            report = asyncio.run(stock.run())

            self.assertEqual(attempted, [failed.content_id, succeeding.content_id])
            self.assertEqual(report["processed"], 1)
            self.assertEqual(report["errors"], [
                "stock:source-1_100:Telegram media unavailable"
            ])
            self.assertTrue(succeeding_path.is_file())
            self.assertEqual(report["outcomes"]["approved_missing_original"], 1)

    def test_recovers_if_original_move_succeeds_but_sqlite_path_repair_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            source_id = "source1"
            data = b"immutable original after interrupted path repair"
            old_path = storage.workspace("88", source_id=source_id) / "88_finallinkoriginal.mp4"
            old_path.write_bytes(data)
            original_url = "https://s.shopee.com.br/short-code-88"
            canonical = storage.original(
                "88", original_url=original_url, source_id=source_id,
            )
            item = SimpleNamespace(
                content_id="88",
                telegram_message_id="88",
                source_id=source_id,
                state=State.RECEIVED,
                affiliate_url="https://affiliate.invalid/88",
                affiliate_name="Produto",
                original_url=original_url,
                original_path=old_path,
                original_sha256=hashlib.sha256(data).hexdigest(),
                topic_id=None,
                topic_name=None,
            )

            class MutableDatabase:
                failures_remaining = 1

                def get(self, _content_id):
                    return item

                def repair_original_path(self, _content_id, path):
                    if self.failures_remaining:
                        self.failures_remaining -= 1
                        raise RuntimeError("simulated-sqlite-path-repair-failure")
                    item.original_path = path

            coordinator = SimpleNamespace(
                db=MutableDatabase(),
                storage=storage,
                source=None,
            )
            stock = ArmoredStock(coordinator)

            with self.assertRaisesRegex(RuntimeError, "simulated-sqlite-path-repair-failure"):
                asyncio.run(stock.materialize_item(item))

            # The first attempt moved, but did not overwrite or corrupt, the original.
            self.assertFalse(old_path.exists())
            self.assertTrue(canonical.is_file())
            self.assertEqual(canonical.read_bytes(), data)
            self.assertEqual(item.original_path, old_path)

            # A retry must reconcile the durable path and reuse the same bytes.
            outcome = asyncio.run(stock.materialize_item(item))
            self.assertEqual(outcome, "existing")
            self.assertEqual(item.original_path, canonical)
            self.assertEqual(canonical.read_bytes(), data)

    def test_migrates_existing_original_to_frozen_url_tail_name_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            source_id = "source1"
            data = b"immutable original"
            old_path = (
                storage.workspace("77", source_id=source_id)
                / "77_finallinkoriginal.mp4"
            )
            old_path.write_bytes(data)
            original_url = "https://s.shopee.com.br/short-code-77"
            canonical = storage.original(
                "77",
                original_url=original_url,
                source_id=source_id,
            )
            item = SimpleNamespace(
                content_id="77",
                telegram_message_id="77",
                source_id=source_id,
                state=State.RECEIVED,
                affiliate_url="https://s.shopee.com.br/affiliate-77",
                affiliate_name="Produto",
                original_url=original_url,
                original_path=old_path,
                original_sha256=hashlib.sha256(data).hexdigest(),
                topic_id=None,
                topic_name=None,
            )

            class MutableDatabase:
                def get(self, _content_id):
                    return item

                def repair_original_path(self, _content_id, path):
                    item.original_path = path

            coordinator = SimpleNamespace(
                db=MutableDatabase(),
                storage=storage,
                source=None,
            )

            outcome = asyncio.run(ArmoredStock(coordinator).materialize_item(item))

            self.assertEqual(outcome, "existing")
            self.assertEqual(canonical.name, "77_short-code-77.mp4")
            self.assertFalse(old_path.exists())
            self.assertEqual(item.original_path, canonical)
            self.assertEqual(canonical.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
