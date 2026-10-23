from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class QueryBudgetError(RuntimeError):
    pass


class ShopeeQueryBudget:
    """Crash-safe, process-independent budget for paid Affiliate API lookups.

    A request is consumed before it leaves the process. If the process crashes
    during transport, the slot stays consumed so a restart cannot exceed the
    operator's authorized quota.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS budgets ("
            "budget_id TEXT PRIMARY KEY, request_limit INTEGER NOT NULL, "
            "evidence TEXT NOT NULL, valid_until TEXT NOT NULL, "
            "used INTEGER NOT NULL DEFAULT 0 CHECK(used>=0))"
        )

    @staticmethod
    def _valid_until(value: str) -> datetime:
        raw = str(value or "").strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QueryBudgetError("quota-budget-valid-until-invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise QueryBudgetError("quota-budget-valid-until-requires-timezone")
        return parsed.astimezone(timezone.utc)

    def consume(
        self,
        *,
        budget_id: str,
        request_limit: int,
        evidence: str,
        valid_until: str,
    ) -> int:
        budget_id = str(budget_id or "").strip()
        evidence = str(evidence or "").strip()
        expiry = self._valid_until(valid_until)
        if not budget_id or not evidence:
            raise QueryBudgetError("quota-budget-id-and-evidence-required")
        if not isinstance(request_limit, int) or isinstance(request_limit, bool) or not 1 <= request_limit <= 1000:
            raise QueryBudgetError("quota-budget-limit-must-be-1-to-1000")
        if expiry <= datetime.now(timezone.utc):
            raise QueryBudgetError("quota-budget-expired")
        expiry_text = expiry.isoformat(timespec="seconds")

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT request_limit,evidence,valid_until,used FROM budgets WHERE budget_id=?",
                (budget_id,),
            ).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO budgets(budget_id,request_limit,evidence,valid_until,used) "
                    "VALUES(?,?,?,?,0)",
                    (budget_id, request_limit, evidence, expiry_text),
                )
                used = 0
            else:
                if (int(row[0]), str(row[1]), str(row[2])) != (
                    request_limit,
                    evidence,
                    expiry_text,
                ):
                    raise QueryBudgetError("quota-budget-configuration-conflict")
                used = int(row[3])
            if used >= request_limit:
                raise QueryBudgetError("quota-budget-exhausted")
            used += 1
            self.conn.execute(
                "UPDATE budgets SET used=? WHERE budget_id=?",
                (used, budget_id),
            )
            self.conn.commit()
            return used
        except BaseException:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()
