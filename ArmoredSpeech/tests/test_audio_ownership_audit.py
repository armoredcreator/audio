from __future__ import annotations

from pathlib import Path

from scripts.audit_audio_ownership import run_audit


def _root(tmp_path: Path) -> Path:
    (tmp_path / "ArmoredStudio" / "processing").mkdir(parents=True)
    (tmp_path / "ArmoredStudio" / "analysis").mkdir(parents=True)
    (tmp_path / "ArmoredSpeech" / "runtime" / "rvc" / "models" / "melody").mkdir(
        parents=True
    )
    (tmp_path / "ArmoredSpeech" / "assets").mkdir(parents=True)
    (tmp_path / "ArmoredStudio" / "assets").mkdir(parents=True)
    return tmp_path


def test_audio_ownership_audit_allows_contract_validation_and_final_graph_render(tmp_path):
    root = _root(tmp_path)
    (root / "ArmoredStudio" / "unified.py").write_text(
        "from .speech_handoff import validate_audio_package\n"
        "def render(audio_filter_graph):\n    return audio_filter_graph\n",
        encoding="utf-8",
    )
    (root / "ArmoredStudio" / "speech_handoff.py").write_text(
        'gain_policy = "loudnorm-I-12-LRA7-TP-1-then-volume-0.8"\n',
        encoding="utf-8",
    )
    (root / "ArmoredStudio" / "processing" / "visual_finalizer.py").write_text(
        'graph = visual_graph + ";" + audio_filter_graph\n'
        'command = ["ffmpeg", "-filter_complex", graph]\n',
        encoding="utf-8",
    )
    model = root / "ArmoredSpeech" / "runtime" / "rvc" / "models" / "melody" / "melody.pth"
    model.write_bytes(b"model fixture")
    (root / "ArmoredSpeech" / "assets" / "efeitosonoro.wav").write_bytes(b"effect fixture")
    (root / "ArmoredStudio" / "assets" / "banner.png").write_bytes(b"banner fixture")

    result = run_audit(root)

    assert result["static_ownership_pass"] is True
    assert result["legacy_execution_hits"] == []
    assert result["unexpected_audio_policy_hits"] == []
    assert result["speech_rvc_pth_files"] == 1
    assert result["speech_rvc_index_files"] == 0
    assert result["speech_effect_present"] is True
    assert result["visual_studio_banner_present"] is True
    assert result["legacy_studio_rvc_files"] == 0


def test_audio_ownership_audit_flags_active_legacy_rvc_and_audio_mix(tmp_path):
    root = _root(tmp_path)
    (root / "ArmoredStudio" / "unified.py").write_text(
        "from .processing.rvc import converter_voz\n"
        'graph = "amix=inputs=2"\n',
        encoding="utf-8",
    )

    result = run_audit(root)

    assert result["static_ownership_pass"] is False
    assert any(hit["rule"] == "legacy RVC module import" for hit in result["legacy_execution_hits"])
    assert len(result["unexpected_audio_policy_hits"]) == 1


def test_audio_ownership_audit_inventories_but_does_not_reject_preserved_legacy_assets(tmp_path):
    root = _root(tmp_path)
    (root / "ArmoredStudio" / "unified.py").write_text("class UnifiedStudio: pass\n", encoding="utf-8")
    old_model = root / "ArmoredStudio" / "runtime" / "rvc" / "models" / "melody.pth"
    old_model.parent.mkdir(parents=True)
    old_model.write_bytes(b"legacy model")

    result = run_audit(root)

    assert result["static_ownership_pass"] is True
    assert result["legacy_studio_rvc_files"] == 1
    assert result["legacy_studio_rvc_bytes"] == len(b"legacy model")
    assert result["legacy_modules"]["ArmoredStudio/processing/rvc.py"] is False
