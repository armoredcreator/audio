from __future__ import annotations

"""Prepare one isolated real-audio Speech v2 smoke fixture without modifying sources.

The utility is lab-only. It opens the supplied Speech SQLite database read-only,
clones that database to storage/validation, and creates byte-identical copies of
one already-READY RVC-eligible MP4/WAV in a unique test workspace.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid
import wave
from typing import Any

from armored_core.storage import Storage


PRODUCTION_DATABASE_NAMES = {
    "armoredcreator.db",
    "armoredcreator.sqlite",
    "armoredcreator.sqlite3",
}
REQUIRED_COLUMNS = {
    "source_id", "item_id", "video_path", "wav_path", "audio_class", "language",
    "speech_detected", "music_detected", "confidence", "rvc_eligible", "status",
    "original_sha256_before", "original_sha256_after", "error", "updated_at",
}


class SmokeFixtureError(RuntimeError):
    """Cannot safely prepare the isolated Speech v2 smoke fixture."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly_connection(database: Path) -> sqlite3.Connection:
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _validate_database_path(root: Path, database: Path) -> Path:
    root = Path(root).resolve(strict=True)
    database = Path(database).expanduser().resolve(strict=True)
    if not database.is_file():
        raise SmokeFixtureError("Speech database must be an existing file")
    try:
        database.relative_to(root)
    except ValueError as exc:
        raise SmokeFixtureError("Speech database must be inside the isolated AudioLab root") from exc
    if database.name.casefold() in PRODUCTION_DATABASE_NAMES:
        raise SmokeFixtureError("Refusing a database named like the ArmoredCreator production database")
    try:
        connection = _readonly_connection(database)
        try:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='armored_speech_results'"
            ).fetchone()
            if table is None:
                raise SmokeFixtureError("Database does not contain the experimental ArmoredSpeech registry")
            columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(armored_speech_results)"
                ).fetchall()
            }
            missing = REQUIRED_COLUMNS - columns
            if missing:
                raise SmokeFixtureError(
                    "Speech registry schema is incompatible; missing columns: "
                    + ", ".join(sorted(missing))
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SmokeFixtureError(f"Cannot read experimental Speech database: {exc}") from exc
    return database


def list_ready_candidates(
    root: Path,
    speech_database: Path,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List READY clean-PT-BR rows with an existing original and WAV sidecar."""
    root = Path(root).resolve(strict=True)
    database = _validate_database_path(root, speech_database)
    if limit < 1 or limit > 200:
        raise ValueError("limit must be in the range 1..200")
    with _readonly_connection(database) as connection:
        rows = connection.execute(
            """
            SELECT source_id, item_id, video_path, wav_path, audio_class, language,
                   speech_detected, music_detected, confidence, rvc_eligible,
                   status, original_sha256_before, original_sha256_after
            FROM armored_speech_results
            WHERE status='READY'
              AND audio_class='PT_BR_NARRATION'
              AND lower(replace(coalesce(language,''), '_','-')) IN ('pt','pt-br')
              AND speech_detected=1
              AND music_detected=0
              AND rvc_eligible=1
            ORDER BY updated_at DESC, source_id, item_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    candidates = []
    for row in rows:
        original = Path(row["video_path"]).expanduser()
        wav_path = Path(row["wav_path"]) if row["wav_path"] else None
        if not original.is_file() or wav_path is None or not wav_path.is_file():
            continue
        candidates.append({
            "source_id": str(row["source_id"]),
            "item_id": str(row["item_id"]),
            "audio_class": str(row["audio_class"]),
            "language": str(row["language"] or ""),
            "original_path": str(original.resolve()),
            "wav_path": str(wav_path.resolve()),
            "confidence": row["confidence"],
        })
    return candidates


def create_smoke_fixture(
    root: Path,
    speech_database: Path,
    *,
    source_id: str,
    item_id: str,
) -> dict[str, str]:
    """Clone one READY RVC-eligible row into a disposable test DB and workspace."""
    root = Path(root).resolve(strict=True)
    database = _validate_database_path(root, speech_database)

    with _readonly_connection(database) as connection:
        row = connection.execute(
            """
            SELECT source_id, item_id, video_path, wav_path, audio_class, language,
                   speech_detected, music_detected, confidence, rvc_eligible,
                   status, error, original_sha256_before, original_sha256_after
            FROM armored_speech_results
            WHERE source_id=? AND item_id=?
            """,
            (source_id, item_id),
        ).fetchone()
    if row is None:
        raise SmokeFixtureError(f"No Speech record for source_id={source_id!r}, item_id={item_id!r}")
    if row["status"] != "READY":
        raise SmokeFixtureError(f"Selected Speech record is not READY: {row['status']!r}")
    language = str(row["language"] or "").strip().lower().replace("_", "-")
    if (
        row["audio_class"] != "PT_BR_NARRATION"
        or language not in {"pt", "pt-br"}
        or row["speech_detected"] != 1
        or row["music_detected"] != 0
        or row["rvc_eligible"] != 1
    ):
        raise SmokeFixtureError(
            "Smoke test requires a READY PT_BR_NARRATION row with explicit no-music "
            "evidence and rvc_eligible=1"
        )

    source_original = Path(row["video_path"]).expanduser().resolve(strict=True)
    source_wav = Path(row["wav_path"]).expanduser().resolve(strict=True)
    if not source_original.is_file() or source_original.suffix.casefold() != ".mp4":
        raise SmokeFixtureError("Selected original must be an existing MP4 file")
    if not source_wav.is_file() or source_wav.suffix.casefold() != ".wav":
        raise SmokeFixtureError("Selected Speech WAV sidecar must be an existing WAV file")

    try:
        with wave.open(str(source_wav), "rb") as audio:
            if audio.getnchannels() < 1 or audio.getframerate() < 1 or audio.getnframes() < 1:
                raise SmokeFixtureError("Speech WAV sidecar has no usable audio frames")
            if audio.getsampwidth() < 1 or not audio.readframes(1):
                raise SmokeFixtureError("Speech WAV sidecar cannot be read")
    except (wave.Error, EOFError, OSError) as exc:
        raise SmokeFixtureError(f"Speech WAV sidecar is invalid: {exc}") from exc

    original_hash = sha256_file(source_original)
    declared_before = row["original_sha256_before"]
    declared_after = row["original_sha256_after"]
    if declared_before and str(declared_before).lower() != original_hash.lower():
        raise SmokeFixtureError("Source registry original_sha256_before does not match the file")
    if declared_after and str(declared_after).lower() != original_hash.lower():
        raise SmokeFixtureError("Source registry original_sha256_after does not match the file")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = uuid.uuid4().hex[:6]
    test_item_id = f"v2smoke_{stamp}_{suffix}"
    batch_id = f"speech-v2-smoke-{stamp}-{suffix}"
    run_dir = root / "storage" / "validation" / batch_id
    run_dir.mkdir(parents=True, exist_ok=False)

    # Pin this disposable fixture to the lab's canonical Source 1 workspace so
    # Storage.result() returns the final MP4 beside this copied input.
    test_source_id = "source1"
    workspace = Storage(root).workspace(test_item_id, source_id=test_source_id)
    original_copy = workspace / f"{test_item_id}_finallinkoriginal.mp4"
    wav_copy = workspace / f"{test_item_id}_finallinkoriginal.wav"
    if original_copy.exists() or wav_copy.exists():
        raise SmokeFixtureError("Unique smoke workspace unexpectedly already contains media")

    try:
        shutil.copy2(source_original, original_copy)
        shutil.copy2(source_wav, wav_copy)
        if sha256_file(original_copy).lower() != original_hash.lower():
            raise SmokeFixtureError("Smoke MP4 copy SHA-256 differs from source")
        if sha256_file(wav_copy).lower() != sha256_file(source_wav).lower():
            raise SmokeFixtureError("Smoke WAV copy differs from Speech sidecar")

        # Use SQLite's backup API on a read-only source connection, and insert
        # the synthetic test identity only into this disposable cloned database.
        database_copy = run_dir / "speech_experimental_smoke.sqlite3"
        source_connection = _readonly_connection(database)
        target_connection = sqlite3.connect(database_copy)
        target_connection.row_factory = sqlite3.Row
        try:
            source_connection.backup(target_connection)
            target_connection.execute(
                """
                INSERT INTO armored_speech_results (
                    source_id, item_id, video_path, wav_path, audio_class, language,
                    speech_detected, music_detected, confidence, rvc_eligible, status,
                    original_sha256_before, original_sha256_after, error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                          strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                """,
                (
                    test_source_id, test_item_id, str(original_copy), str(wav_copy),
                    row["audio_class"], row["language"], row["speech_detected"],
                    row["music_detected"], row["confidence"], row["rvc_eligible"],
                    "READY", original_hash, original_hash,
                ),
            )
            target_connection.commit()
        finally:
            source_connection.close()
            target_connection.close()

        manifest = {
            "schema_version": 1,
            "phase": "ARMORED_SPEECH",
            "phase_status": "COMPLETE",
            "approved": True,
            "allow_copy_only": False,
            "batch_id": batch_id,
            "expected_item_count": 1,
            "items": [{
                "content_id": f"{test_source_id}_{test_item_id}",
                "telegram_message_id": test_item_id,
                "source_id": test_source_id,
                "state": "IA",
                "workspace": str(workspace),
                "original_path": str(original_copy),
                "original_sha256": original_hash,
                "affiliate_name": "speech-v2-smoke",
                "affiliate_url": f"https://example.invalid/speech-v2-smoke/{test_item_id}",
            }],
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        # Clean up only the unique disposable copy created by this helper.
        original_copy.unlink(missing_ok=True)
        wav_copy.unlink(missing_ok=True)
        shutil.rmtree(run_dir, ignore_errors=True)
        raise

    return {
        "batch_id": batch_id,
        "manifest_path": str(manifest_path),
        "speech_database_copy": str(database_copy),
        "original_copy": str(original_copy),
        "wav_copy": str(wav_copy),
        "source_original": str(source_original),
        "source_wav": str(source_wav),
        "original_sha256": original_hash,
        "source_database": str(database),
        "source_database_modified": "false",
        "workspace": str(workspace),
        "test_source_id": test_source_id,
        "test_item_id": test_item_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare an isolated Speech v2 RVC smoke fixture without modifying the source DB or originals."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--speech-database", type=Path, required=True,
                        help="path to the experimental ArmoredSpeech SQLite database inside the AudioLab")
    parser.add_argument("--list-ready", action="store_true",
                        help="list existing READY clean-PT-BR rows eligible for Melody")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--source-id", help="exact source_id from armored_speech_results")
    parser.add_argument("--item-id", help="exact item_id from armored_speech_results")
    args = parser.parse_args(argv)

    try:
        root = Path(args.root).expanduser().resolve(strict=True)
        database = _validate_database_path(root, args.speech_database)
        if args.list_ready or (not args.source_id and not args.item_id):
            candidates = list_ready_candidates(root, database, limit=args.limit)
            if not candidates:
                print("NO_READY_MELODY_CANDIDATES")
                return 1
            for item in candidates:
                print(
                    f"source_id={item['source_id']}\t"
                    f"item_id={item['item_id']}\t"
                    f"confidence={item['confidence']}\t"
                    f"original={Path(item['original_path']).name}\t"
                    f"wav={Path(item['wav_path']).name}"
                )
            return 0
        if not args.source_id or not args.item_id:
            raise SmokeFixtureError("--source-id and --item-id must be provided together")
        fixture = create_smoke_fixture(
            root,
            database,
            source_id=args.source_id,
            item_id=args.item_id,
        )
    except Exception as exc:
        print(f"SPEECH_V2_SMOKE_FIXTURE=FAILED: {type(exc).__name__}: {exc}")
        return 2

    print("SPEECH_V2_SMOKE_FIXTURE=READY")
    for key, value in fixture.items():
        print(f"{key.upper()}={value}")
    print("NEXT_COMMAND=PowerShell: run ArmoredSpeech.studio_batch_runner with the emitted MANIFEST_PATH and SPEECH_DATABASE_COPY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
