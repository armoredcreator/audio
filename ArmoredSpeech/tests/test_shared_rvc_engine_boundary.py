from __future__ import annotations

import os
from pathlib import Path

from ArmoredStudio.processing import rvc as legacy_rvc
from armored_core.audio import rvc as shared_rvc


def test_legacy_studio_entry_point_delegates_to_shared_rvc_engine():
    assert legacy_rvc.converter_voz is shared_rvc.converter_voz
    assert legacy_rvc.converter_interno is shared_rvc.converter_interno
    assert legacy_rvc.executar_no_rvc is shared_rvc.executar_no_rvc
    assert legacy_rvc.localizar_modelo is shared_rvc.localizar_modelo
    assert legacy_rvc.main is shared_rvc.main


def test_shared_engine_keeps_existing_root_venv_and_model_location():
    repo_root = Path(__file__).resolve().parents[2]
    expected_rvc_root = os.getenv("ARMORED_RVC_ROOT", "").strip()
    if expected_rvc_root:
        expected_rvc_root = str(Path(expected_rvc_root).expanduser().resolve())
    else:
        expected_rvc_root = str((repo_root / "ArmoredStudio" / "runtime" / "rvc").resolve())

    assert shared_rvc.REPO_ROOT == repo_root
    assert shared_rvc.SHARED_ENV_DIR == repo_root / ".venv"
    assert shared_rvc.RVC_ROOT == Path(expected_rvc_root)
    assert shared_rvc.MODELS_DIR == Path(expected_rvc_root) / "models"
    # Keep legacy diagnostic path constants stable even though implementation
    # now lives in the shared engine package.
    assert legacy_rvc.BASE_DIR == repo_root / "ArmoredStudio" / "processing"
    assert legacy_rvc.STUDIO_ROOT == repo_root / "ArmoredStudio"
    assert legacy_rvc.REPO_ROOT == repo_root
