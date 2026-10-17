from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("cv2", reason="Studio visual contract requires Studio dependencies")

from ArmoredStudio.unified import AnalysisEngine, UnifiedStudio


class FakeStorage:
    def __init__(self, result_path: Path):
        self.result_path = result_path

    def result(self, *args, **kwargs):
        return self.result_path


@dataclass
class FakeItem:
    source_id: str
    content_id: str
    telegram_message_id: str
    original_path: Path
    working_path: Path | None = None
    affiliate_url: str | None = None
    affiliate_name: str | None = None


def test_studio_explicit_copy_only_mode_does_not_require_audio_tools(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic-disposable-video")
    output = tmp_path / "output.mp4"
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))

    monkeypatch.setenv("ARMORED_STUDIO_FORCE_COPY", "1")
    monkeypatch.setenv("ARMORED_STUDIO_ALLOW_COPY", "1")
    result, details = studio.process(item)

    assert result == output
    assert details["mode"] == "test-copy"
    assert output.read_bytes() == original.read_bytes()


def test_studio_rejects_legacy_audio_handoffs_for_real_render(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic-disposable-video")
    output = tmp_path / "output.mp4"
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))
    monkeypatch.delenv("ARMORED_STUDIO_FORCE_COPY", raising=False)

    with pytest.raises(RuntimeError, match="contract v4"):
        studio.process(item, speech_payload={
            "contract_version": 1,
            "source_id": "source-1",
            "item_id": "telegram-1",
        })

    assert not output.exists()
    assert original.read_bytes() == b"synthetic-disposable-video"


def test_analysis_engine_is_visual_only_and_returns_export_plan(tmp_path, monkeypatch):
    import ArmoredStudio.unified as unified

    monkeypatch.setattr(unified, "obter_informacoes_video", lambda source: {"sucesso": True, "duracao": 1.0})
    monkeypatch.setattr(unified, "analisar_bordas_video", lambda source: {})
    monkeypatch.setattr(unified, "analisar_banner", lambda source: {"banner_detectado": False})
    monkeypatch.setattr(unified, "detectar_veo", lambda source: {})
    monkeypatch.setattr(unified, "detectar_gemini", lambda source: {})
    monkeypatch.setattr(unified, "criar_plano_exportacao", lambda *args: {"crop_final": None, "video_start": 0.0, "acoes": []})
    monkeypatch.setattr(unified, "validar_plano_exportacao", lambda plan: {"valido": True, "erros": []})

    result = AnalysisEngine().analyze(tmp_path / "synthetic.mp4")

    assert result.plan == {"crop_final": None, "video_start": 0.0, "acoes": []}
    assert result.validation["valido"] is True
    assert not hasattr(result, "audio")
    assert not hasattr(AnalysisEngine(), "audio_intelligence")


def test_studio_never_overwrites_existing_result_even_with_valid_speech_boundary(tmp_path, monkeypatch):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic-disposable-video")
    output = tmp_path / "output.mp4"
    output.write_bytes(b"previous-good-output")
    item = FakeItem("source-1", "content-1", "telegram-1", original)
    studio = UnifiedStudio(tmp_path, FakeStorage(output))
    monkeypatch.delenv("ARMORED_STUDIO_FORCE_COPY", raising=False)

    with pytest.raises(FileExistsError, match="never overwrites"):
        studio.process(item, speech_payload={"contract_version": 3})

    assert output.read_bytes() == b"previous-good-output"
    assert original.read_bytes() == b"synthetic-disposable-video"
