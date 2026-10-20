from __future__ import annotations

import json
from pathlib import Path

from armored_core.database import Database
from armored_core.models import State
from armored_core.storage import Storage
from ArmoredSpeech.contracts import AudioClass, SpeechDecision
from scripts.run_lab_pipeline import LabPipelineError, load_fixture_manifest, run_lab_pipeline


class FakeVision:
    def identify(self, item):
        from armored_core.services import VisionResult
        return VisionResult(
            "Produto fixture",
            "https://affiliate.invalid/product",
            affiliate_urls=("https://affiliate.invalid/product",),
            ia_context={
                "productName": "Produto fixture",
                "shopId": "700",
                "itemId": "800",
            },
        )


class FakeIA:
    def generate_caption(self, _context):
        return "Produto demonstrativo 🧡 #casa"


class FakeSpeechProcessor:
    def analyze(self, _video_path):
        return SpeechDecision(
            audio_class=AudioClass.MUSIC_ONLY,
            language=None,
            speech_detected=False,
            music_detected=True,
            confidence=0.99,
            rationale="deterministic fixture decision",
        )

    def prepare_wav(self, _video_path, wav_path, _decision):
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(b"fixture-wav-sidecar")


def _manifest(root: Path, rows: list[dict]) -> Path:
    path = root / "fixture-manifest.json"
    payload = {
        "schema_version": 1,
        "phase": "ARMORED_AUDIO_LAB_INPUT",
        "batch_id": "offline-test",
        "sources": ["source1", "source2", "source3"],
        "items": rows,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _studio_runner(*, root, manifest_path, speech_database, report_path, copy_only):
    assert copy_only is False
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert all(item["state"] == "SPEECH" for item in manifest["items"])
    output = []
    storage = Storage(Path(root))
    for item in manifest["items"]:
        original = Path(item["original_path"])
        result = storage.result(
            item["content_id"],
            item.get("affiliate_url"),
            item.get("affiliate_name"),
            source_id=item["source_id"],
        )
        result.write_bytes(original.read_bytes() + b"-studio")
        output.append({
            "source_id": item["source_id"],
            "item_id": item["telegram_message_id"],
            "output_path": str(result.resolve()),
            "original_sha256_before": item["original_sha256"],
        })
    return {
        "status": "SUCCESS",
        "originals_intact": True,
        "processed": output,
        "report_path": str(report_path),
    }


def test_manifest_requires_three_sources_and_existing_in_root_videos(tmp_path):
    video = tmp_path / "one.mp4"
    video.write_bytes(b"fixture-video")
    good = _manifest(tmp_path, [{
        "source_id": "source1", "telegram_message_id": "1",
        "source_path": "one.mp4", "original_url": None,
    }])
    payload, rows = load_fixture_manifest(tmp_path, good)
    assert payload["sources"] == ["source1", "source2", "source3"]
    assert rows[0]["source_sha256"]

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "schema_version": 1, "phase": "ARMORED_AUDIO_LAB_INPUT",
        "sources": ["source1", "source2"], "items": [],
    }), encoding="utf-8")
    import pytest
    with pytest.raises(LabPipelineError, match="três source labels"):
        load_fixture_manifest(tmp_path, bad)


def test_full_fixture_pipeline_reaches_studio_and_keeps_hub_disabled(tmp_path, monkeypatch):
    first_video = tmp_path / "video-1.mp4"
    second_video = tmp_path / "video-2.mp4"
    first_video.write_bytes(b"immutable-video-one")
    second_video.write_bytes(b"immutable-video-two")
    before = {first_video: first_video.read_bytes(), second_video: second_video.read_bytes()}
    manifest = _manifest(tmp_path, [
        {
            "source_id": "source1", "telegram_message_id": "101",
            "source_path": "video-1.mp4", "original_url": "https://shopee.co/first-different-link",
        },
        {
            "source_id": "source2", "telegram_message_id": "202",
            "source_path": "video-2.mp4", "original_url": "https://shopee.co/second-different-link",
        },
    ])
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")

    report = run_lab_pipeline(
        tmp_path, manifest,
        ia_provider="ollama",
        require_live_preflight=False,
        vision_factory=FakeVision,
        ia_factory=FakeIA,
        speech_processor=FakeSpeechProcessor(),
        studio_runner=_studio_runner,
    )

    assert report["status"] == "SUCCESS"
    assert report["publication_disabled"] is True
    assert report["hub_loaded"] is False
    assert report["stage_order"] == ["sync", "vision", "stock", "ia", "speech", "studio"]
    assert len(report["outputs"]) == 1
    assert report["counts_after_vision"]["duplicates_blocked"] == 1
    assert all(path.read_bytes() == content for path, content in before.items())

    db = Database(Path(report["database"]))
    rows = db.conn.execute("SELECT state, affiliate_url, duplicate_of_content_id FROM items ORDER BY created_at, content_id").fetchall()
    states = [row["state"] for row in rows]
    assert states.count(State.STUDIO_COMPLETE.value) == 1
    assert states.count(State.DUPLICATE.value) == 1
    duplicate = next(row for row in rows if row["state"] == State.DUPLICATE.value)
    assert duplicate["affiliate_url"] is None
    assert duplicate["duplicate_of_content_id"]
    db.close()

    output_path = Path(report["outputs"][0]["result_path"])
    assert output_path.is_file()
    assert output_path.read_bytes().endswith(b"-studio")
    assert report["fixture_originals_intact"] is True
