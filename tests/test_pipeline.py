import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.pipeline import Pipeline
from armored_core.services import PublicationResult, PublicationUnknownError, StudioResult, SyncService, VisionResult
from armored_core.storage import Storage

class Vision:
    def identify(self, item):
        return VisionResult("produto-final", "https://example.invalid/a")

class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        w = self.storage.working(item.item_id)
        w.write_bytes(item.original_path.read_bytes())
        r = self.storage.result(item.item_id, item.affiliate_name)
        r.write_bytes(w.read_bytes())
        return StudioResult(w, r)

class Publisher:
    def __init__(self):
        self.count = 0
        self.ids = set()

    def check_publication(self, item):
        return PublicationCheck.CONFIRMED if item.item_id in self.ids else PublicationCheck.ABSENT

    def publish(self, item):
        self.count += 1
        self.ids.add(item.item_id)
        return PublicationResult(True, str(self.count))


class CaptionIA:
    def generate_caption_with_evidence(self, context):
        from ArmoredIA.caption.generator import CaptionGenerator

        class Provider:
            def generate_candidates(self, _task, _context, _limit):
                return ["Cantinho charmoso 🧡 #casa"]

        return CaptionGenerator(provider=Provider()).generate_with_evidence(context)

    def generate_caption(self, context):
        return self.generate_caption_with_evidence(context).caption

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.storage = Storage(root)
        self.db = Database(self.storage.database / "armoredcreator.db")
        src = root / "source.mp4"
        src.write_bytes(b"VIDEO")
        self.item = SyncService(self.db, self.storage).ingest(src, "msg-1")
        self.pub = Publisher()
        self.ia = CaptionIA()

    def tearDown(self):
        self.db.close()
        self.td.cleanup()

    def test_happy_path_keeps_original_and_published_result(self):
        Pipeline(self.db, self.storage, Vision(), Studio(self.storage), self.pub, self.ia).run(self.item)
        row = self.db.get(self.item)
        self.assertEqual(row.state, State.PUBLISHED)
        self.assertEqual(row.original_path.read_bytes(), b"VIDEO")
        self.assertTrue(row.result_path.is_file())
        self.assertEqual(
            {p.name for p in row.workspace.iterdir()},
            {row.original_path.name, row.result_path.name},
        )
        self.assertEqual(self.pub.count, 1)
        self.assertTrue(row.working_path is not None)
        self.assertTrue(row.result_path is not None)

    def test_publish_once_receives_fresh_publication_without_preflight(self):
        class IdempotentPublisher:
            def __init__(self, db):
                self.db = db
                self.calls = 0

            def publish_once(self, item):
                self.calls += 1
                if self.db.publication(item.item_id) is not None:
                    raise AssertionError("Pipeline must not pre-create publication before publish_once")
                return PublicationResult(True, "telegram-456")

        pub = IdempotentPublisher(self.db)
        p = Pipeline(self.db, self.storage, Vision(), Studio(self.storage), pub, self.ia)
        p.run(self.item)

        self.assertEqual(pub.calls, 1)
        self.assertEqual(self.db.get(self.item).state, State.PUBLISHED)


    def test_armored_ia_runs_between_vision_and_studio(self):
        class IA:
            def __init__(self):
                self.calls = 0
            def generate_caption_with_evidence(self, context):
                self.calls += 1
                assert context["productName"] == "produto-final"
                from ArmoredIA.caption.generator import CaptionGenerator

                class Provider:
                    def generate_candidates(self, _task, _context, _limit):
                        return ["Olha esse charme 🧡\n#casa"]

                return CaptionGenerator(provider=Provider()).generate_with_evidence(context)

            def generate_caption(self, context):
                return self.generate_caption_with_evidence(context).caption

        ia = IA()
        import os
        old_enabled = os.environ.get("ARMORED_IA_ENABLED")
        old_caption = os.environ.get("ARMORED_IA_CAPTION_ENABLED")
        os.environ["ARMORED_IA_ENABLED"] = "1"
        os.environ["ARMORED_IA_CAPTION_ENABLED"] = "1"

        class VisionWithContext:
            def identify(self, item):
                return VisionResult(
                    "produto-final",
                    "https://example.invalid/a",
                    ia_context={"productName": "produto-final"},
                )

        try:
            p = Pipeline(
                self.db,
                self.storage,
                VisionWithContext(),
                Studio(self.storage),
                self.pub,
                ia,
            )
            p.run(self.item)
            row = self.db.get(self.item)
            self.assertEqual(row.state, State.PUBLISHED)
            self.assertEqual(row.publication_caption, "Olha esse 🧡 charme\n#casa")
            self.assertEqual(ia.calls, 1)
        finally:
            if old_enabled is None:
                os.environ.pop("ARMORED_IA_ENABLED", None)
            else:
                os.environ["ARMORED_IA_ENABLED"] = old_enabled
            if old_caption is None:
                os.environ.pop("ARMORED_IA_CAPTION_ENABLED", None)
            else:
                os.environ["ARMORED_IA_CAPTION_ENABLED"] = old_caption

    def test_telegram_timeout_enters_recovery_without_becoming_failed(self):
        class UnknownPublisher(Publisher):
            def publish(self, item):
                self.count += 1
                raise PublicationUnknownError("Telegram publication outcome is UNKNOWN")

        pub = UnknownPublisher()
        p = Pipeline(self.db, self.storage, Vision(), Studio(self.storage), pub, self.ia)

        p.run(self.item)

        row = self.db.get(self.item)
        self.assertEqual(row.state, State.RECOVERY)
        self.assertNotEqual(row.state, State.FAILED)
        self.assertEqual(pub.count, 1)
        publication = self.db.publication(self.item)
        self.assertIsNotNone(publication)
        self.assertEqual(publication["verification_status"], "PENDING")

    def test_second_run_does_not_republish(self):
        p = Pipeline(self.db, self.storage, Vision(), Studio(self.storage), self.pub, self.ia)
        p.run(self.item)
        p.run(self.item)
        self.assertEqual(self.pub.count, 1)

    def test_waiting_vision_is_retried_only_when_explicit_and_has_source_url(self):
        class CountingVision:
            def __init__(self):
                self.calls = 0
            def identify(self, item):
                self.calls += 1
                return VisionResult("produto-recuperado", "https://example.invalid/affiliate")

        self.db.conn.execute(
            "UPDATE items SET original_url=? WHERE content_id=?",
            ("https://shopee.com.br/product/123/456", self.item),
        )
        self.db.conn.commit()
        self.db.mark_vision_waiting(self.item, "old unresolved result")
        vision = CountingVision()
        pipeline = Pipeline(self.db, self.storage, vision, Studio(self.storage), self.pub, self.ia)

        # A normal run must preserve WAITING_VISION and not call Vision.
        pipeline.run(self.item, stop_after_vision=True)
        self.assertEqual(self.db.get(self.item).state, State.WAITING_VISION)
        self.assertEqual(vision.calls, 0)

        # Only the explicit recovery flag re-enters Vision; the hard gate still
        # returns to RECEIVED and never invokes Studio or Publisher.
        pipeline.run(self.item, stop_after_vision=True, retry_waiting_vision=True)
        row = self.db.get(self.item)
        self.assertEqual(row.state, State.RECEIVED)
        self.assertEqual(row.affiliate_url, "https://example.invalid/affiliate")
        self.assertEqual(vision.calls, 1)
        self.assertEqual(self.pub.count, 0)
        reasons = [r[0] for r in self.db.conn.execute(
            "SELECT reason FROM state_events WHERE content_id=? ORDER BY id", (self.item,)
        ).fetchall()]
        self.assertIn("explicit-waiting-vision-retry", reasons)

    def test_waiting_vision_without_source_url_is_not_retried(self):
        class CountingVision:
            def __init__(self):
                self.calls = 0
            def identify(self, item):
                self.calls += 1
                return VisionResult("unexpected", "https://example.invalid/affiliate")

        self.db.mark_vision_waiting(self.item, "missing source URL")
        vision = CountingVision()
        pipeline = Pipeline(self.db, self.storage, vision, Studio(self.storage), self.pub, self.ia)
        pipeline.run(self.item, stop_after_vision=True, retry_waiting_vision=True)
        self.assertEqual(self.db.get(self.item).state, State.WAITING_VISION)
        self.assertEqual(vision.calls, 0)
        self.assertEqual(self.pub.count, 0)

    def test_waiting_vision_retry_inventory_requires_source_url_and_no_approval(self):
        self.db.mark_vision_waiting(self.item, "missing URL")
        self.assertEqual(self.db.pending_waiting_vision_retries(limit=10), [])
        self.db.conn.execute(
            "UPDATE items SET original_url=? WHERE content_id=?",
            ("https://shopee.com.br/product/123/456", self.item),
        )
        self.db.conn.commit()
        candidates = self.db.pending_waiting_vision_retries(limit=10)
        self.assertEqual([item.content_id for item in candidates], [self.item])
        self.db.conn.execute(
            "UPDATE items SET affiliate_url=? WHERE content_id=?",
            ("https://example.invalid/approved", self.item),
        )
        self.db.conn.commit()
        self.assertEqual(self.db.pending_waiting_vision_retries(limit=10), [])

if __name__ == "__main__":
    unittest.main()
