from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from armored_core.database import Database
from armored_core.services import IngestMessage, SyncService
from armored_core.storage import Storage


class SyncOriginalPreservationTests(unittest.TestCase):
    def test_retry_keeps_renamed_original_after_sqlite_hash_write_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            source = root / "source.mp4"
            source.write_bytes(b"complete-original-from-source")
            service = SyncService(db, storage)
            message = IngestMessage(
                telegram_message_id="501",
                source_id="source-test",
                source_path=source,
            )
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            content_id = db.content_id_for("501", "source-test")
            original = storage.original(content_id, ".mp4", source_id="source-test")

            try:
                with patch.object(
                    db,
                    "finalize_original_path",
                    side_effect=RuntimeError("simulated sqlite commit failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "simulated sqlite commit failure"):
                        service.ingest_message(message)

                self.assertTrue(original.is_file())
                self.assertEqual(original.read_bytes(), source.read_bytes())
                self.assertFalse(original.with_suffix(".mp4.part").exists())
                self.assertIsNone(db.get(content_id).original_sha256)

                self.assertEqual(service.ingest_message(message), content_id)
                self.assertEqual(db.get(content_id).original_sha256, expected)
                self.assertEqual(original.read_bytes(), source.read_bytes())
            finally:
                db.close()

    def test_existing_original_hash_mismatch_is_blocked_without_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            source = root / "source.mp4"
            source.write_bytes(b"verified-original")
            service = SyncService(db, storage)
            message = IngestMessage(
                telegram_message_id="502",
                source_id="source-test",
                source_path=source,
            )
            try:
                content_id = service.ingest_message(message)
                original = db.get(content_id).original_path
                original.write_bytes(b"modified-original")

                with self.assertRaisesRegex(IOError, "existing-original-hash-mismatch"):
                    service.ingest_message(message)

                self.assertEqual(original.read_bytes(), b"modified-original")
                self.assertEqual(db.get(content_id).original_sha256, hashlib.sha256(b"verified-original").hexdigest())
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
