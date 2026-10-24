from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from armored_core.storage_reconciliation import (  # noqa: E402
    StorageReconciliationError,
    apply_storage_copy_plan,
    build_storage_copy_plan,
    rollback_storage_copy,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify exact-hash duplicate backup media and quarantine copies only in an isolated storage root."
    )
    parser.add_argument("--database-copy", type=Path, required=True)
    parser.add_argument("--source-storage", type=Path)
    parser.add_argument("--diagnostic", type=Path)
    parser.add_argument("--copy-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply-to-copy", action="store_true")
    mode.add_argument("--rollback-manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.report.exists():
            raise StorageReconciliationError(f"report exists; refusing overwrite: {args.report}")
        root = args.copy_root.resolve(strict=True)
        if args.rollback_manifest:
            result = {"mode": "ROLLBACK_COPY", "result": rollback_storage_copy(args.rollback_manifest, root)}
        else:
            if not args.source_storage or not args.diagnostic:
                raise StorageReconciliationError("--source-storage and --diagnostic are required")
            if args.apply_to_copy:
                outcome = apply_storage_copy_plan(args.database_copy, args.source_storage, args.diagnostic, root)
                result = {"mode": "APPLY_COPY", "result": outcome}
            else:
                result = {"mode": "DRY_RUN", "result": build_storage_copy_plan(
                    args.database_copy, args.source_storage, args.diagnostic, root
                )}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("STORAGE_RECONCILIATION=SUCCESS")
        print(f"MODE={result['mode']}")
        print(f"CANDIDATES={result['result'].get('candidate_count', result['result'].get('quarantined_in_copy', 0))}")
        print(f"SOURCE_MUTATIONS={result['result'].get('source_mutations', 0)}")
        print(f"REPORT={args.report.resolve()}")
        return 0
    except Exception as exc:
        print(f"STORAGE_RECONCILIATION=FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
