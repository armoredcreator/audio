import asyncio
import tempfile
import unittest
from pathlib import Path

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.storage import Storage


class _Reader:
    def __init__(self):
        self.connected = False
        self.client = self

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True


class _Source:
    def __init__(self):
        self.reader = _Reader()


class CoordinatorConnectionTests(unittest.TestCase):
    def test_ensure_source_connection_supports_telethon_client_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            source = _Source()
            coordinator = Coordinator(
                db,
                storage,
                object(),
                object(),
                object(),
                source,
            )
            try:
                asyncio.run(coordinator._ensure_source_connection())
                self.assertTrue(source.reader.connected)
            finally:
                coordinator.close()


if __name__ == "__main__":
    unittest.main()
