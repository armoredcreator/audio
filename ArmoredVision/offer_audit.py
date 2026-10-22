"""Bounded read-only product checks; its SQLite file is an audit artifact only.

This ledger cannot update pipeline states, download media, or generate links.
Each request is charged durably before network I/O. Interrupted requests remain
IN_FLIGHT and are never silently repeated by a resumed run.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from armored_core.shopee_urls import is_supported_shopee_url
from .modules.v1.shopee_api import (
    ShopeeAPIError, ShopeeAuthenticationError, ShopeeProductNotFoundError,
    ShopeeRateLimitError, ShopeeTransientAPIError, _response_metadata,
)

APPLICATION_ID = 1094798415


class OfferAudit:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        if not self.path.name.endswith(".offer-audit.sqlite3"):
            raise ValueError("Use a dedicated .offer-audit.sqlite3 file")
        if self.path.exists():
            with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as check:
                if check.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                    raise ValueError("Existing file is not an offer audit ledger")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("xb"):
                pass
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS products(
              product_key TEXT PRIMARY KEY, shop_id TEXT NOT NULL, item_id TEXT NOT NULL,
              outcome TEXT NOT NULL DEFAULT 'NOT_QUERIED', checked_at REAL, expires_at REAL,
              error_type TEXT, metadata_json TEXT, offer_link TEXT);
            CREATE TABLE IF NOT EXISTS records(
              content_id TEXT PRIMARY KEY, product_key TEXT, historical_cause TEXT);
            CREATE TABLE IF NOT EXISTS budgets(
              budget_id TEXT PRIMARY KEY, request_limit INTEGER NOT NULL,
              used INTEGER NOT NULL DEFAULT 0, evidence TEXT NOT NULL, valid_until REAL);
            CREATE TABLE IF NOT EXISTS attempts(
              id INTEGER PRIMARY KEY, product_key TEXT NOT NULL, budget_id TEXT NOT NULL,
              started_at REAL NOT NULL, finished_at REAL, outcome TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def seed(self, rows):
        with self.db:
            for row in rows:
                identity = row.get("canonical_product_identity") or {}
                shop, item = str(identity.get("shop_id") or ""), str(identity.get("item_id") or "")
                key = f"{shop}:{item}" if shop.isascii() and item.isascii() and shop.isdigit() and item.isdigit() else None
                prior = self.db.execute("SELECT product_key FROM records WHERE content_id=?", (str(row["content_id"]),)).fetchone()
                if prior is not None and prior["product_key"] != key:
                    raise ValueError("Manifest identity changed for an existing audit record; review required")
                if key:
                    self.db.execute("INSERT OR IGNORE INTO products(product_key,shop_id,item_id) VALUES(?,?,?)", (key, shop, item))
                self.db.execute("INSERT OR IGNORE INTO records VALUES(?,?,?)", (str(row["content_id"]), key, row.get("primary_cause")))

    def import_previous_check(self, shop_id, item_id, checked_at, outcome):
        # An old no-node summary without the response envelope cannot prove a
        # valid empty nodes list. Preserve it without claiming current absence.
        key = f"{shop_id}:{item_id}"
        with self.db:
            self.db.execute("UPDATE products SET outcome=?,checked_at=? WHERE product_key=? AND outcome='NOT_QUERIED'", (outcome, checked_at, key))

    def run(self, api, *, budget_id: str, request_limit: int = 1,
            evidence: str = "single-request observation; quota unknown",
            verified_budget: bool = False, valid_until: float | None = None,
            interval_seconds: float = 5.0):
        if request_limit < 1 or request_limit > 1000 or (not verified_budget and request_limit != 1):
            raise ValueError("Unknown quota permits one observation only; larger budgets require verified evidence")
        if interval_seconds < 0:
            raise ValueError("Negative request interval")
        if verified_budget and (not valid_until or valid_until <= time.time() or not evidence.strip()):
            raise ValueError("A verified budget requires dated, unexpired evidence")
        existing = self.db.execute("SELECT * FROM budgets WHERE budget_id=?", (budget_id,)).fetchone()
        if existing and (existing["request_limit"] != request_limit or existing["evidence"] != evidence or existing["valid_until"] != valid_until):
            raise ValueError("A persisted budget cannot be enlarged or relabeled on resume")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO budgets(budget_id,request_limit,evidence,valid_until) VALUES(?,?,?,?)", (budget_id, request_limit, evidence, valid_until))
        count = 0
        stop = "BUDGET_EXHAUSTED"
        while True:
            now = time.time()
            self.db.execute("BEGIN IMMEDIATE")
            budget = self.db.execute("SELECT * FROM budgets WHERE budget_id=?", (budget_id,)).fetchone()
            if budget["valid_until"] and budget["valid_until"] <= now:
                self.db.rollback()
                stop = "BUDGET_EVIDENCE_EXPIRED"
                break
            if budget["used"] >= budget["request_limit"]:
                self.db.rollback()
                break
            row = self.db.execute("SELECT * FROM products WHERE outcome='NOT_QUERIED' OR (outcome IN ('OFFER_FOUND','OFFER_ABSENT') AND expires_at<=?) ORDER BY product_key LIMIT 1", (now,)).fetchone()
            if row is None:
                self.db.rollback()
                stop = "NO_ELIGIBLE_IDENTITIES"
                break
            self.db.execute("UPDATE budgets SET used=used+1 WHERE budget_id=?", (budget_id,))
            self.db.execute("UPDATE products SET outcome='IN_FLIGHT',expires_at=NULL WHERE product_key=?", (row["product_key"],))
            attempt = self.db.execute("INSERT INTO attempts(product_key,budget_id,started_at,outcome) VALUES(?,?,?,'IN_FLIGHT')", (row["product_key"], budget_id, now)).lastrowid
            self.db.commit()
            outcome, error, offer, ttl = "INVALID_RESPONSE", None, None, None
            try:
                product = api.get_exact_product(row["shop_id"], row["item_id"], allow_transient_retries=False)
                offer = str(product.get("offerLink") or "").strip()
                if offer and is_supported_shopee_url(offer):
                    outcome, ttl = "OFFER_FOUND", 3600
                else:
                    outcome = "PRODUCT_FOUND_OFFER_LINK_MISSING_OR_UNSAFE"
                    offer = None
            except ShopeeProductNotFoundError:
                outcome, ttl = "OFFER_ABSENT", 21600
            except ShopeeAuthenticationError as exc:
                outcome, error, stop = "AUTH_FAILURE", type(exc).__name__, "AUTH_FAILURE"
            except ShopeeRateLimitError as exc:
                outcome, error, stop = "QUOTA_FAILURE", type(exc).__name__, "QUOTA_FAILURE"
            except ShopeeTransientAPIError as exc:
                outcome, error, stop = "TRANSIENT_FAILURE", type(exc).__name__, "TRANSIENT_FAILURE"
            except ShopeeAPIError as exc:
                outcome, error, stop = "INVALID_RESPONSE", type(exc).__name__, "INVALID_RESPONSE"
            raw_metadata = getattr(api, "last_response_metadata", {})
            metadata = {"status_code": raw_metadata.get("status_code"),
                        "headers": _response_metadata(raw_metadata.get("headers", {}))}
            finished = time.time()
            with self.db:
                self.db.execute("UPDATE products SET outcome=?,checked_at=?,expires_at=?,error_type=?,metadata_json=?,offer_link=? WHERE product_key=?", (outcome, finished, finished + ttl if ttl else None, error, json.dumps(metadata), offer, row["product_key"]))
                self.db.execute("UPDATE attempts SET outcome=?,finished_at=? WHERE id=?", (outcome, finished, attempt))
            count += 1
            if outcome not in ("OFFER_FOUND", "OFFER_ABSENT"):
                if stop == "BUDGET_EXHAUSTED":
                    stop = outcome
                break
            headers = metadata.get("headers", {})
            remaining = next((headers[k] for k in ("ratelimit-remaining", "x-ratelimit-remaining", "x-rate-limit-remaining") if k in headers), None)
            if remaining is not None and str(remaining).strip() == "0":
                stop = "OBSERVED_REMAINING_ZERO"
                break
            if not verified_budget:
                stop = "QUOTA_BUDGET_UNVERIFIED"
                break
            if count < request_limit:
                time.sleep(interval_seconds)
        return {"requests_this_run": count, "stop_reason": stop, "summary": self.summary()}

    def summary(self):
        counts = {r[0]: r[1] for r in self.db.execute("SELECT outcome,COUNT(*) FROM products GROUP BY outcome")}
        return {"identities": sum(counts.values()), "by_outcome": counts,
                "records": self.db.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                "records_without_exact_identity": self.db.execute("SELECT COUNT(*) FROM records WHERE product_key IS NULL").fetchone()[0]}

    def export(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        for name, query in (
            ("identities.jsonl", "SELECT * FROM products ORDER BY product_key"),
            ("records.jsonl", "SELECT r.*,COALESCE(p.outcome,'IDENTITY_UNRESOLVED') AS current_offer_outcome,p.checked_at,p.error_type FROM records r LEFT JOIN products p USING(product_key) ORDER BY content_id"),
        ):
            with (directory / name).open("x", encoding="utf-8") as output:
                for row in self.db.execute(query):
                    output.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
