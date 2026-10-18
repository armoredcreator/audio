from __future__ import annotations

"""Read-only audit of ArmoredSpeech/ArmoredStudio audio ownership and local assets."""

import argparse
import re
from pathlib import Path
from typing import Any


LEGACY_EXECUTION_PATTERNS = {
    "RVC engine invocation": re.compile(
        r"\bRVCInference\b|\bconverter_voz\s*\(|\bexecutar_no_rvc\s*\(",
        re.IGNORECASE,
    ),
    "legacy RVC module import": re.compile(
        r"from\s+(?:\.\s*)?(?:ArmoredStudio\.)?processing\.rvc\b",
        re.IGNORECASE,
    ),
    "legacy audio classifier import": re.compile(
        r"(?:from\s+[^\n]*audio_intelligence\s+import|import\s+[^\n]*audio_intelligence)",
        re.IGNORECASE,
    ),
    "legacy Studio runtime/model/effect path": re.compile(
        r"ArmoredStudio[\\/]+(?:runtime[\\/]+rvc|analysis[\\/]+audio_intelligence|"
        r"processing[\\/]+rvc|assets[\\/]+efeitosonoro\.wav)",
        re.IGNORECASE,
    ),
}
AUDIO_POLICY_PATTERN = re.compile(
    r"\bamix\s*=|\bloudnorm\b|\bafade\s*=|\badelay\s*=|\bapad\s*=|"
    r"\bsilencedetect\b|\baudio-separator\b|\bRVCInference\b|\bconverter_voz\s*\(",
    re.IGNORECASE,
)
STUDIO_AUDIO_VALIDATION_ALLOWLIST = {
    Path("speech_handoff.py"),
    Path("processing") / "visual_finalizer.py",
}
LEGACY_MODULES = (
    Path("ArmoredStudio") / "analysis" / "audio_intelligence.py",
    Path("ArmoredStudio") / "processing" / "rvc.py",
    Path("ArmoredStudio") / "processing" / "finalizer.py",
)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _active_studio_sources(studio_root: Path) -> list[Path]:
    sources = []
    for path in studio_root.rglob("*.py"):
        relative_parts = {part.lower() for part in path.relative_to(studio_root).parts}
        if relative_parts.intersection({"tests", "test", "__pycache__", ".venv", "venv"}):
            continue
        sources.append(path)
    return sorted(sources)


def run_audit(root: Path) -> dict[str, Any]:
    """Audit active source ownership and inventory assets without modifying files."""
    root = Path(root).expanduser().resolve(strict=True)
    studio_root = root / "ArmoredStudio"
    speech_root = root / "ArmoredSpeech"
    if not studio_root.is_dir() or not speech_root.is_dir():
        raise ValueError("Execute a auditoria na raiz do AudioLab contendo ArmoredSpeech e ArmoredStudio.")

    sources = _active_studio_sources(studio_root)
    legacy_hits: list[dict[str, Any]] = []
    policy_hits: list[dict[str, Any]] = []
    for path in sources:
        relative_to_studio = path.relative_to(studio_root)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        for line_number, line in enumerate(lines, start=1):
            for label, pattern in LEGACY_EXECUTION_PATTERNS.items():
                if pattern.search(line):
                    legacy_hits.append({
                        "file": _relative(path, root),
                        "line": line_number,
                        "rule": label,
                        "text": line.strip(),
                    })
            if relative_to_studio not in STUDIO_AUDIO_VALIDATION_ALLOWLIST and AUDIO_POLICY_PATTERN.search(line):
                policy_hits.append({
                    "file": _relative(path, root),
                    "line": line_number,
                    "text": line.strip(),
                })

    legacy_module_status = {
        path.as_posix(): (root / path).is_file()
        for path in LEGACY_MODULES
    }
    legacy_rvc_root = studio_root / "runtime" / "rvc"
    legacy_rvc_files = (
        [p for p in legacy_rvc_root.rglob("*") if p.is_file()]
        if legacy_rvc_root.is_dir() else []
    )
    speech_models_root = speech_root / "runtime" / "rvc" / "models"
    speech_model_files = (
        [p for p in speech_models_root.rglob("*") if p.is_file() and p.suffix.lower() in {".pth", ".index"}]
        if speech_models_root.is_dir() else []
    )
    speech_effect = speech_root / "assets" / "efeitosonoro.wav"
    legacy_effect = studio_root / "assets" / "efeitosonoro.wav"
    banner = studio_root / "assets" / "banner.png"
    return {
        "root": str(root),
        "active_studio_python_files": len(sources),
        "legacy_execution_hits": legacy_hits,
        "unexpected_audio_policy_hits": policy_hits,
        "legacy_modules": legacy_module_status,
        "legacy_studio_rvc_files": len(legacy_rvc_files),
        "legacy_studio_rvc_bytes": sum(p.stat().st_size for p in legacy_rvc_files),
        "speech_rvc_pth_files": sum(p.suffix.lower() == ".pth" for p in speech_model_files),
        "speech_rvc_index_files": sum(p.suffix.lower() == ".index" for p in speech_model_files),
        "speech_effect_present": speech_effect.is_file(),
        "speech_effect_bytes": speech_effect.stat().st_size if speech_effect.is_file() else 0,
        "legacy_studio_effect_present": legacy_effect.is_file(),
        "visual_studio_banner_present": banner.is_file(),
        "static_ownership_pass": not legacy_hits and not policy_hits,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        result = run_audit(args.root)
    except (OSError, ValueError) as exc:
        print(f"AUDIO_OWNERSHIP_AUDIT_ERROR={exc}")
        return 2

    print("AUDIO_OWNERSHIP_AUDIT=READ_ONLY")
    print(f"REPOSITORY_ROOT={result['root']}")
    print(f"STUDIO_PYTHON_FILES={result['active_studio_python_files']}")
    print(f"LEGACY_AUDIO_EXECUTION_REFERENCES={len(result['legacy_execution_hits'])}")
    for hit in result["legacy_execution_hits"]:
        print(f"LEGACY_REFERENCE={hit['file']}:{hit['line']} [{hit['rule']}]: {hit['text']}")
    print(f"UNEXPECTED_STUDIO_AUDIO_POLICY_REFERENCES={len(result['unexpected_audio_policy_hits'])}")
    for hit in result["unexpected_audio_policy_hits"]:
        print(f"AUDIO_POLICY_REFERENCE={hit['file']}:{hit['line']}: {hit['text']}")
    for module, present in result["legacy_modules"].items():
        print(f"LEGACY_MODULE_{module}={'PRESENT' if present else 'ABSENT'}")
    print(
        f"LEGACY_STUDIO_RVC_ASSET_FILES={result['legacy_studio_rvc_files']}; "
        f"BYTES={result['legacy_studio_rvc_bytes']}"
    )
    print(
        f"SPEECH_RVC_PTH_FILES={result['speech_rvc_pth_files']}; "
        f"SPEECH_RVC_INDEX_FILES={result['speech_rvc_index_files']}"
    )
    print(f"SPEECH_EFFECT_ASSET_PRESENT={result['speech_effect_present']}")
    print(f"LEGACY_STUDIO_EFFECT_ASSET_PRESENT={result['legacy_studio_effect_present']}")
    print(f"VISUAL_STUDIO_BANNER_PRESENT={result['visual_studio_banner_present']}")
    if not result["static_ownership_pass"]:
        print("STATIC_AUDIO_OWNERSHIP_AUDIT=FAIL")
        return 3
    print("STATIC_AUDIO_OWNERSHIP_AUDIT=PASS")
    print("Read-only audit only. No files were changed or deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
