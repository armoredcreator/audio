# Unified Python 3.11.9 runtime

The target is a single repository-root `.venv` with Python **3.11.9**, including
RVC, analysis, separation, Studio and Hub. Python 3.10 is not a fallback.
Existing operational environments and Melody assets must be preserved.

## Cause and explicit compatibility package

Published `rvc-python 0.1.5` still requires `fairseq==0.12.2` and
`omegaconf==2.0.6`. The working Windows Python 3.11 environment instead contains
the prebuilt Python 3.11 Fairseq fork `0.12.3.1`, Hydra `1.3.2` and OmegaConf
`2.3.0`. Six unconditional upstream requirements were missing: FastAPI,
ffmpeg-python, Loguru, Pydantic, python-multipart and Uvicorn. They are retained
and installed; they are not relabeled as optional extras.

`scripts/build_rvc311_wheel.py` builds **rvc-python 0.1.5+armored311.1** from the
official PyPI source archive. This is an explicitly versioned compatibility
fork, not a claim that upstream 0.1.5 supports these dependencies. Only its
build recipe changes: Python 3.11, fixed build-backend versions, Fairseq
0.12.3.1, OmegaConf 2.3.0 and Hydra 1.3.2. All other upstream requirements,
inference files and the MIT license remain intact.

The generated wheel was built twice with the same SHA-256. All **43 package
files** match the original upstream wheel byte for byte. No installed
distribution metadata was edited to silence `pip check`.

| Required local artifact | SHA-256 |
| --- | --- |
| Original `rvc_python-0.1.5.tar.gz` | `486da646e4813b0d7d2ad8ac31eff728e7ecb8adfc05c545f2768b11542f6857` |
| `rvc_python-0.1.5+armored311.1-py3-none-any.whl` | `88be78121a4bf64e74e8dad6c28c61fa0dacaad329199f5287bea458b8831765` |
| `fairseq-0.12.3.1-cp311-cp311-win_amd64.whl` | `515f6b9e9cfd2d14c16eb6c25f60df2c48335a01b6a51da78f1776b6317a528f` |

The Fairseq wheel is a preserved local MIT-licensed artifact. Its metadata
identifies it as a Python 3.11 fork; it is not the official PyPI Fairseq
0.12.2 release. Its original build commit has not been established. Keep the
verified wheel with the installation artifacts. Do not substitute a similarly
named distribution or an unverified binary. Rebuilding Fairseq from source
with recorded compiler and source revision remains a separate provenance gate.

## Controlled installation

Obtain the source archive from the [PyPI release](https://pypi.org/project/rvc-python/0.1.5/#files)
and verify the digest above. Build in a new directory:

```powershell
py -3.11 scripts/build_rvc311_wheel.py --source artifacts/rvc_python-0.1.5.tar.gz --work build/rvc311-source --output artifacts/rvc311
```

Place the preserved, verified Fairseq wheel beside the generated RVC wheel.
The installer requires a **new** isolated target and refuses to change an
existing `.venv`:

The target must be new or empty. The installer exports the reviewed Git HEAD
into it and installs that snapshot in editable mode. Code, the shared `.venv`,
and subsequently provisioned Speech assets therefore have the same project
root; RVC cannot accidentally resolve another checkout's environment.

```powershell
powershell -File scripts/install_unified_runtime.ps1 -TargetRoot work/runtime311-clean -Wheelhouse artifacts/rvc311
```

`requirements-runtime311.lock.txt` records the complete corrected package set,
including Torch/torchaudio/torchvision CPU 2.4.1/2.4.1/0.19.1, NumPy 1.23.5,
OpenCV headless 4.10.0.84 and python-telegram-bot 22.8. The script verifies the
two nonstandard wheels, installs the exact set through pip's resolver, checks
dependencies, and imports the inference and publishing libraries. It does
not download models or send messages. Build and install logs are evidence;
the lock file alone is not proof of a successful clean installation.

The corrected package set and optional `requirements-separation.txt` both pin
audio-separator **0.28.2** from the exercised runtime. The prior >=0.30 range
contradicted this retained version; upgrading separation remains a reviewed
change requiring separation regression, not an implicit setup side effect.

When the Windows launcher does not register Python 3.11, pass
`-PythonExecutable` with the installed Python 3.11.9 executable. The installer
validates that executable before creating the environment. `-PipCache` can
reuse a previously downloaded isolated cache; it does not reuse installed
packages or modify the old environment.

## Actual validation and limits

An isolated copy of the existing Python 3.11.9 package set was corrected with
the explicit fork and the missing dependencies. Existing versions were held
by constraints. `pip check` then reported **No broken requirements found**.
A network-disabled two-second Melody inference on that corrected environment
produced **79,200 finite samples at 40 kHz (1.98 seconds)**; Melody model and
index hashes were unchanged. This is separate from the preserved 14-video
Speech-to-Studio corpus and does not rerun it.

A second environment was installed from scratch by the installer and full
lock (not copied site-packages). Installation, `pip check`, and runtime imports
passed there as well. The two reviewed local wheels remain required artifacts;
the original Fairseq build commit is still a provenance limitation.

The RVC library's CPU configuration rewrites its own configuration files.
Its HuBERT/RMVPE cache also lives under `site-packages/rvc_python/base_model`.
Use writable isolated package files and verified copies of existing model
caches for tests. A fresh installation needs the same verified cache assets
before a network-disabled inference test; the installer does not silently
download them. Melody ownership remains `ArmoredSpeech/runtime/rvc`.

Dependency consistency and finite audio do not establish subjective voice
quality, every optional separation model, or every GPU configuration. Keep
those claims distinct from the tested CPU conversion path.
