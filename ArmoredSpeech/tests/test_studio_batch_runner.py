from __future__ import annotations

import hashlib
import json
import sqlite3
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import ArmoredSpeech.studio_batch_runner as runner_module
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


def _plan_digest(plan: dict) -> str:
    return hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


def _write_v2_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(bytes(44100 * 2 * 2))


def _fake_v2_package(root: Path, item, *, corrupt_effect_hash: bool = False) -> dict:
    from ArmoredSpeech.timeline_mixer import build_audio_filter_graph

    original = Path(item.original_path)
    output_dir = root / "storage" / "validation" / "fake-packages" / str(item.telegram_message_id)
    effect = output_dir / "effect.wav"
    _write_v2_wav(effect)
    digest = _sha(original)
    effect_hash = _sha(effect)
    if corrupt_effect_hash:
        effect_hash = "0" * 64
    plan = {"video_start": 0.0}
    return {
        "contract_version": 3,
        "status": "READY",
        "source_id": str(item.source_id),
        "item_id": str(item.telegram_message_id),
        "content_id": str(item.content_id),
        "original_path": str(original),
        "original_sha256_before": digest,
        "original_sha256_after": digest,
        "audio_class": "MUSIC_ONLY",
        "language": "unknown",
        "speech_detected": False,
        "music_detected": True,
        "confidence": 0.99,
        "route": "EFFECT_ONLY",
        "rvc_eligible": False,
        "rvc_model": None,
        "rvc_model_sha256": None,
        "rvc_index_sha256": None,
        "voice_track": None,
        "effect_track": {
            "path": str(effect),
            "sha256": effect_hash,
            "source_sha256": "e" * 64,
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
            "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        },
        "audio_timeline": build_audio_filter_graph(
            visual_plan=plan, intro=True, intro_position="final"
        ),
        "prepared_video_duration_seconds": 1.0,
    }

def test_real_runner_validates_entire_v3_batch_before_first_studio_call(tmp_path, monkeypatch):
    manifest_path, database, _ = _fixture(tmp_path, item_count=2)
    report_path = tmp_path / "storage" / "validation" / "v2-report.json"
    events = []

    def fake_prepare(items, **kwargs):
        packages = [(item, _fake_v2_package(tmp_path, item)) for item in items]
        events.append(("all-packages-prepared", len(packages)))
        return packages

    class FakeStudio:
        def analyze_visual(self, item):
            return SimpleNamespace(plan={"video_start": 0.0})

        def process(self, item, *, speech_payload=None, visual_analysis=None):
            events.append(("studio", str(item.telegram_message_id)))
            destination = runner_module.Storage(tmp_path).result(
                item.content_id,
                item.affiliate_url,
                item.affiliate_name,
                source_id=item.source_id,
            )
            destination.write_bytes(b"synthetic rendered video")
            return SimpleNamespace(result_path=destination)

    monkeypatch.setattr(runner_module, "prepare_audio_packages_for_items", fake_prepare)
    monkeypatch.setattr(runner_module, "build_studio", lambda _root: FakeStudio())

    report = run_complete_batch(
        root=tmp_path,
        manifest_path=manifest_path,
        speech_database=database,
        report_path=report_path,
        copy_only=False,
    )

    assert report["status"] == "SUCCESS"
    assert report["audio_contract_version"] == 3
    assert len(report["audio_packages"]) == 2
    assert len(report["processed"]) == 2
    assert events == [
        ("all-packages-prepared", 2),
        ("studio", "telegram-1"),
        ("studio", "telegram-2"),
    ]


def test_real_runner_blocks_all_studio_calls_if_any_v3_package_is_invalid(tmp_path, monkeypatch):
    manifest_path, database, _ = _fixture(tmp_path, item_count=2)
    report_path = tmp_path / "storage" / "validation" / "v2-report-failed.json"
    events = []

    def fake_prepare(items, **kwargs):
        packages = [
            (item, _fake_v2_package(tmp_path, item, corrupt_effect_hash=(position == 2)))
            for position, item in enumerate(items, start=1)
        ]
        events.append(("all-packages-prepared", len(packages)))
        return packages

    class FakeStudio:
        def analyze_visual(self, item):
            return SimpleNamespace(plan={"audio_start": 0.0})

        def process(self, item, *, speech_payload=None, visual_analysis=None):
            events.append(("studio", str(item.telegram_message_id)))
            raise AssertionError("No Studio call is allowed when any v3 package is invalid")

    monkeypatch.setattr(runner_module, "prepare_audio_packages_for_items", fake_prepare)
    monkeypatch.setattr(runner_module, "build_studio", lambda _root: FakeStudio())

    with pytest.raises(BatchManifestError, match="Pacote final de áudio do Speech inválido"):
        run_complete_batch(
            root=tmp_path,
            manifest_path=manifest_path,
            speech_database=database,
            report_path=report_path,
            copy_only=False,
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "FAILED"
    assert report["processed"] == []
    assert events == [("all-packages-prepared", 2)]
    for item in json.loads(manifest_path.read_text(encoding="utf-8"))["items"]:
        output = Path(runner_module.Storage(tmp_path).result(
            item["content_id"],
            item.get("affiliate_url"),
            item.get("affiliate_name"),
            source_id=item["source_id"],
        ))
        assert not output.exists()
