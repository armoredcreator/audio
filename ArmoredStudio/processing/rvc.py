"""Compatibility facade for the legacy Studio RVC entry point.

The low-level engine lives in armored_core.audio.rvc. ArmoredSpeech will own
routing and invocation; this facade preserves the existing v1 imports/CLI while
the contract migration is staged and tested.
"""
from __future__ import annotations

from pathlib import Path

from armored_core.audio.rvc import (
    DEFAULT_OUTPUT,
    DEFAULT_VOICE,
    MODELS_DIR,
    RVC_ROOT,
    converter_interno,
    converter_voz,
    executar_no_rvc,
    localizar_modelo,
    main,
    resolver_rvc_python,
    validar_arquivo,
)

# Preserve path constants that older diagnostics may inspect.
BASE_DIR = Path(__file__).resolve().parent
STUDIO_ROOT = BASE_DIR.parent
REPO_ROOT = STUDIO_ROOT.parent
SHARED_ENV_DIR = REPO_ROOT / ".venv"

__all__ = [
    "BASE_DIR",
    "STUDIO_ROOT",
    "REPO_ROOT",
    "SHARED_ENV_DIR",
    "RVC_ROOT",
    "MODELS_DIR",
    "DEFAULT_VOICE",
    "DEFAULT_OUTPUT",
    "resolver_rvc_python",
    "validar_arquivo",
    "localizar_modelo",
    "converter_interno",
    "executar_no_rvc",
    "converter_voz",
    "main",
]

if __name__ == "__main__":
    main()
