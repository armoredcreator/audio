from __future__ import annotations

import hashlib
import json
import sqlite3
import wave
from pathlib import Path

import pytest

from ArmoredSpeech.studio_batch_runner import BatchManifestError, run_complete_batch


SCHEMA = """
CREATE TABLE armored_speech_results (
    source_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    video_path TEXT NOT NULL,
    wav_path TEXT,
    audio_class TEXT NOT NULL,
    language TEXT,
    speech_detected INTEGER NOT NULL,
    music_detected INTEGER,
    confidence REAL,
    rvc_eligible INTEGER NOT NULL,
    status TEXT NOT NULL,
    original_sha256_before TEXT,
    original_sha256_after TEXT,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT 'test',
    PRIMARY KEY (source_id, item_id)
)
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 160)


def _fixture(root: Path, *, item_count: int = 2):
    items = []
    rows = []
    for number in range(1, item_count + 1):
        message_id = f"telegram-{number}"
        workspace = root / "storage" / "Videos GRUPO_FONTE_1" / message_id
        workspace.mkdir(parents=True, exist_ok=True)
        original = workspace / f"{message_id}_finallinkoriginal.mp4"
        original.write_bytes(f"synthetic disposable video {number}".encode())
        wav_path = original.with_suffix(".wav")
        _write_wav(wav_path)
        digest = _sha(original)
        rows.append((
            "source1", message_id, str(original), str(wav_path),
            "PT_BR_NARRATION", "pt-br", 1, 0, 0.99, 1, "READY", digest, digest,
        ))
        items.append({
            "content_id": f"source1_{message_id}",
            "telegram_message_id": message_id,
            "source_id": "source1",
            "state": "IA",
            "workspace": str(workspace),
            "original_path": str(original),
            "original_sha256": digest,
            "affiliate_name": "demo",
            "affiliate_url": f"https://shopee.com.br/product/demo-{number}",
        })

    database = root / "storage" / "database" / "speech.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as con:
        con.execute(SCHEMA)
        con.executemany(
            """
            INSERT INTO armored_speech_results (
                source_id, item_id, video_path, wav_path, audio_class, language,
                speech_detected, music_detected, confidence, rvc_eligible, status,
                original_sha256_before, original_sha256_after
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )

    manifest_path = root / "storage" / "validation" / "speech-phase-complete.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "phase": "ARMORED_SPEECH",
        "phase_status": "COMPLETE",
        "approved": True,
        "allow_copy_only": True,
        "batch_id": "test-batch-001",
        "expected_item_count": len(items),
        "items": items,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path, database, manifest


def test_runner_executes_real_studio_boundary_in_explicit_copy_only_mode(tmp_path):
    manifest_path, database, manifest = _fixture(tmp_path)
    report_path = tmp_path / "storage" / "validation" / "integration-report.json"
    before = {
        (item["source_id"], item["telegram_message_id"]): item["original_sha256"]
        for item in manifest["items"]
    }

    report = run_complete_batch(
        root=tmp_path,
        manifest_path=manifest_path,
        speech_database=database,
        report_path=report_path,
        copy_only=True,
    )

    assert report["status"] == "SUCCESS"
    assert report["mode"] == "COPY_ONLY_TEST"
    assert report["originals_intact"] is True
    assert len(report["processed"]) == 2
    assert report_path.is_file()
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "SUCCESS"

    for result in report["processed"]:
        output = Path(result["output_path"])
        assert output.is_file() and output.stat().st_size > 0
        key = (result["source_id"], result["item_id"])
        item = next(
            row for row in manifest["items"]
            if (row["source_id"], row["telegram_message_id"]) == key
        )
        assert _sha(Path(item["original_path"])) == before[key]
        assert output != Path(item["original_path"])


def test_runner_rejects_batch_without_explicit_completion(tmp_path):
    manifest_path, database, manifest = _fixture(tmp_path, item_count=1)
    manifest["phase_status"] = "RUNNING"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BatchManifestError, match="COMPLETE"):
        run_complete_batch(
            root=tmp_path,
            manifest_path=manifest_path,
            speech_database=database,
            copy_only=True,
        )


def test_runner_preflights_entire_speech_batch_before_first_studio_call(tmp_path):
    manifest_path, database, manifest = _fixture(tmp_path, item_count=2)
    with sqlite3.connect(database) as con:
        con.execute(
            "DELETE FROM armored_speech_results WHERE source_id=? AND item_id=?",
            ("source1", "telegram-2"),
        )
        con.commit()

    with pytest.raises(Exception, match="No Speech decision"):
        run_complete_batch(
            root=tmp_path,
            manifest_path=manifest_path,
            speech_database=database,
            copy_only=True,
        )

    for item in manifest["items"]:
        output = Path(item["workspace"]) / f"{item['telegram_message_id']}_demo-{'1' if item['telegram_message_id'].endswith('1') else '2'}.mp4"
        assert not output.exists()


def test_runner_rejects_manifest_item_count_mismatch(tmp_path):
    manifest_path, database, manifest = _fixture(tmp_path, item_count=1)
    manifest["expected_item_count"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BatchManifestError, match="Lote incompleto"):
        run_complete_batch(
            root=tmp_path,
            manifest_path=manifest_path,
            speech_database=database,
            copy_only=True,
        )



def test_runner_preserves_an_existing_result_and_blocks_the_whole_batch(tmp_path):
    manifest_path, database, manifest = _fixture(tmp_path, item_count=2)
    first_item = manifest["items"][0]
    prior_output = Path(first_item["workspace"]) / "telegram-1_demo-1.mp4"
    prior_bytes = b"pre-existing valid output must be preserved"
    prior_output.write_bytes(prior_bytes)

    with pytest.raises(RuntimeError, match="preservar resultado anterior"):
        run_complete_batch(
            root=tmp_path,
            manifest_path=manifest_path,
            speech_database=database,
            copy_only=True,
        )

    assert prior_output.read_bytes() == prior_bytes
    second_item = manifest["items"][1]
    second_output = Path(second_item["workspace"]) / "telegram-2_demo-2.mp4"
    assert not second_output.exists()
