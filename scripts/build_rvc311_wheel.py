"""Build the explicit Armored Python 3.11 compatibility fork of rvc-python.

Input is the original, hash-verified PyPI source archive. No installed package
metadata is edited. The source license and inference code remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

UPSTREAM_SHA256 = "486da646e4813b0d7d2ad8ac31eff728e7ecb8adfc05c545f2768b11542f6857"
VERSION = "0.1.5+armored311.1"
REPLACEMENTS = {
    'requires = ["hatchling","hatch-vcs"]': 'requires = ["hatchling==1.27.0","hatch-vcs==0.4.0"]',
    'version = "0.1.5"': f'version = "{VERSION}"',
    'requires-python = ">=3.9"': 'requires-python = "==3.11.*"',
    '"fairseq==0.12.2"': '"fairseq==0.12.3.1"',
    '"omegaconf==2.0.6"': '"omegaconf==2.3.0",\n  "hydra-core==1.3.2"',
}


def prepare_source(archive: Path, destination: Path) -> Path:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != UPSTREAM_SHA256:
        raise ValueError("Unexpected rvc-python source archive digest")
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as source:
        # The verified upstream archive contains ordinary source files only.
        # Reject links/devices explicitly, and reject traversal before writing.
        for member in source.getmembers():
            resolved = (destination / member.name).resolve()
            if not resolved.is_relative_to(destination.resolve()):
                raise ValueError("Source archive path escapes its destination")
            if not (member.isfile() or member.isdir()):
                raise ValueError("Source archive contains a non-regular entry")
        source.extractall(destination, filter="data")
    root = destination / "rvc_python-0.1.5"
    project = root / "pyproject.toml"
    original = project.read_text(encoding="utf-8")
    patched = original
    for old, new in REPLACEMENTS.items():
        if patched.count(old) != 1:
            raise ValueError(f"Upstream build recipe changed: {old}")
        patched = patched.replace(old, new)
    project.write_text(patched, encoding="utf-8")
    (root / "ARMORED_BUILD.json").write_text(json.dumps({
        "upstream_name": "rvc-python", "upstream_version": "0.1.5",
        "upstream_source_sha256": digest, "fork_version": VERSION,
        "patch_scope": "build recipe only; all upstream requirements retained",
        "changes": REPLACEMENTS,
    }, indent=2), encoding="utf-8")
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 11, 9):
        raise SystemExit("Build requires Python 3.11.9")
    root = prepare_source(args.source, args.work)
    args.output.mkdir(parents=True, exist_ok=True)
    if list(args.output.glob(f"rvc_python-{VERSION}-*.whl")):
        raise FileExistsError("Preserving an existing fork wheel; choose a new output directory")
    env = dict(os.environ, SOURCE_DATE_EPOCH="1722470400")
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                    "--wheel-dir", str(args.output), str(root)], check=True, env=env)


if __name__ == "__main__":
    main()
