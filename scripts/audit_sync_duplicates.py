from __future__ import annotations

"""Read-only duplicate audit for an explicitly supplied ArmoredCreator SQLite DB.

The script takes a consistent SQLite backup into a *new* AudioLab validation
folder, analyzes only the backup, and writes reports beside it. It never runs
schema migrations or UPDATE/DELETE statements against the source database.
"""

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PRODUCT_URL_PATTERNS = (
    re.compile(r"-i\.(\d+)\.(\d+)(?:/|$)", re.IGNORECASE),
    re.compile(r"/opaanlp/(\d+)/(\d+)", re.IGNORECASE),
    re.compile(r"/product/(\d+)/(\d+)", re.IGNORECASE),
    re.compile(r"/(\d+)/(\d+)(?:[/?#]|$)", re.IGNORECASE),
)
SHOPEE_HOST_SUFFIXES = ("shopee.com.br", "shopee.co", "shopee.ee")

GROUP_FIELDS = (
    "match_type",
    "match_key",
    "group_size",
    "vision_approved_count",
    "source_count",
    "source_ids",
    "content_ids",
    "states",
)
ITEM_FIELDS = (
    "match_type",
    "match_key",
    "group_size",
    "vision_approved_count",
    "content_id",
    "source_id",
    "telegram_message_id",
    "state",
    "vision_approved",
    "product_identity_source",
    "original_url",
)
UNLINKED_FIELDS = (
    "content_id",
    "source_id",
    "telegram_message_id",
    "state",
    "vision_approved",
    "last_error",
)


