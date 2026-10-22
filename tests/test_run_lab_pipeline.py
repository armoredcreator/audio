from __future__ import annotations

import json
import os
from pathlib import Path

from armored_core.database import Database
from armored_core.models import State
from armored_core.storage import Storage
from ArmoredSpeech.contracts import AudioClass, SpeechDecision
from scripts.run_lab_pipeline import (
    LabPipelineError,
    _load_project_environment,
    load_fixture_manifest,
    run_lab_pipeline,
)


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


def _studio_runner(*, root, manifest_path, speech_database, report_path, copy_only, storage_root=None):
    assert copy_only is False
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert all(item["state"] == "SPEECH" for item in manifest["items"])
    output = []
    storage = Storage(Path(root), storage_root=Path(storage_root) if storage_root is not None else None)
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


def test_project_environment_loader_ignores_production_runtime_and_telegram_settings(tmp_path, monkeypatch):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "project.env").write_text(
        "\n".join([
            "SHOPEE_APP_ID=lab-app-id",
            "SHOPEE_SECRET_KEY=lab-secret",
            "GEMINI_API_KEY=lab-gemini-key",
            "ARMORED_IA_ENABLED=1",
            "ARMORED_IA_MAX_ATTEMPTS=3",
            "ARMORED_REAL_TELEGRAM=1",
            "ARMORED_ROOT=C:/ArmoredCreator-final",
            "ARMORED_CREATOR_BOT_TOKEN=production-token",
            "ARMORED_HUB_1_CHAT_ID=-100123",
            "TELEGRAM_API_ID=12345",
            "ARMORED_STUDIO_ALLOW_COPY=1",
            "BOT_API_PORT=8081",
        ]) + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join([
            "SHOPEE_APP_ID=should-not-replace-project",
            "ARMORED_IA_ENABLED=0",
            "ARMORED_IA_MODEL=lab-model",
            "ARMORED_REAL_TELEGRAM=1",
        ]) + "\n",
        encoding="utf-8",
    )

    for key in (
        "SHOPEE_APP_ID", "SHOPEE_SECRET_KEY", "GEMINI_API_KEY",
        "ARMORED_IA_ENABLED", "ARMORED_IA_MAX_ATTEMPTS", "ARMORED_IA_MODEL",
        "ARMORED_REAL_TELEGRAM", "ARMORED_ROOT", "ARMORED_CREATOR_BOT_TOKEN",
        "ARMORED_HUB_1_CHAT_ID", "TELEGRAM_API_ID", "ARMORED_STUDIO_ALLOW_COPY",
        "BOT_API_PORT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SHOPEE_APP_ID", "inherited-shell-value")

    _load_project_environment(tmp_path)

    assert os.environ["SHOPEE_APP_ID"] == "lab-app-id"
    assert os.environ["SHOPEE_SECRET_KEY"] == "lab-secret"
    assert os.environ["GEMINI_API_KEY"] == "lab-gemini-key"
    assert os.environ["ARMORED_IA_ENABLED"] == "1"
    assert os.environ["ARMORED_IA_MAX_ATTEMPTS"] == "3"
    assert os.environ["ARMORED_IA_MODEL"] == "lab-model"
    for key in (
        "ARMORED_REAL_TELEGRAM", "ARMORED_ROOT", "ARMORED_CREATOR_BOT_TOKEN",
        "ARMORED_HUB_1_CHAT_ID", "TELEGRAM_API_ID", "ARMORED_STUDIO_ALLOW_COPY",
        "BOT_API_PORT",
    ):
        assert key not in os.environ


def test_repeated_pipeline_runs_keep_storage_and_outputs_isolated(tmp_path, monkeypatch):
    video = tmp_path / "same-video.mp4"
    video.write_bytes(b"immutable-video-for-two-independent-runs")
    original_bytes = video.read_bytes()
    manifest = _manifest(tmp_path, [{
        "source_id": "source1",
        "telegram_message_id": "901",
        "source_path": "same-video.mp4",
        "original_url": "https://shopee.co/repeated-run-isolation",
    }])
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")

    reports = []
    for _ in range(2):
        reports.append(run_lab_pipeline(
            tmp_path,
            manifest,
            ia_provider="ollama",
            require_live_preflight=False,
            vision_factory=FakeVision,
            ia_factory=FakeIA,
            speech_processor=FakeSpeechProcessor(),
            studio_runner=_studio_runner,
        ))

    original_paths = []
    result_paths = []
    run_roots = []
    for report in reports:
        run_root = Path(report["run_directory"]).resolve()
        workspace_root = (run_root / "workspace").resolve()
        run_roots.append(run_root)
        assert Path(report["storage_root"]).resolve() == workspace_root
        assert report["storage_isolated"] is True
        db = Database(Path(report["database"]))
        try:
            row = db.conn.execute(
                "SELECT original_path, result_path FROM items WHERE telegram_message_id='901'"
            ).fetchone()
            original_path = Path(row["original_path"]).resolve()
            result_path = Path(row["result_path"]).resolve()
            assert original_path.is_file() and result_path.is_file()
            assert original_path.is_relative_to(workspace_root)
            assert result_path.is_relative_to(workspace_root)
            original_paths.append(original_path)
            result_paths.append(result_path)
        finally:
            db.close()

    assert run_roots[0] != run_roots[1]
    assert original_paths[0] != original_paths[1]
    assert result_paths[0] != result_paths[1]
    assert video.read_bytes() == original_bytes
    assert not list((tmp_path / "storage").glob("Videos GRUPO_FONTE_*"))


def test_storage_root_rejects_path_outside_project_root(tmp_path):
    from armored_core.storage import Storage
    import pytest

    with pytest.raises(ValueError, match="storage_root must stay inside project root"):
        Storage(tmp_path, storage_root=tmp_path.parent / "outside-lab")
