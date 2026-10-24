from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    root = Path(os.getenv("ARMORED_ROOT") or Path(__file__).resolve().parent).resolve()
    os.environ["ARMORED_ROOT"] = str(root)
    credentials = root / "credentials" / "project.env"
    if credentials.is_file():
        load_dotenv(credentials, override=True)
    project_config = root / ".env"
    if project_config.is_file():
        load_dotenv(project_config, override=False)

    provider = (os.getenv("ARMORED_IA_PROVIDER") or "ollama").strip().casefold()
    provider_configured = provider == "ollama" or (
        provider == "gemini" and bool(os.getenv("GEMINI_API_KEY"))
    )
    checks = {
        "python_311": sys.version_info[:2] == (3, 11),
        "speech_effect": (root / "ArmoredSpeech" / "assets" / "efeitosonoro.wav").is_file(),
        "audio_model_cache": any((root / "storage" / "models" / "audio").rglob("*")),
        "rvc_model": any((root / "ArmoredSpeech" / "runtime" / "rvc" / "models").rglob("*.pth")),
        "ai_provider_configured": provider_configured,
    }
    for module in ("faster_whisper", "torch", "transformers", "rvc_python", "telegram"):
        checks[f"import_{module}"] = importlib.util.find_spec(module) is not None
    failed = [name for name, ok in checks.items() if not ok]
    print("AUDIO_PREFLIGHT=PASS" if not failed else "AUDIO_PREFLIGHT=FAIL")
    print("CHECKS=" + ",".join(f"{name}:{'ok' if ok else 'failed'}" for name, ok in checks.items()))
    if failed:
        print("No model download or inference was attempted.")
        return 1
    print("No model download or inference was attempted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
