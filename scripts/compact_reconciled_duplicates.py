from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from armored_core.database_compaction import (  # noqa: E402
    CompactionError,
    compact_duplicates,
    inspect_compaction,
    rollback_compaction,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reversible compaction of already-reconciled duplicate rows in a SQLite copy."
    )
    parser.add_argument("--database-copy", type=Path, required=True)
    parser.add_argument("--copy-root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply-to-copy", action="store_true")
    mode.add_argument("--rollback-run")
    args = parser.parse_args(argv)
    try:
        if args.report.exists():
            raise CompactionError(f"report already exists; refusing overwrite: {args.report}")
        database = args.database_copy.resolve(strict=True)
        if args.apply_to_copy or args.rollback_run:
            if not args.copy_root:
                raise CompactionError("mutations require explicit --copy-root")
            root = args.copy_root.resolve(strict=True)
            if args.apply_to_copy:
                result = {"mode": "APPLY_COPY", "result": compact_duplicates(database, root)}
            else:
                result = {
                    "mode": "ROLLBACK_COPY",
                    "result": rollback_compaction(database, root, args.rollback_run),
                }
        else:
            result = {"mode": "DRY_RUN", "result": inspect_compaction(database)}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        details = result["result"]
        print("DUPLICATE_COMPACTION=SUCCESS")
        print(f"MODE={result['mode']}")
        print(f"ARCHIVED={details.get('archived_count', details.get('compactable_count', 0))}")
        if details.get("run_id"):
            print(f"RUN_ID={details['run_id']}")
        print(f"REPORT={args.report.resolve()}")
        return 0
    except Exception as exc:
        print(f"DUPLICATE_COMPACTION=FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