class AuditError(RuntimeError):
    """The requested database/report path cannot be audited safely."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_only_uri(path: Path) -> str:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise AuditError(f"SQLite source is not a file: {resolved}")
    return resolved.as_uri() + "?mode=ro"


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_read_only_uri(path), uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    # Defense in depth: the connection is opened mode=ro and query_only blocks
    # writes at the connection level as well.
    connection.execute("PRAGMA query_only = ON")
    return connection


def create_consistent_snapshot(source_db: Path, destination_db: Path) -> None:
    """Copy one consistent SQLite snapshot without writing to the source DB."""
    source_db = Path(source_db).resolve(strict=True)
    destination_db = Path(destination_db).resolve(strict=False)
    if not source_db.is_file():
        raise AuditError(f"SQLite source is not a file: {source_db}")
    if source_db == destination_db:
        raise AuditError("Snapshot destination must differ from the source database.")
    if destination_db.exists():
        raise AuditError(f"Snapshot already exists; refusing to overwrite: {destination_db}")

    destination_db.parent.mkdir(parents=True, exist_ok=True)
    source = None
    destination = None
    try:
        source = _connect_read_only(source_db)
        destination = sqlite3.connect(destination_db, timeout=15)
        source.backup(destination, pages=256, sleep=0.05)
        destination.commit()
    except Exception:
        # Preserve any partial snapshot as forensic evidence; a fresh run must
        # use a new report directory instead of silently overwriting it.
        raise
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()


def _first(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row:
            value = row[name]
            if value is not None:
                return value
    return default


def _clean_text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _vision_context(row: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    raw = _clean_text(_first(row, "ia_context_json", "vision_context_json", default=""))
    if not raw:
        return None, False
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, True
    return (value if isinstance(value, dict) else None), False


def _product_identity_from_context(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None
    shop_id = _clean_text(_first(context, "shopId", "shop_id"))
    item_id = _clean_text(_first(context, "itemId", "item_id"))
    if not shop_id or not item_id:
        return None
    return f"{shop_id}:{item_id}"


def product_identity_from_direct_url(url: str | None) -> str | None:
    """Extract product IDs from an explicit Shopee product URL without network I/O."""
    value = _clean_text(url)
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return None

    if not any(host == suffix or host.endswith("." + suffix) for suffix in SHOPEE_HOST_SUFFIXES):
        return None
    path = parsed.path or ""
    for pattern in PRODUCT_URL_PATTERNS:
        match = pattern.search(path)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
    return None


def _canonical_product_identity(
    row: dict[str, Any],
    original_url: str | None,
) -> tuple[str | None, str | None, bool]:
    context, parse_error = _vision_context(row)
    from_context = _product_identity_from_context(context)
    if from_context:
        return from_context, "vision_context", parse_error
    from_url = product_identity_from_direct_url(original_url)
    if from_url:
        return from_url, "direct_product_url", parse_error
    return None, None, parse_error


def _item_record(row: dict[str, Any], ordinal: int) -> dict[str, Any]:
    source_id = _clean_text(_first(row, "source_id", default="telegram")) or "telegram"
    message_id = _clean_text(_first(row, "telegram_message_id", "message_id", default=""))
    content_id = _clean_text(_first(row, "content_id", "item_id", default=""))
    if not content_id:
        content_id = f"{source_id}:{message_id or ordinal}"
    original_url = _clean_text(_first(row, "original_url", "source_url", default=""))
    affiliate_url = _clean_text(_first(row, "affiliate_url", "affiliate_link", default=""))
    state = _clean_text(_first(row, "state", "status", default="UNKNOWN")) or "UNKNOWN"
    product_key, identity_source, context_parse_error = _canonical_product_identity(
        row, original_url or None
    )
    return {
        "content_id": content_id,
        "source_id": source_id,
        "telegram_message_id": message_id,
        "state": state,
        "original_url": original_url,
        "vision_approved": bool(affiliate_url),
        "product_key": product_key,
        "product_identity_source": identity_source or "",
        "last_error": _clean_text(_first(row, "last_error", "error", default="")),
        "context_parse_error": context_parse_error,
    }


def _groups(records: list[dict[str, Any]], field: str) -> list[tuple[str, list[dict[str, Any]]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if field == "url_key":
            # This mirrors the current textual guard conservatively: trim and
            # case-fold only. It intentionally does not drop query parameters.
            key = record["original_url"].strip().casefold()
        else:
            key = record.get("product_key") or ""
        if key:
            buckets[key].append(record)

    result = []
    for key, members in sorted(buckets.items(), key=lambda pair: pair[0]):
        distinct_ids = {member["content_id"] for member in members}
        if len(distinct_ids) > 1:
            result.append((key, members))
    return result


def _schema_and_records(snapshot_db: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    connection = _connect_read_only(snapshot_db)
    try:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "items" not in tables:
            raise AuditError("SQLite snapshot has no items table; no report was produced.")

        columns = [
            str(row["name"])
            for row in connection.execute('PRAGMA table_info("items")').fetchall()
        ]
        if "original_url" not in columns and "source_url" not in columns:
            warnings.append("No URL column was found; URL duplicate analysis is unavailable.")
        if "ia_context_json" not in columns and "vision_context_json" not in columns:
            warnings.append("No Vision context JSON column was found; canonical product analysis relies on direct URLs only.")

        raw_rows = connection.execute('SELECT * FROM "items"').fetchall()
        records = [_item_record(dict(row), index) for index, row in enumerate(raw_rows, start=1)]
        malformed_context_rows = sum(record["context_parse_error"] for record in records)
        if malformed_context_rows:
            warnings.append(f"{malformed_context_rows} row(s) have non-empty but malformed Vision-context JSON.")

        schema = {
            "items_columns": columns,
            "items_table_count": len(raw_rows),
            "has_original_url": "original_url" in columns or "source_url" in columns,
            "has_vision_context": "ia_context_json" in columns or "vision_context_json" in columns,
            "has_affiliate_url": "affiliate_url" in columns or "affiliate_link" in columns,
        }
        return schema, records, warnings
    finally:
        connection.close()


def _group_rows(
    match_type: str,
    groups: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    group_rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    for key, members in groups:
        approved = sum(bool(member["vision_approved"]) for member in members)
        sources = sorted({member["source_id"] for member in members})
        content_ids = sorted({member["content_id"] for member in members})
        states = sorted({member["state"] for member in members})
        group_rows.append({
            "match_type": match_type,
            "match_key": key,
            "group_size": len(content_ids),
            "vision_approved_count": approved,
            "source_count": len(sources),
            "source_ids": "|".join(sources),
            "content_ids": "|".join(content_ids),
            "states": "|".join(states),
        })
        for member in members:
            item_rows.append({
                "match_type": match_type,
                "match_key": key,
                "group_size": len(content_ids),
                "vision_approved_count": approved,
                "content_id": member["content_id"],
                "source_id": member["source_id"],
                "telegram_message_id": member["telegram_message_id"],
                "state": member["state"],
                "vision_approved": member["vision_approved"],
                "product_identity_source": member["product_identity_source"],
                "original_url": member["original_url"],
            })
    return group_rows, item_rows


def audit_snapshot(snapshot_db: Path) -> dict[str, Any]:
    """Analyze a SQLite snapshot and return a report without mutating it."""
    schema, records, warnings = _schema_and_records(Path(snapshot_db))
    url_groups = _groups(records, "url_key")
    product_groups = _groups(records, "product_key")

    url_group_rows, url_item_rows = _group_rows("url_text", url_groups)
    product_group_rows, product_item_rows = _group_rows("canonical_product", product_groups)

    groups = url_group_rows + product_group_rows
    duplicate_items = url_item_rows + product_item_rows
    unlinked = [
        {
            "content_id": record["content_id"],
            "source_id": record["source_id"],
            "telegram_message_id": record["telegram_message_id"],
            "state": record["state"],
            "vision_approved": record["vision_approved"],
            "last_error": record["last_error"],
        }
        for record in records
        if not record["original_url"]
    ]

    url_groups_with_multiple_approved = sum(
        int(group["vision_approved_count"] >= 2) for group in url_group_rows
    )
    product_groups_with_multiple_approved = sum(
        int(group["vision_approved_count"] >= 2) for group in product_group_rows
    )
    product_keys = {record["product_key"] for record in records if record["product_key"]}
    sources = sorted({record["source_id"] for record in records})

    return {
        "schema_version": 1,
        "audit_mode": "READ_ONLY_SNAPSHOT",
        "snapshot_database": Path(snapshot_db).name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema": schema,
        "sources": sources,
        "counts": {
            "items_total": len(records),
            "items_with_original_url": sum(bool(r["original_url"]) for r in records),
            "items_without_original_url": len(unlinked),
            "items_with_vision_approved_affiliate_url": sum(bool(r["vision_approved"]) for r in records),
            "items_with_canonical_product_identity": sum(bool(r["product_key"]) for r in records),
            "unique_canonical_product_identities": len(product_keys),
            "malformed_vision_context_json_rows": sum(r["context_parse_error"] for r in records),
            "url_duplicate_groups": len(url_group_rows),
            "url_duplicate_items": len(url_item_rows),
            "url_duplicate_groups_with_2plus_vision_approved": url_groups_with_multiple_approved,
            "url_duplicate_items_with_vision_approved_affiliate_url": sum(
                bool(r["vision_approved"]) for r in url_item_rows
            ),
            "canonical_product_duplicate_groups": len(product_group_rows),
            "canonical_product_duplicate_items": len(product_item_rows),
            "canonical_product_duplicate_groups_with_2plus_vision_approved": product_groups_with_multiple_approved,
            "canonical_product_duplicate_items_with_vision_approved_affiliate_url": sum(
                bool(r["vision_approved"]) for r in product_item_rows
            ),
        },
        "warnings": warnings,
        "interpretation": [
            "A duplicate group is diagnostic evidence, not permission to delete, merge, or change an existing item.",
            "URL-text groups use trim + case-fold only and preserve query parameters.",
            "Canonical product groups use persisted Vision shopId/itemId when available, otherwise product IDs extractable directly from a Shopee product URL.",
            "The audit performs no HTTP requests and cannot resolve shortened URLs whose product ID is not stored in Vision context.",
            "An affiliate_url is treated as evidence that Vision has persisted an approval; review states and events before any future reconciliation.",
        ],
        "_duplicate_group_rows": groups,
        "_duplicate_item_rows": duplicate_items,
        "_unlinked_rows": unlinked,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(report: dict[str, Any], report_dir: Path, snapshot_db: Path) -> None:
    """Write new artifacts outside the audited DB; refuse all overwrites."""
    report_dir = Path(report_dir)
    outputs = (
        report_dir / "duplicate-audit.json",
        report_dir / "duplicate-groups.csv",
        report_dir / "duplicate-items.csv",
        report_dir / "unlinked-items.csv",
    )
    if any(path.exists() for path in outputs):
        raise AuditError("At least one report artifact already exists; refusing to overwrite.")
    report_dir.mkdir(parents=True, exist_ok=True)

    public_report = dict(report)
    group_rows = public_report.pop("_duplicate_group_rows")
    item_rows = public_report.pop("_duplicate_item_rows")
    unlinked_rows = public_report.pop("_unlinked_rows")
    public_report["snapshot_sha256"] = sha256_file(snapshot_db)
    public_report["report_artifacts"] = {
        "snapshot_database": Path(snapshot_db).name,
        "duplicate_groups_csv": outputs[1].name,
        "duplicate_items_csv": outputs[2].name,
        "unlinked_items_csv": outputs[3].name,
    }
    outputs[0].write_text(
        json.dumps(public_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(outputs[1], group_rows, GROUP_FIELDS)
    _write_csv(outputs[2], item_rows, ITEM_FIELDS)
    _write_csv(outputs[3], unlinked_rows, UNLINKED_FIELDS)


def _resolve_report_dir(root: Path, requested: Path) -> Path:
    root = Path(root).resolve(strict=True)
    validation_root = (root / "storage" / "validation").resolve(strict=False)
    destination = Path(requested).expanduser()
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve(strict=False)
    try:
        destination.relative_to(validation_root)
    except ValueError as exc:
        raise AuditError(
            f"Report directory must be inside {validation_root}; got {destination}"
        ) from exc
    if destination == validation_root:
        raise AuditError("Choose a new, uniquely named child directory under storage/validation.")
    if destination.exists():
        raise AuditError(f"Report directory already exists; refusing to overwrite: {destination}")
    return destination


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Create a read-only SQLite snapshot and report global Sync/Vision duplicates."
    )
    parser.add_argument("--root", type=Path, default=default_root, help="raiz do AudioLab")
    parser.add_argument("--database", type=Path, required=True, help="SQLite de origem para abrir apenas em modo read-only")
    parser.add_argument("--report-dir", type=Path, required=True, help="diretório NOVO sob storage/validation")
    args = parser.parse_args(argv)

    try:
        root = Path(args.root).resolve(strict=True)
        database = Path(args.database).expanduser().resolve(strict=True)
        if not database.is_file():
            raise AuditError(f"SQLite source is not a file: {database}")
        report_dir = _resolve_report_dir(root, args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=False)
        snapshot_db = report_dir / "source-snapshot.sqlite3"
        create_consistent_snapshot(database, snapshot_db)
        report = audit_snapshot(snapshot_db)
        write_report(report, report_dir, snapshot_db)
        counts = report["counts"]
        print("SYNC_DUPLICATE_AUDIT=SUCCESS")
        print("MODE=READ_ONLY_SNAPSHOT")
        print(f"ITEMS={counts['items_total']}")
        print(f"URL_DUPLICATE_GROUPS={counts['url_duplicate_groups']}")
        print(f"URL_GROUPS_WITH_2PLUS_VISION_APPROVED={counts['url_duplicate_groups_with_2plus_vision_approved']}")
        print(f"PRODUCT_DUPLICATE_GROUPS={counts['canonical_product_duplicate_groups']}")
        print(
            "PRODUCT_GROUPS_WITH_2PLUS_VISION_APPROVED="
            f"{counts['canonical_product_duplicate_groups_with_2plus_vision_approved']}"
        )
        print(f"UNLINKED_ITEMS={counts['items_without_original_url']}")
        print(f"REPORT={report_dir / 'duplicate-audit.json'}")
        print(f"SNAPSHOT={snapshot_db}")
        return 0
    except Exception as exc:
        print(f"SYNC_DUPLICATE_AUDIT=FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
