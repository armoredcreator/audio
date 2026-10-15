# ArmoredStudio lab port status

Target: `test/armoredstudio-speech-integration-20261015`. Official source `armoredcreator/armoredcreator:main` remains read-only. No tests were run and no video was rendered.

## Completed in the lab
- Static audit: [ARMOREDSTUDIO_BASE_AUDIT.md](./ARMOREDSTUDIO_BASE_AUDIT.md)
- Integration plan: [ARMOREDSTUDIO_SPEECH_INTEGRATION.md](./ARMOREDSTUDIO_SPEECH_INTEGRATION.md)
- Added `ArmoredSpeech/studio_adapter.py` with fail-closed validation for contract version, item identity, workspace containment, WAV readability, SHA-256 of the current original, and conservative RVC eligibility.
- Added `ArmoredSpeech/tests/test_studio_adapter.py` using synthetic disposable files.
- Ported to lab: `ArmoredStudio/service.py`, `unified.py`, `analysis/audio_intelligence.py`, `analysis/video.py`, `analysis/banner.py`, `analysis/veo_detector.py`, `analysis/gemini_detector.py`, `analysis/export_planner.py`, `analysis/export_plan_validator.py`, `analysis/logger.py`, `processing/finalizer.py`, `processing/tool_paths.py`, and `ArmoredStudio/__init__.py`.

## Blockers — Studio must not be run yet
1. Not ported: `ArmoredStudio/analysis/blackbar.py`, `analysis/banner_analyzer.py`, `analysis/export_executor.py`, `processing/rvc.py`. The GitHub write operation blocked attempts to port these files.
2. Official assets not ported/verified: `ArmoredStudio/assets/banner.png` and `ArmoredStudio/assets/efeitosonoro.wav`.
3. The new Speech handoff validator is not yet wired into Studio; the existing Studio still classifies audio independently. Integration is not complete.
4. A code patch for atomic output promotion and per-item temporary WAV paths was blocked by the GitHub write operation. The known risks remain in the ported `unified.py`: it deletes a previous result early and writes temporary WAVs beside the source.
5. Confirm the lab's required `armored_core` models/services and runtime dependencies before import/test.
6. Tests have not been executed. File commits do not prove imports or tests pass.

## Required next steps
1. Copy the four missing Python files and exact two assets from official `main`; verify asset hashes.
2. Wire the validated Speech handoff so Studio uses Speech's decision and WAV rather than reclassifying audio.
3. Fix atomic result replacement, isolated per-item temporary/cache paths, and original SHA-256 checks.
4. Run tests and report exact results.
5. Only after the suite passes, request explicit approval for a synthetic render. No real video, Telegram, Shopee, Hub or publication in this phase.
