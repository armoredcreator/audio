from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ArmoredSpeech import prepare_audio_route_validation as route_validation


def _manifest(items: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "phase": "ARMORED_SPEECH",
        "phase_status": "COMPLETE",
        "approved": True,
        "batch_id": "original-batch",
        "expected_item_count": len(items),
        "items": items,
    }


def _item(number: int) -> dict:
    return {
        "source_id": "source-lab",
        "telegram_message_id": f"item-{number}",
        "content_id": f"source-lab_item-{number}",
        "state": "IA",
        "workspace": "storage/workspace",
        "original_path": f"storage/workspace/item-{number}.mp4",
        "original_sha256": f"{number:064x}",
    }


def test_route_manifest_selects_at_most_one_per_available_no_rvc_class(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "source.json"
    database = root / "speech.sqlite3"
    output = root / "storage" / "validation" / "routes.json"
    database.write_bytes(b"read-only test placeholder")
    items = [_item(n) for n in range(1, 8)]
    source.write_text(json.dumps(_manifest(items)), encoding="utf-8")
    original_manifest_bytes = source.read_bytes()
    original_database_bytes = database.read_bytes()

    classes = {
        "item-1": "MUSIC_ONLY",
        "item-2": "MUSIC_ONLY",
        "item-3": "NO_AUDIO",
        "item-4": "NOISE",
        "item-5": "NO_USABLE_NARRATION",
        "item-6": "FOREIGN_SPEECH",
        "item-7": "PT_BR_NARRATION_WITH_MUSIC",
    }
    monkeypatch.setattr(
        route_validation,
        "export_audio_directive",
        lambda _db, *, source_id, item_id, require_files: SimpleNamespace(
            audio_class=classes[item_id]
        ),
    )

    selected, missing, counts = route_validation.prepare_route_validation_manifest(
        root=root,
        source_manifest=source,
        speech_database=database,
        output_manifest=output,
        stamp="20300101T010101Z",
    )

    assert [item["route_validation_audio_class"] for item in selected] == [
        "MUSIC_ONLY",
        "NO_AUDIO",
        "NOISE",
        "NO_USABLE_NARRATION",
        "FOREIGN_SPEECH",
    ]
    assert [item["telegram_message_id"] for item in selected] == [
        "item-1", "item-3", "item-4", "item-5", "item-6",
    ]
    assert len({item["content_id"] for item in selected}) == 5
    assert all("routecheck_20300101T010101Z_" in item["content_id"] for item in selected)
    assert missing == []
    assert counts["MUSIC_ONLY"] == 2
    assert "PT_BR_NARRATION_WITH_MUSIC" not in [
        item["route_validation_audio_class"] for item in selected
    ]
    assert source.read_bytes() == original_manifest_bytes
    assert database.read_bytes() == original_database_bytes
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["expected_item_count"] == 5
    assert written["batch_id"] == "audio-route-audit-20300101T010101Z"
    assert len(written["items"]) == 5


def test_route_manifest_reports_missing_classes_and_fails_on_existing_output(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "source.json"
    database = root / "speech.sqlite3"
    output = root / "routes.json"
    database.write_bytes(b"read-only test placeholder")
    source.write_text(json.dumps(_manifest([_item(1)])), encoding="utf-8")
    monkeypatch.setattr(
        route_validation,
        "export_audio_directive",
        lambda *_args, **_kwargs: SimpleNamespace(audio_class="MUSIC_ONLY"),
    )

    selected, missing, _counts = route_validation.prepare_route_validation_manifest(
        root=root,
        source_manifest=source,
        speech_database=database,
        output_manifest=output,
        stamp="20300101T010102Z",
    )
    assert len(selected) == 1
    assert missing == [
        "NO_AUDIO",
        "NOISE",
        "NO_USABLE_NARRATION",
        "FOREIGN_SPEECH",
    ]

    with pytest.raises(FileExistsError, match="Destino já existe"):
        route_validation.prepare_route_validation_manifest(
            root=root,
            source_manifest=source,
            speech_database=database,
            output_manifest=output,
            stamp="20300101T010103Z",
        )


def test_route_manifest_refuses_output_outside_workspace(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.json"
    database = root / "speech.sqlite3"
    source.write_text(json.dumps(_manifest([_item(1)])), encoding="utf-8")
    database.write_bytes(b"readonly")

    with pytest.raises(ValueError, match="dentro de"):
        route_validation.prepare_route_validation_manifest(
            root=root,
            source_manifest=source,
            speech_database=database,
            output_manifest=tmp_path / "outside.json",
        )
