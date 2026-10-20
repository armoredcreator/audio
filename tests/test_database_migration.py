import sqlite3
import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_publication_schema_gets_all_recovery_columns(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "armoredcreator.db"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE publications (
                    content_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    published_message_id TEXT,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
            conn.close()

            db = Database(path)
            columns = {
                row[1]
                for row in db.conn.execute("PRAGMA table_info(publications)").fetchall()
            }
            self.assertTrue({
                "verification_status",
                "destination_chat_id",
                "destination_topic_id",
                "verified_at",
            }.issubset(columns))
            db.close()


if __name__ == "__main__":
    unittest.main()
