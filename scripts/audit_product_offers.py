"""Create/resume a private offer audit without opening the operational database."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ArmoredVision.offer_audit import OfferAudit
from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI


def aware_timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("An explicit timezone is required; do not infer the host timezone")
    return parsed.timestamp()


def legacy_check_time(row):
    raw = row.get("checked_at_utc") or row.get("checked_at") or row.get("checked_date")
    if not raw:
        return None, None
    try:
        return aware_timestamp(str(raw)), str(raw)
    except (ValueError, TypeError):
        # Date-only receipts remain date-only evidence. Midnight is not an
        # observed query time and must never be invented during migration.
        return None, str(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--previous-checks", type=Path, action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--budget-id")
    parser.add_argument("--verified-budget", type=Path,
                        help="Local JSON: request_limit, evidence, valid_until UTC timestamp, interval_seconds")
    parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args()
    audit = OfferAudit(args.ledger)
    try:
        with args.manifest.open(encoding="utf-8") as source:
            audit.seed(json.loads(line) for line in source if line.strip())
        for previous in args.previous_checks:
            with previous.open(encoding="utf-8") as source:
                for line in source:
                    row = json.loads(line)
                    if row.get("shop_id") and row.get("item_id"):
                        # Legacy summaries omitted the raw envelope; keep that
                        # limitation rather than treating missing nodes as [].
                        checked_at, raw_time = legacy_check_time(row)
                        audit.import_previous_check(row["shop_id"], row["item_id"], checked_at,
                                                    "LEGACY_QUERY_RESPONSE_SHAPE_UNAVAILABLE",
                                                    checked_at_source=raw_time)
        result = {"requests_this_run": 0, "stop_reason": "PREVIEW_ONLY", "summary": audit.summary()}
        if args.execute:
            if not args.budget_id:
                parser.error("--execute requires a stable --budget-id")
            kwargs = {"budget_id": args.budget_id}
            if args.verified_budget:
                grant = json.loads(args.verified_budget.read_text(encoding="utf-8"))
                kwargs.update(request_limit=grant["request_limit"], evidence=str(grant["evidence"]),
                              valid_until=aware_timestamp(grant["valid_until"]),
                              interval_seconds=float(grant["interval_seconds"]), verified_budget=True)
            api = ShopeeAffiliateAPI()
            try:
                result = audit.run(api, **kwargs)
            finally:
                api.close()
        audit.export(args.export)
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        (args.export / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
    finally:
        audit.close()


if __name__ == "__main__":
    main()
