from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from ArmoredSpeech import audio_package
import ArmoredSpeech.studio_integration as integration


@dataclass
class Item:
    source_id: str
    telegram_message_id: str
    content_id: str
    original_path: Path


def test_batch_preflights_all_directives_and_inputs_before_first_package_build(tmp_path, monkeypatch):
    effect = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    effect.parent.mkdir(parents=True, exist_ok=True)
    effect.write_bytes(b"synthetic effect placeholder" * 8)
    items = []
    for i in (1, 2, 3):
        original = tmp_path / f"original-{i}.mp4"
        original.write_bytes(f"original-{i}".encode())
        items.append(Item("source1", f"telegram-{i}", f"source1_telegram-{i}", original))

    events = []

    def fake_export(database, *, source_id, item_id, require_files):
        events.append(("export", item_id))
        payload = {"source_id": source_id, "item_id": item_id, "contract_version": 1}
        return SimpleNamespace(to_payload=lambda: payload)

    def fake_preflight(payload, item, **kwargs):
        events.append(("preflight", item.telegram_message_id))
        return {"preflight_for": item.telegram_message_id}

    def fake_prepare_effect(source, **kwargs):
        events.append(("shared-effect", "once"))
        return {
            "path": str(tmp_path / "effect-prepared.wav"),
            "sha256": "a" * 64,
            "source_sha256": "b" * 64,
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
            "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
            "_prepared_and_verified": True,
        }

    def fake_prepare(payload, item, **kwargs):
        events.append(("build", item.telegram_message_id))
        assert kwargs["preflight"]["preflight_for"] == item.telegram_message_id
        assert kwargs["prepared_effect_track"]["_prepared_and_verified"] is True
        return {"contract_version": 2, "item_id": item.telegram_message_id}

    monkeypatch.setattr(integration, "export_audio_directive", fake_export)
    monkeypatch.setattr(audio_package, "preflight_audio_package", fake_preflight)
    monkeypatch.setattr(audio_package, "prepare_effect_track", fake_prepare_effect)
    monkeypatch.setattr(audio_package, "prepare_audio_package", fake_prepare)
    monkeypatch.setattr(audio_package, "sha256_file", lambda path: "b" * 64)

    prepared = integration.prepare_audio_packages_for_items(
        items,
        speech_database=tmp_path / "speech.sqlite3",
        workspace_root=tmp_path,
        package_root=tmp_path / "storage" / "validation" / "packages",
        effect_source_path=effect,
    )

    assert len(prepared) == 3
    assert [event[0] for event in events] == [
        "export", "export", "export",
        "preflight", "preflight", "preflight",
        "shared-effect",
        "build", "build", "build",
    ]
    assert [item.telegram_message_id for item, _ in prepared] == [
        "telegram-1", "telegram-2", "telegram-3",
    ]
