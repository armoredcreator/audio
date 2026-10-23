from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path
from typing import Callable


class CatchUpBudgetError(RuntimeError):
    pass


class ContinuousOperationBudget:
    """Installation-wide persistent daily allowance with resumable batches.

    The ledger has one immutable configuration per file. Changing an operation
    ID cannot reset its daily usage. Batch IDs remain open across restarts and
    are closed only after their assigned items reach a durable hold/terminal
    state.
    """

    def __init__(
        self,
        path: Path,
        operation_id: str,
        max_items_per_day: int,
        batch_size: int,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.operation_id = str(operation_id or "").strip()
        self.max_items_per_day = max_items_per_day
        self.batch_size = batch_size
        self._now = now or (lambda: datetime.now(timezone.utc))
        if not self.operation_id or len(self.operation_id) > 48:
            raise CatchUpBudgetError("continuous-operation-id-required-or-too-long")
        if isinstance(max_items_per_day, bool) or not isinstance(max_items_per_day, int):
            raise CatchUpBudgetError("continuous-daily-item-limit-must-be-an-integer")
        if not 1 <= max_items_per_day <= 10000:
            raise CatchUpBudgetError("continuous-daily-item-limit-must-be-1-to-10000")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise CatchUpBudgetError("continuous-batch-size-must-be-an-integer")
        if not 1 <= batch_size <= min(1000, max_items_per_day):
            raise CatchUpBudgetError("continuous-batch-size-must-be-1-to-min-1000-daily-limit")
        self.conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS continuous_operation_config ("
            " singleton INTEGER PRIMARY KEY CHECK(singleton=1), operation_id TEXT NOT NULL,"
            " max_items_per_day INTEGER NOT NULL, batch_size INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS continuous_daily_items ("
            " day TEXT NOT NULL, content_id TEXT NOT NULL, assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            " PRIMARY KEY(day,content_id));"
            "CREATE TABLE IF NOT EXISTS continuous_batches ("
            " run_id TEXT PRIMARY KEY, day TEXT NOT NULL, sequence INTEGER NOT NULL,"
            " item_limit INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')) ,"
            " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(day,sequence));"
        )
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT operation_id,max_items_per_day,batch_size "
                "FROM continuous_operation_config WHERE singleton=1"
            ).fetchone()
            config = (self.operation_id, self.max_items_per_day, self.batch_size)
            if row is None:
                self.conn.execute(
                    "INSERT INTO continuous_operation_config "
                    "(singleton,operation_id,max_items_per_day,batch_size) VALUES(1,?,?,?)",
                    config,
                )
            elif tuple(row) != config:
                raise CatchUpBudgetError("continuous-budget-config-is-persistent-and-cannot-be-reconfigured")
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            self.conn.close()
            raise

    @property
    def day(self) -> str:
        return self._now().astimezone(timezone.utc).date().isoformat()

    @property
    def used_today(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM continuous_daily_items WHERE day=?", (self.day,)
        ).fetchone()[0])

    @property
    def remaining(self) -> int:
        return max(0, self.max_items_per_day - self.used_today)

    def try_claim(self, content_id: str) -> bool:
        day = self.day
        value = str(content_id)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            exists = self.conn.execute(
                "SELECT 1 FROM continuous_daily_items WHERE day=? AND content_id=?",
                (day, value),
            ).fetchone()
            if exists:
                self.conn.commit()
                return True
            used = int(self.conn.execute(
                "SELECT COUNT(*) FROM continuous_daily_items WHERE day=?", (day,)
            ).fetchone()[0])
            if used >= self.max_items_per_day:
                self.conn.commit()
                return False
            self.conn.execute(
                "INSERT INTO continuous_daily_items(day,content_id) VALUES(?,?)",
                (day, value),
            )
            self.conn.commit()
            return True
        except BaseException:
            self.conn.rollback()
            raise

    def open_batch(self) -> tuple[str, int] | None:
        day = self.day
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "UPDATE continuous_batches SET status='CLOSED' "
                "WHERE status='OPEN' AND day<>?", (day,)
            )
            row = self.conn.execute(
                "SELECT run_id,item_limit FROM continuous_batches "
                "WHERE day=? AND status='OPEN' ORDER BY sequence DESC LIMIT 1", (day,)
            ).fetchone()
            if row is not None:
                self.conn.commit()
                return str(row[0]), int(row[1])
            used = int(self.conn.execute(
                "SELECT COUNT(*) FROM continuous_daily_items WHERE day=?", (day,)
            ).fetchone()[0])
            allowance = self.max_items_per_day - used
            if allowance <= 0:
                self.conn.commit()
                return None
            sequence = int(self.conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM continuous_batches WHERE day=?",
                (day,),
            ).fetchone()[0])
            run_id = f"{self.operation_id}-{day}-{sequence:04d}"
            limit = min(self.batch_size, allowance)
            self.conn.execute(
                "INSERT INTO continuous_batches(run_id,day,sequence,item_limit,status) "
                "VALUES(?,?,?,?, 'OPEN')", (run_id, day, sequence, limit)
            )
            self.conn.commit()
            return run_id, limit
        except BaseException:
            self.conn.rollback()
            raise

    def close_batch(self, run_id: str) -> None:
        self.conn.execute(
            "UPDATE continuous_batches SET status='CLOSED' WHERE run_id=? AND status='OPEN'",
            (str(run_id),),
        )

    def seconds_until_reset(self) -> float:
        now = self._now().astimezone(timezone.utc)
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), timezone.utc)
        return max(0.1, (midnight - now).total_seconds())

    def close(self) -> None:
        self.conn.close()


class DurableCatchUpBudget:
    """Persistent allowlist and item cap for one bounded Coordinator run.

    Reusing a run ID after interruption resumes the same assigned items. A
    different run ID is required to authorize another batch. The ledger is
    separate from the operational item database and contains only IDs/counts.
    """

    def __init__(
        self, path: Path, run_id: str, limit: int,
        *, continuous_budget: ContinuousOperationBudget | None = None,
    ) -> None:
        run_id = str(run_id or "").strip()
        if not run_id or len(run_id) > 96:
            raise CatchUpBudgetError("catch-up-run-id-required-or-too-long")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise CatchUpBudgetError("catch-up-item-limit-must-be-1-to-1000")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.limit = limit
        self.continuous_budget = continuous_budget
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
        batch_remaining = max(0, self.limit - self.assigned_count)
        if self.continuous_budget is not None:
            return min(batch_remaining, self.continuous_budget.remaining)
        return batch_remaining

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
                if (
                    self.continuous_budget is not None
                    and not self.continuous_budget.try_claim(content_id)
                ):
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
