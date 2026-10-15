# ArmoredStudio lab port status

**Target branch:** `test/armoredstudio-speech-integration-20261015`  
**Official source:** `armoredcreator/armoredcreator:main` (read-only).  
**Execution policy:** no real video rendered; synthetic tests only.

## Completed in the lab
- Static audit: [ARMOREDSTUDIO_BASE_AUDIT.md](./ARMOREDSTUDIO_BASE_AUDIT.md)
- Integration plan: [ARMOREDSTUDIO_SPEECH_INTEGRATION.md](./ARMOREDSTUDIO_SPEECH_INTEGRATION.md)
- Ported the official Studio Python modules, including the four modules that had previously been missing: `analysis/blackbar.py`, `analysis/banner_analyzer.py`, `analysis/export_executor.py`, and `processing/rvc.py`.
- Ported canonical `armored_core/models.py` and `armored_core/storage.py` required for the actual `Item` contract and storage paths. Studio's service boundary returns its own small `StudioResult` value so it does not import the whole Coordinator service stack.
- Added `ArmoredSpeech/studio_adapter.py`: validates contract version, `source_id + item_id`, workspace containment, readable WAV sidecar matching the original basename, current original SHA-256 versus Speech's before/after hashes, and conservative RVC eligibility.
- Wired an optional `speech_payload` through `ArmoredStudio/service.py` into `UnifiedStudio.process`. When provided, the Studio validates it, maps Speech's class into Studio routing without rerunning audio classification, and uses the approved Speech WAV as RVC input only for the eligible narration route.
- Added safer output behavior in `unified.py`: render to a unique temporary sibling, preserve the existing destination until validation, promote with `replace`, hash-check the original before/after, and place disposable WAV intermediates under `storage/studio_work/<source>/<item>` rather than beside the source.
- Added synthetic adapter and Studio handoff tests. The GitHub Actions workflow now compiles all lab modules and runs the Python test suite when Studio code changes.
- Added `ArmoredStudio/assets/fetch_official_assets.py`, which downloads assets by their official Git blob IDs and verifies exact Git blob SHA-1 before writing. It requires an explicit read-only `ARMORED_OFFICIAL_REPO_TOKEN`; it does not save the token.

## Still blocked — do not render real media
1. The official source repo is private. The CI runner for `armoredcreator/audio` cannot download the two binary assets anonymously. The asset downloader was adjusted to require an explicit read-only token, and its step was removed from CI. The exact assets are **not yet present** in the lab branch: `ArmoredStudio/assets/banner.png` and `ArmoredStudio/assets/efeitosonoro.wav`.
2. The new handoff is an optional parameter to the Studio boundary; the existing Coordinator does not automatically supply a Speech payload. End-to-end orchestration from the real Speech registry to Studio remains to be wired and tested. Do not set `ARMORED_STUDIO_REQUIRE_SPEECH_HANDOFF=1` in any live environment until that caller exists.
3. Runtime integration tests are synthetic and use the explicit copy-only test mode. They do not prove real FFmpeg, RVC model/index, visual detector accuracy, watermark removal, or final audio quality.
4. The branch contains MP4 files in its root from earlier lab history; they were not opened or processed. Their origin and authorization are not established, so they remain unused.
5. GitHub Actions run `38815934451` completed successfully for commit `7e482a285bc346c4e85c26442642762f9efd18ab`: `compileall` passed and `pytest` reported **46 passed**. This is synthetic/unit coverage only; it is not evidence of a real FFmpeg/RVC render or full Coordinator-to-Speech orchestration.

## Required next steps
1. Materialize the two exact official binary assets with the authorized read-only route and verify their Git blob IDs.
2. Wire the actual Speech registry/Coordinator caller to pass the correct READY payload for each item, respecting the real source/item IDs and sidecar paths.
3. Add a full adapter test around that caller; then validate FFmpeg/RVC dependencies in the lab.
4. Only after all automated tests pass, request explicit approval for a controlled synthetic render. No real video, Telegram, Shopee, Hub or publication in this phase.
