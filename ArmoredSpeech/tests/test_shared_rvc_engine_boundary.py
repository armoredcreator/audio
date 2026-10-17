from __future__ import annotations

import os
from pathlib import Path

from ArmoredSpeech import rvc_engine


def test_rvc_engine_is_owned_by_armoredspeech():
    repo_root = Path(__file__).resolve().parents[2]
    speech_root = repo_root / "ArmoredSpeech"

    assert rvc_engine.REPO_ROOT == repo_root
    assert rvc_engine.SPEECH_ROOT == speech_root
    assert Path(rvc_engine.__file__).resolve().parent == speech_root
    assert rvc_engine.SHARED_ENV_DIR == repo_root / ".venv"
    assert rvc_engine.BASE_DIR == speech_root


def test_rvc_models_default_to_speech_runtime_not_studio():
    repo_root = Path(__file__).resolve().parents[2]
    configured = os.getenv("ARMORED_RVC_ROOT", "").strip()
    expected_root = (
        Path(configured).expanduser().resolve()
        if configured
        else (repo_root / "ArmoredSpeech" / "runtime" / "rvc").resolve()
    )

    assert rvc_engine.RVC_ROOT == expected_root
    assert rvc_engine.MODELS_DIR == expected_root / "models"
    assert Path(rvc_engine.MODELS_DIR).parts[-3:] == ("runtime", "rvc", "models") or configured


def test_studio_has_no_importable_rvc_entry_point():
    from ArmoredStudio import unified

    assert not hasattr(unified, "converter_voz")
    assert not hasattr(unified, "rvc")
