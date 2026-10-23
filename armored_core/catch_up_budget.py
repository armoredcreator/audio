from __future__ import annotations

import sqlite3
from pathlib import Path


class CatchUpBudgetError(RuntimeError):
    pass


class DurableCatchUpBudget:
    """Persistent allowlist and item cap for one bounded Coordinator run.

    Reusing a run ID after interruption resumes the same assigned items. A
    different run ID is required to authorize another batch. The ledger is
    separate from the operational item database and contains only IDs/counts.
    """

    def __init__(self, path: Path, run_id: str, limit: int) -> None:
        run_id = str(run_id or "").strip()
        if not run_id or len(run_id) > 96:
            raise CatchUpBudgetError("catch-up-run-id-required-or-too-long")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise CatchUpBudgetError("catch-up-item-limit-must-be-1-to-1000")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.limit = limit
        self.conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS catch_up_runs ("
            " run_id TEXT PRIMARY KEY, item_limit INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ");"
            "CREATE TABLE IF NOT EXISTS catch_up_run_items ("
            " run_id TEXT NOT NULL, content_id TEXT NOT NULL, assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            " PRIMARY KEY(run_id, content_id),"
            " FOREIGN KEY(run_id) REFERENCES catch_up_runs(run_id)"
            ");"
        )
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT item_limit FROM catch_up_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO catch_up_runs(run_id,item_limit) VALUES(?,?)",
                    (run_id, limit),
                )
            elif int(row[0]) != limit:
                raise CatchUpBudgetError("catch-up-run-limit-cannot-be-reconfigured")
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            self.conn.close()
            raise

    def assigned_ids(self) -> set[str]:
        return {
            str(row[0])
            for row in self.conn.execute(
                "SELECT content_id FROM catch_up_run_items WHERE run_id=?",
                (self.run_id,),
            )
        }

    @property
    def assigned_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM catch_up_run_items WHERE run_id=?",
            (self.run_id,),
        ).fetchone()[0])

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.assigned_count)

    def claim(self, content_ids) -> set[str]:
        claimed: set[str] = set()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            existing = {
                str(row[0])
                for row in self.conn.execute(
                    "SELECT content_id FROM catch_up_run_items WHERE run_id=?",
                    (self.run_id,),
                )
            }
            remaining = max(0, self.limit - len(existing))
            for value in content_ids:
                content_id = str(value)
                if content_id in existing:
                    claimed.add(content_id)
                    continue
                if remaining <= 0:
                    break
                self.conn.execute(
                    "INSERT INTO catch_up_run_items(run_id,content_id) VALUES(?,?)",
                    (self.run_id, content_id),
                )
                existing.add(content_id)
                claimed.add(content_id)
                remaining -= 1
            self.conn.commit()
            return claimed
        except BaseException:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()
