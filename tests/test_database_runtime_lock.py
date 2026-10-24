import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import armored_core.database as database_module
from armored_core.coordinator import Coordinator
from armored_core.database import Database


def test_deferred_schema_initialization_allows_runtime_lease_first(tmp_path):
    db = Database(Path(tmp_path) / "armoredcreator.db", initialize=False)
    try:
        tables_before = {
            row["name"]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "items" not in tables_before

        db.acquire_runtime_lock("coordinator")

        tables_with_lease = {
            row["name"]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "runtime_locks" in tables_with_lease
        assert "items" not in tables_with_lease

        db.initialize()

        tables_after = {
            row["name"]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "items" in tables_after
        assert db.conn.execute(
            "SELECT pid FROM runtime_locks WHERE name='coordinator'"
        ).fetchone() is not None
    finally:
        db.release_runtime_lock("coordinator")
        db.close()


def test_runtime_lock_reclaims_pid_reused_by_newer_process(tmp_path, monkeypatch):
    db = Database(Path(tmp_path) / "armoredcreator.db", initialize=False)
    try:
        db.conn.execute(
            """CREATE TABLE runtime_locks (
                name TEXT PRIMARY KEY,
                pid INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL
            )"""
        )
        db.conn.execute(
            "INSERT INTO runtime_locks VALUES(?,?,?,?)",
            ("coordinator", 424242, "2026-10-14T18:42:59+00:00", "2026-10-14T18:42:59+00:00"),
        )
        db.conn.commit()
        monkeypatch.setattr(
            database_module,
            "_process_started_at_utc",
            lambda pid: datetime(2026, 10, 22, 19, 15, tzinfo=timezone.utc),
        )

        db.acquire_runtime_lock("coordinator")

        owner = db.conn.execute(
            "SELECT pid FROM runtime_locks WHERE name='coordinator'"
        ).fetchone()["pid"]
        assert owner == os.getpid()
    finally:
        db.release_runtime_lock("coordinator")
        db.close()


def test_runtime_lock_keeps_live_owner_whose_process_predates_lease(tmp_path, monkeypatch):
    db = Database(Path(tmp_path) / "armoredcreator.db", initialize=False)
    try:
        db.conn.execute(
            """CREATE TABLE runtime_locks (
                name TEXT PRIMARY KEY,
                pid INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL
            )"""
        )
        db.conn.execute(
            "INSERT INTO runtime_locks VALUES(?,?,?,?)",
            ("coordinator", 424242, "2026-10-22T19:15:00+00:00", "2026-10-22T19:15:00+00:00"),
        )
        db.conn.commit()
        monkeypatch.setattr(
            database_module,
            "_process_started_at_utc",
            lambda pid: datetime(2026, 10, 22, 19, 14, tzinfo=timezone.utc),
        )

        with pytest.raises(RuntimeError, match="runtime-lock-active"):
            db.acquire_runtime_lock("coordinator")
    finally:
        db.release_runtime_lock("coordinator")
        db.close()


def test_run_forever_reuses_preacquired_runtime_lease(monkeypatch):
    class Lease:
        def __init__(self):
            self.acquired = 0
            self.released = 0

        def acquire_runtime_lock(self, _name):
            self.acquired += 1

        def release_runtime_lock(self, _name):
            self.released += 1

    coordinator = Coordinator.__new__(Coordinator)
    coordinator.db = Lease()
    coordinator._runtime_lock_held = True
    coordinator._shutdown_requested = False

    async def no_op(**_kwargs):
        return None

    coordinator._run_forever_async = no_op
    coordinator.run_forever(poll_seconds=0.01, max_cycles=1)

    assert coordinator.db.acquired == 0
    assert coordinator.db.released == 1
    assert coordinator._runtime_lock_held is False
