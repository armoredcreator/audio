from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("cv2", reason="Studio visual contract requires Studio dependencies")

import ArmoredStudio.unified as unified_module
from ArmoredStudio.processing import visual_finalizer
from ArmoredStudio.speech_handoff import sha256_file, validate_audio_package
from ArmoredStudio.unified import UnifiedStudio


@dataclass
class FakeItem:
    source_id: str
    content_id: str
    telegram_message_id: str
    original_path: Path
    working_path: Path | None = None
    affiliate_url: str | None = None
    affiliate_name: str | None = None


class FakeStorage:
    def __init__(self, output: Path):
        self.output = output

    def result(self, *args, **kwargs):
        return self.output


def _write_m4a(path: Path, *, bytes_content: bytes = b"fake m4a bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypM4A " + bytes_content * 16)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan_digest(plan: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _payload(root: Path, original: Path, final_audio: Path, plan: dict, *, intro: bool = True) -> dict:
    original_hash = sha256_file(original)
    main_duration = 3.0
    intro_duration = 2.0 if intro else 0.0
    audio_duration = main_duration + intro_duration
    track = {
        "path": str(final_audio),
        "sha256": _sha(final_audio),
        "format": "aac-m4a",
        "codec": "aac",
        "sample_rate": 44100,
        "channels": 2,
        "duration_seconds": audio_duration,
        "composition_owner": "ArmoredSpeech",
        "size_bytes": final_audio.stat().st_size,
    }
    timeline = {
        "visual_plan_sha256": _plan_digest(plan),
        "audio_start_seconds": 0.0,
        "video_start_seconds": float(plan.get("video_start", 0.0)),
        "main_duration_seconds": main_duration,
        "intro_enabled": intro,
        "intro_position": "final" if intro else None,
        "intro_duration_seconds": intro_duration,
        "final_audio_duration_seconds": audio_duration,
        "composition_owner": "ArmoredSpeech",
        "filter_graph_sha256": "a" * 64,
    }
    return {
        "contract_version": 4,
        "status": "READY",
        "source_id": "source-1",
        "item_id": "telegram-1",
        "content_id": "source1_telegram1",
        "original_path": str(original),
        "original_sha256_before": original_hash,
        "original_sha256_after": original_hash,
        "audio_class": "PT_BR_NARRATION",
        "route": "RVC_MELODY",
        "final_audio_track": track,
        "audio_timeline": timeline,
        "prepared_video_duration_seconds": main_duration,
    }


def test_studio_muxes_only_the_final_speech_track_and_never_processes_audio(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    final_audio = tmp_path / "speech_package" / "final_audio.m4a"
    _write_m4a(final_audio)
    banner = tmp_path / "ArmoredStudio" / "assets" / "banner.png"
    banner.parent.mkdir(parents=True, exist_ok=True)
    banner.write_bytes(b"synthetic banner")
    output = tmp_path / "final.mp4"
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    plan = {"video_start": 0.0}
    visual = SimpleNamespace(plan=plan, video={"sucesso": True, "duracao": 3.0})
    payload = _payload(tmp_path, original, final_audio, plan)
    captured = {}

    monkeypatch.delenv("ARMORED_STUDIO_FORCE_COPY", raising=False)
    monkeypatch.setenv("ARMORED_STUDIO_BANNER", str(banner))
    monkeypatch.setenv("ARMORED_FFMPEG", "ffmpeg")
    monkeypatch.setattr(unified_module.shutil, "which", lambda _: "ffmpeg")

    def fake_render(video, audio_track, banner_path, output_path, **kwargs):
        captured.update({
            "video": Path(video),
            "audio_track": Path(audio_track),
            "banner": Path(banner_path),
            "output": Path(output_path),
            "intro": kwargs.get("intro"),
            "position": kwargs.get("position"),
            "plan": kwargs.get("plan"),
        })
        Path(output_path).write_bytes(b"synthetic rendered output")

    monkeypatch.setattr(visual_finalizer, "render_video_with_speech_track", fake_render)
    studio = UnifiedStudio(
        tmp_path,
        FakeStorage(output),
        analysis=SimpleNamespace(
            analyze=lambda source: (_ for _ in ()).throw(
                AssertionError("the already validated visual plan should be reused")
            )
        ),
    )

    result, details = studio.process(item, speech_payload=payload, visual_analysis=visual)

    assert result == output
    assert output.read_bytes() == b"synthetic rendered output"
    assert captured["audio_track"] == final_audio
    assert captured["intro"] is True
    assert captured["position"] == "final"
    assert captured["plan"] == plan
    assert details is visual
    assert _sha(original) == payload["original_sha256_before"]


def test_studio_rejects_final_audio_hash_mismatch_before_render(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    final_audio = tmp_path / "speech_package" / "final_audio.m4a"
    _write_m4a(final_audio)
    output = tmp_path / "final.mp4"
    plan = {"video_start": 0.0}
    payload = _payload(tmp_path, original, final_audio, plan)
    payload["final_audio_track"]["sha256"] = "0" * 64
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    def forbidden_render(*args, **kwargs):
        raise AssertionError("Invalid final audio must be rejected before rendering")

    monkeypatch.setattr(visual_finalizer, "render_video_with_speech_track", forbidden_render)
    with pytest.raises(RuntimeError, match="handoff inválido"):
        studio.process(item, speech_payload=payload, visual_analysis=SimpleNamespace(plan=plan))
    assert not output.exists()


def test_studio_rejects_package_composed_for_different_visual_plan(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic immutable video bytes")
    final_audio = tmp_path / "speech_package" / "final_audio.m4a"
    _write_m4a(final_audio)
    output = tmp_path / "final.mp4"
    package_plan = {"video_start": 0.0}
    payload = _payload(tmp_path, original, final_audio, package_plan)
    different_plan = {"video_start": 0.25}
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    def forbidden_render(*args, **kwargs):
        raise AssertionError("Mismatched visual/audio timelines must not render")

    monkeypatch.setattr(visual_finalizer, "render_video_with_speech_track", forbidden_render)
    with pytest.raises(RuntimeError, match="plano visual difere"):
        studio.process(
            item,
            speech_payload=payload,
            visual_analysis=SimpleNamespace(plan=different_plan),
        )
    assert not output.exists()


def test_studio_handoff_exposes_only_one_final_audio_file(tmp_path):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"immutable original")
    final_audio = tmp_path / "speech_package" / "final_audio.m4a"
    _write_m4a(final_audio)
    plan = {"video_start": 0.0}
    payload = _payload(tmp_path, original, final_audio, plan)

    validated = validate_audio_package(
        payload,
        workspace_root=tmp_path,
        expected_source_id="source-1",
        expected_item_id="telegram-1",
        expected_original_path=original,
    )

    assert validated.final_audio_track_path == final_audio
    assert validated.final_audio_sha256 == _sha(final_audio)
    assert validated.visual_plan_sha256 == _plan_digest(plan)
    assert not hasattr(validated, "voice_track_path")
    assert not hasattr(validated, "effect_track_path")
    assert "voice_track" not in payload
    assert "effect_track" not in payload
    assert "audio_filter_graph" not in payload
