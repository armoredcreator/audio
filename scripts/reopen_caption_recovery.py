from __future__ import annotations

import argparse
import json
from pathlib import Path

from armored_core.database import Database


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autoriza uma única nova tentativa auditada de legenda BLOCKED."
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    database_path = args.database.expanduser().resolve(strict=True)
    db = Database(database_path)
    try:
        db.reopen_caption_recovery(
            args.item_id,
            actor=args.actor,
            reason=args.reason,
            evidence={"database": str(database_path), "tool": "reopen_caption_recovery.py"},
        )
        events = db.caption_recovery_events(args.item_id)
        last_event = dict(events[-1]) if events else {}
        print(json.dumps({
            "item_id": args.item_id,
            "status": "BLOCKED",
            "one_retry_authorized": True,
            "audit_event": last_event.get("event_type"),
            "attempt_number": last_event.get("attempt_number"),
        }, ensure_ascii=False))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
