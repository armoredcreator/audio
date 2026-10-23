from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from armored_core.historical_reconciliation import (  # noqa: E402
    ReconciliationError,
    apply_plan,
    build_plan,
    database_summary,
    rollback_run,
)


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evidence-based historical duplicate and interrupted-stage reconciler for a SQLite copy."
    )
    parser.add_argument("--database-copy", type=Path, required=True, help="consistent SQLite clone to inspect")
    parser.add_argument("--media-manifest", type=Path, help="existing JSONL media/hash inventory")
    parser.add_argument("--storage-diagnostic", type=Path, help="existing read-only storage reference/orphan inventory")
    parser.add_argument("--report", type=Path, required=True, help="new JSON manifest path; never overwritten")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply-to-copy", action="store_true", help="apply the plan only inside --copy-root")
    mode.add_argument("--rollback-run", help="restore a prior applied run using its recorded pre-images")
    parser.add_argument("--copy-root", type=Path, help="root that contains the disposable database clone")
    args = parser.parse_args(argv)

    try:
        database = args.database_copy.resolve(strict=True)
        report = args.report.resolve(strict=False)
        if report.exists():
            raise ReconciliationError(f"report already exists; refusing overwrite: {report}")
        if args.media_manifest and not args.media_manifest.is_file():
            raise ReconciliationError("media manifest does not exist")
        if args.storage_diagnostic and not args.storage_diagnostic.is_file():
            raise ReconciliationError("storage diagnostic does not exist")
        mutating = args.apply_to_copy or bool(args.rollback_run)
        if mutating:
            if not args.copy_root:
                raise ReconciliationError("copy mutation requires --copy-root")
            copy_root = args.copy_root.resolve(strict=True)
            if not _under(database, copy_root):
                raise ReconciliationError("database clone must be inside --copy-root")
        else:
            copy_root = None

        before = database_summary(database)
        plan = build_plan(database, args.media_manifest, args.storage_diagnostic)
        applied = {"applied_count": 0, "changes": []}
        after = before
        second_pass = None
        if args.apply_to_copy:
            applied = apply_plan(database, plan, copy_root)
            second = build_plan(database, args.media_manifest, args.storage_diagnostic)
            second_pass = {
                "remaining_action_count": second["action_count"],
                "is_noop": second["action_count"] == 0,
            }
            if not second_pass["is_noop"]:
                raise ReconciliationError(
                    f"second reconciliation pass is not idempotent: {second_pass['remaining_action_count']} actions remain"
                )
            after = database_summary(database)
        elif args.rollback_run:
            applied = rollback_run(database, args.rollback_run, copy_root)
            after = database_summary(database)

        report.parent.mkdir(parents=True, exist_ok=True)
        result = {
            "schema_version": 1,
            "mode": "APPLY_COPY" if args.apply_to_copy else "ROLLBACK_COPY" if args.rollback_run else "DRY_RUN",
            "database_copy": str(database),
            "media_manifest": str(args.media_manifest.resolve()) if args.media_manifest else None,
            "storage_diagnostic": str(args.storage_diagnostic.resolve()) if args.storage_diagnostic else None,
            "before": before,
            "plan": plan,
            "applied": applied,
            "after": after,
            "second_pass": second_pass,
        }
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("HISTORICAL_RECONCILIATION=SUCCESS")
        print(f"MODE={result['mode']}")
        print(f"DUPLICATE_COMPONENTS={len(plan['duplicate_components'])}")
        print(f"PLANNED_ACTIONS={plan['action_count']}")
        print(f"APPLIED_ACTIONS={applied['applied_count']}")
        print(f"BLOCKED_COMPONENTS={len(plan['blocked'])}")
        print(f"IDEMPOTENT_SECOND_PASS={second_pass['is_noop'] if second_pass else 'not-run'}")
        print(f"REPORT={report}")
        return 0
    except Exception as exc:
        print(f"HISTORICAL_RECONCILIATION=FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
