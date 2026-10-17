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
- The RVC unique dependencies and custom Fairseq build were added to the root `.venv` with pinned versions. Root-environment imports, Speech tests, five real-audio checks, and a real Melody conversion passed on 2026-10-16. `ArmoredStudio/processing/rvc.py` now resolves only the root environment; the old `env311` venv is no longer an operational dependency and may be deleted locally after pulling this branch.

## Completed in the lab
- Static audit: [ARMOREDSTUDIO_BASE_AUDIT.md](./ARMOREDSTUDIO_BASE_AUDIT.md)
- Integration plan: [ARMOREDSTUDIO_SPEECH_INTEGRATION.md](./ARMOREDSTUDIO_SPEECH_INTEGRATION.md)
- Ported the official Studio Python modules, including the four modules that had previously been missing: `analysis/blackbar.py`, `analysis/banner_analyzer.py`, `analysis/export_executor.py`, and `processing/rvc.py`.
- Ported canonical `armored_core/models.py` and `armored_core/storage.py` required for the actual `Item` contract and storage paths. Studio's service boundary returns its own small `StudioResult` value so it does not import the whole Coordinator service stack.
- Added `ArmoredSpeech/studio_adapter.py`: validates contract version, `source_id + item_id`, workspace containment, readable WAV sidecar matching the original basename, current original SHA-256 versus Speech's before/after hashes, and conservative RVC eligibility.
- Wired an optional `speech_payload` through `ArmoredStudio/service.py` into `UnifiedStudio.process`. When provided, the Studio validates it, maps Speech's class into Studio routing without rerunning audio classification, and uses the approved Speech WAV as RVC input only for the eligible narration route.
- Added safer output behavior in `unified.py`: render to a unique temporary sibling, preserve the existing destination until validation, promote with `replace`, hash-check the original before/after, and place disposable WAV intermediates under `storage/studio_work/<source>/<item>` rather than beside the source.
- Added synthetic adapter and Studio handoff tests. Added `process_items_with_speech(...)` as a lab-only phase boundary: it preflights every supplied item from the Speech-owned SQLite registry and validates READY/file availability for the entire batch before invoking Studio for the first item. Added `ArmoredSpeech/studio_batch_runner.py`, a runnable lab-only caller that requires an explicitly approved `ARMORED_SPEECH / COMPLETE` manifest, validates the declared item count, unique source/message IDs, canonical `Item` records, root/workspace containment and original SHA-256, then calls the public Studio service through the batch preflight. It writes a JSON report and rechecks original hashes. A guarded `--copy-only` mode validates the actual service/handoff contract without claiming to render a video. Usage and manifest contract: `ArmoredSpeech/docs/STUDIO_BATCH_RUNNER.md`. GitHub Actions run `38987110736` passou: `compileall` concluído e `pytest` com **67 passed**. Isso valida a suíte sintética, não renderização de vídeo real.
- Added `ArmoredStudio/assets/fetch_official_assets.py`, which downloads assets by their official Git blob IDs and verifies exact Git blob SHA-1 before writing. It requires an explicit read-only `ARMORED_OFFICIAL_REPO_TOKEN`; it does not save the token.

## Still blocked — do not declare end-to-end validation complete
1. The exact official assets are not yet present in the lab: `ArmoredStudio/assets/banner.png` and `ArmoredStudio/assets/efeitosonoro.wav`. The user plans to transfer the assets separately; verify their identity/content before use. The asset downloader is available as an alternative and requires an explicit read-only `ARMORED_OFFICIAL_REPO_TOKEN`.
2. The runnable lab caller now exists, but it deliberately does not infer historical completeness from SQLite. The operator must prepare/review a manifest representing the entire approved post-Speech batch and mark it `phase_status=COMPLETE` only after Speech finishes. Synthetic copy-only integration passed in CI. Real rendering still requires local verified official assets, FFmpeg, model/index and an approved isolated subset. Do not set `ARMORED_STUDIO_REQUIRE_SPEECH_HANDOFF=1` in any live environment.
3. Runtime integration tests are synthetic and use the explicit copy-only test mode. They do not prove real FFmpeg, RVC model/index, visual detector accuracy, watermark removal, or final audio quality.
4. GitHub Actions run `38816123362` completed successfully for commit `248cafed7377b4885b9959b5d9f52f7ba7e222bf`: `compileall` passed and `pytest` reported **49 passed**. Coverage includes synthetic registry-to-Studio handoff, preservation of an existing output on invalid handoff, explicit RVC eligibility gating, and a test that supplied Speech decisions bypass Studio's second audio classifier. This is synthetic/unit coverage only; it is not evidence of a real FFmpeg/RVC render or production Coordinator orchestration.

## Required next steps
1. The user transfers the official Studio assets and RVC runtime/model/index into the isolated lab; confirm expected paths and hashes without committing private/binary assets.
2. Pull/checkout this feature branch in the Windows lab and run `.\.venv\Scripts\python.exe -m pytest -q` and `.\.venv\Scripts\python.exe -m ArmoredSpeech.studio_batch_runner --help`; confirm the local environment passes before creating a reviewed manifest and experimental Speech SQLite.
3. Run a read-only inventory/preflight on the local corpus, verify the exact official Studio assets, FFmpeg and RVC model/index without uploading private or binary resources.
4. Only then run a small approved real-render subset in an isolated workspace; record pass/fail per case and preserve original hashes. No Telegram, Shopee, Hub or publication in this phase.
5. Only after these checks and user review, consider promotion. The production/reference repositories remain untouched.
