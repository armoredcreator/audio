# ArmoredStudio lab port status

**Target branch:** `test/armoredstudio-speech-integration-20261015`  
**Official source:** `armoredcreator/armoredcreator:main` (read-only).  
**Execution policy:** no real video rendered yet; synthetic tests only.

## Ordem oficial do pipeline

**ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub.**

Esta branch valida isoladamente a integração Speech → Studio antes de qualquer transferência para o ArmoredCreator. Hub/publicação ficam fora do escopo dos testes atuais.

## Corpus local preparado

O corpus informado pelo usuário está em `C:\Users\Administrador\Downloads\ArmoredAudioLab\storage`. Contém vídeos para Speech e casos visuais de Studio com VEO 1, VEO 2, Gemini e barras pretas de diferentes tamanhos. Os arquivos são locais, não fazem parte do commit e não devem ser enviados ao Git. O teste deve usar workspace/cópias isoladas, manter os originais intactos e gerar um relatório por caso.

Os assets oficiais do Studio e os arquivos/modelo/índice do ambiente RVC serão transferidos pelo usuário separadamente. Até estarem disponíveis e validados no laboratório, o teste real de ponta a ponta permanece bloqueado.

## Shared Python environment status
- ArmoredSpeech setup now targets the root `.venv` rather than installing a second identical package set under `ArmoredSpeech/runtime/venv`.
- The root `.venv` with NumPy 1.23.5 passed 45 unit tests (1 skipped) and the five real Speech reference cases; the original hashes remained unchanged.
- The RVC environment `ArmoredStudio/runtime/rvc/env311` remains untouched until its unique dependencies and custom Fairseq build can be migrated safely and real Melody conversion passes under the root interpreter.

## Completed in the lab
- Static audit: [ARMOREDSTUDIO_BASE_AUDIT.md](./ARMOREDSTUDIO_BASE_AUDIT.md)
- Integration plan: [ARMOREDSTUDIO_SPEECH_INTEGRATION.md](./ARMOREDSTUDIO_SPEECH_INTEGRATION.md)
- Ported the official Studio Python modules, including the four modules that had previously been missing: `analysis/blackbar.py`, `analysis/banner_analyzer.py`, `analysis/export_executor.py`, and `processing/rvc.py`.
- Ported canonical `armored_core/models.py` and `armored_core/storage.py` required for the actual `Item` contract and storage paths. Studio's service boundary returns its own small `StudioResult` value so it does not import the whole Coordinator service stack.
- Added `ArmoredSpeech/studio_adapter.py`: validates contract version, `source_id + item_id`, workspace containment, readable WAV sidecar matching the original basename, current original SHA-256 versus Speech's before/after hashes, and conservative RVC eligibility.
- Wired an optional `speech_payload` through `ArmoredStudio/service.py` into `UnifiedStudio.process`. When provided, the Studio validates it, maps Speech's class into Studio routing without rerunning audio classification, and uses the approved Speech WAV as RVC input only for the eligible narration route.
- Added safer output behavior in `unified.py`: render to a unique temporary sibling, preserve the existing destination until validation, promote with `replace`, hash-check the original before/after, and place disposable WAV intermediates under `storage/studio_work/<source>/<item>` rather than beside the source.
- Added synthetic adapter and Studio handoff tests. Added `process_items_with_speech(...)` as a lab-only phase boundary: it preflights every supplied item from the Speech-owned SQLite registry and validates READY/file availability for the entire batch before invoking Studio for the first item. Tests prove a blocked item prevents all Studio calls. The GitHub Actions workflow compiles all lab modules and runs the Python test suite when Studio code changes.
- Added `ArmoredStudio/assets/fetch_official_assets.py`, which downloads assets by their official Git blob IDs and verifies exact Git blob SHA-1 before writing. It requires an explicit read-only `ARMORED_OFFICIAL_REPO_TOKEN`; it does not save the token.

## Still blocked — do not declare end-to-end validation complete
1. The exact official assets are not yet present in the lab: `ArmoredStudio/assets/banner.png` and `ArmoredStudio/assets/efeitosonoro.wav`. The user plans to transfer the assets separately; verify their identity/content before use. The asset downloader is available as an alternative and requires an explicit read-only `ARMORED_OFFICIAL_REPO_TOKEN`.
2. The new handoff is an optional parameter to the Studio boundary. `process_items_with_speech(...)` now provides a safe batch-call primitive, but there is not yet a runnable lab orchestrator that proves the whole historical Speech batch is complete and supplies the matching real `Item` objects automatically. End-to-end orchestration from the completed Speech phase to Studio remains to be wired and tested. Do not set `ARMORED_STUDIO_REQUIRE_SPEECH_HANDOFF=1` in any live environment until that caller exists.
3. Runtime integration tests are synthetic and use the explicit copy-only test mode. They do not prove real FFmpeg, RVC model/index, visual detector accuracy, watermark removal, or final audio quality.
4. GitHub Actions run `38816123362` completed successfully for commit `248cafed7377b4885b9959b5d9f52f7ba7e222bf`: `compileall` passed and `pytest` reported **49 passed**. Coverage includes synthetic registry-to-Studio handoff, preservation of an existing output on invalid handoff, explicit RVC eligibility gating, and a test that supplied Speech decisions bypass Studio's second audio classifier. This is synthetic/unit coverage only; it is not evidence of a real FFmpeg/RVC render or production Coordinator orchestration.

## Required next steps
1. The user transfers the official Studio assets and RVC runtime/model/index into the isolated lab; confirm expected paths and hashes without committing private/binary assets.
2. Add the explicit lab caller/orchestrator that proves the Speech phase is complete, loads the corresponding lab `Item` records, and invokes `process_items_with_speech(...)` once for the approved batch; keep this separate from production Coordinator and databases.
3. Run a read-only inventory/preflight on the local corpus, validate FFmpeg/RVC dependencies and detector accuracy, then execute only the approved isolated test plan.
4. Record pass/fail evidence per video and preserve original hashes. No Telegram, Shopee, Hub or publication in this phase.
5. Only after all tests pass, request explicit approval before any controlled real render or promotion. The production/reference repositories remain untouched.
