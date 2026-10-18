# Speech batch → Studio visual renderer (contract v5)

O runner recebe um manifesto que declara um lote Speech completo e aprovado. Não descobre lote parcial pelo SQLite operacional, não abre banco de produção e bloqueia destinos já existentes.

## Responsabilidades e fluxo

1. **Studio faz pré-análise visual read-only:** dimensões, barras, banner, marcas VEO e Gemini e plano visual. O detector de marca Gemini é local (OpenCV/template matching); ele não chama a API Google Gemini.
2. **Speech valida as decisões persistidas:** identidade, original/WAV, classe, idioma, fala/música e elegibilidade RVC.
3. **Speech prepara os artefatos de áudio:** quando elegível, executa RVC Melody; prepara faixa de voz ou silêncio, prepara uma faixa de efeito compartilhada e aplica a política de ganho uma única vez.
4. **Speech compõe a timeline e o grafo de áudio:** trims, fades, sincronização, intro sonora, mixagem, `audio_filter_graph`, `audio_output_label` e as opções de codificação declaradas pelo contrato.
5. **Handoff v5:** identidade, original e hash, caminhos/hashes/metadados das faixas `voice_track` e `effect_track`, grafo de áudio, timeline, plano visual assinado por hash e opções de renderização. Não é um AAC/M4A final pré-renderizado.
6. O runner valida o lote e todos os pacotes antes do primeiro render. Plano/timeline divergentes, caminho fora do laboratório, hash incorreto, rota inconsistente ou artefato ausente bloqueiam o processamento.
7. Studio valida o handoff, aplica um crop canônico na fonte e combina o grafo visual com o grafo de áudio do Speech em **uma única invocação FFmpeg para a renderização audiovisual final**.
8. O runner confere a existência do resultado, a identidade do item e o SHA-256 do original.

A regra de uma chamada FFmpeg vale para a renderização audiovisual final. Speech continua autorizado a usar FFmpeg para extração e preparação/normalização de áudio antes dela.

## Comandos

Na raiz do laboratório:

```powershell
.\.venv\Scripts\python.exe -m compileall -q ArmoredSpeech ArmoredStudio armored_core
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ArmoredSpeech.studio_batch_runner --root . --manifest '.\storage\validation\<manifesto-aprovado>\manifest.json' --speech-database '.\storage\database\<sqlite-isolado>.sqlite3'
```

O teste real não deve usar `--copy-only`. Escolha um manifesto já revisado e um banco de Speech experimental correspondente; não use `storage/database/armoredcreator.db`. Antes de executar, confira o HEAD, hashes dos originais, ativos necessários e inexistência do destino. Gere relatório/log novos por tentativa; não apague evidências nem sobrescreva resultados anteriores.

## E2E v5 confirmado em 17/10/2026

Item `source_teste` / mensagem `5` concluído em Windows:
- Runner: `STUDIO_SPEECH_BATCH=SUCCESS`, `MODE=REAL_STUDIO`, `ITEMS=1`, `ORIGINALS_INTACT=True`, exit code `0`.
- Classificação/rota: `PT_BR_NARRATION` → `RVC_MELODY`.
- Pacote criado e validado: `voice.wav`, `_shared/effect.wav` e `package.json`.
- Vídeo de saída: `storage/Videos GRUPO_FONTE_teste/5/5_finaldomeulinknovo.mp4`.
- Saída: H.264 1080×1920, 11,420 s; áudio AAC estéreo 44.100 Hz, 11,401 s.
- Hash do original antes/depois: `c489fcaae803f36407c82dde3b93ff68077001ef5607909081a2c6555bee15a1`; igual ao manifesto.
- O usuário confirmou visualmente o tratamento que removeu a marca d'água Gemini neste item.

Relatório: `storage/validation/speech-studio-v5-item5-20261017T185708Z.json`  
Log: `storage/validation/speech-studio-v5-item5-20261017T185708Z.log`

O resultado comprova o caminho PT-BR/RVC v5 para esse item e a preservação do original. A evidência ampliada do corpus e das rotas sem RVC está registrada na seção "Evidência atualizada — 18/10/2026" abaixo. Nenhum teste desta branch publica no Hub.

## Teste E2E do corpus completo (14 vídeos)

O smoke E2E de um único item não substitui a validação do corpus. O teste dos 14 começa por uma etapa **somente de inventário**; revise a lista antes de processar mídia.

**1. Inventariar e hashear os candidatos, sem análise de áudio, cópias de vídeo ou SQLite novo:**

```powershell
.\.venv\Scripts\python.exe -m ArmoredSpeech.corpus_e2e_runner --root . --video-root .\storage --expected-count 14 --list-only
```

A saída deve mostrar `CORPUS_DISCOVERED=14/14`, cada caminho e hash, e `INPUTS_JSON=...`. Abra a lista/relatório e confirme que são os 14 vídeos-fontes pretendidos, sem saídas renderizadas ou duplicatas. A descoberta não encontrar exatamente 14 é uma condição de bloqueio; não processe a lista errada.

**2. Somente depois de confirmar os 14 caminhos**, execute o lote real substituindo o placeholder pelo caminho exato impresso em `INPUTS_JSON`:

```powershell
.\.venv\Scripts\python.exe -m ArmoredSpeech.corpus_e2e_runner --root . --inputs-json ".\storage\validation\corpus14_inventory_<batch-id>\inputs.json" --expected-count 14 --render
```

Esse segundo comando:

- exige exatamente 14 caminhos MP4 existentes, únicos e dentro do laboratório;
- cria cópias byte-a-byte em um namespace exclusivo, um SQLite experimental novo e artefatos Speech derivados, sem modificar vídeos-fonte;
- classifica e prepara os 14 pelo ArmoredSpeech; só começa o render real se todos os 14 estiverem `READY`, todos os WAVs forem válidos, hashes dos originais estiverem íntegros e os ativos passarem no preflight;
- executa o runner real v5 para o lote completo. Os pacotes de áudio são validados antes do primeiro render; o Studio faz uma renderização audiovisual final por item;
- não limpa nem sobrescreve execuções anteriores, não usa `storage/database/armoredcreator.db` e não publica no Hub.

O comando de inventário cria `storage/validation/corpus14_inventory_<batch-id>/inputs.json` e `inventory-report.json`. O processamento cria `storage/validation/corpus14_<batch-id>/speech-report.json` e `studio-report.json`; os vídeos finais ficam em `storage/Videos GRUPO_FONTE_corpus14_<batch-id>/`. Se algum item falhar durante Speech, o render de Studio de todo o lote é bloqueado e o relatório identifica classe/status/erro; nenhum vídeo do lote começa a ser renderizado.

Critério de sucesso observável: `CORPUS_E2E=SUCCESS`, `STUDIO_SPEECH_BATCH=SUCCESS`, `ITEMS=14/14`, `ORIGINALS_INTACT=True` e `ORIGINAL_SOURCE_HASHES_UNCHANGED=True`. CI e testes unitários validam o programa, mas não substituem a execução real com os 14 arquivos locais.

## Evidência atualizada — 18/10/2026

- Corpus de 17 entradas: Speech classificou 17/17 como `READY`; o runner real do Studio concluiu 17/17, com `ORIGINALS_INTACT=True`, saídas não vazias e hashes de origem iguais ao inventário. O usuário assistiu aos 17 resultados e confirmou que estavam corretos.
- Rotas observadas: 9 `RVC_MELODY` e 8 `EFFECT_ONLY`. Os novos casos de música e fala estrangeira seguiram `EFFECT_ONLY`, sem RVC.
- Uma fixture sintética separada concluiu `NO_USABLE_NARRATION` → `EFFECT_ONLY`; a faixa voz/silêncio tinha RMS/pico zero, o efeito tinha sinal e a saída renderizada existia. Isso valida a rota técnica, mas não equivale a uma amostra real de fala ininteligível.
- Auditoria estática: zero referências executáveis legadas, zero políticas inesperadas no Studio e `STATIC_AUDIO_OWNERSHIP_AUDIT=PASS`. Localmente, `compileall` passou e `pytest` obteve 102 testes aprovados. Correções documentais e proteções Git recentes ainda precisam de CI verde e sincronização local.
- Assets binários RVC/efeito não são versionados. Neste checkout, as cópias locais do Studio foram removidas seletivamente após comparar SHA-256 com os assets do Speech e concluir os E2Es; um checkout novo deve provisionar os assets canônicos em ArmoredSpeech.

## Segurança e isolamento

- Sem escrita/leitura do banco operacional pelo runner, sem publicação e sem integração automática no repositório principal.
- O runtime do Studio não tem fallback de RVC nem de efeito sonoro legado. Os ativos antigos só são aceitos como origem opcional de uma cópia verificada; não são necessários para execução nem removidos pelo script. `storage/`, bancos locais, originais e evidências ficam fora do Git.
- Preservar originais, corpus, manifestos, relatórios, pacotes e vídeos de evidência.
- A PR #5 permanece em draft até a revisão final e os testes de rotas adicionais.

## Auditoria isolada das classes sem RVC

Antes de executar mídia, rode as auditorias locais em modo somente leitura. A primeira procura referências executáveis a conversão RVC, classificação/mixagem legada e inventaria os ativos; a segunda confere o ambiente Python compartilhado e seus imports:

```powershell
.\.venv\Scripts\python.exe .\scripts\audit_audio_ownership.py --root .
.\scripts\audit_shared_dependencies.ps1
```

Esta auditoria é somente de leitura. Qualquer limpeza de cópias locais é uma etapa separada e exige hashes SHA-256 iguais aos assets canônicos do Speech e E2E aprovados. Não remova os modelos/índices canônicos do Speech, o efeito sonoro do Speech, os originais ou os relatórios/dados em storage/.

O módulo `ArmoredSpeech.prepare_audio_route_validation` escolhe no máximo um exemplar já existente no manifesto para cada classe sem RVC: `MUSIC_ONLY`, `NO_AUDIO`, `NOISE`, `NO_USABLE_NARRATION` e `FOREIGN_SPEECH`. Ele lê as decisões através de SQLite em modo read-only; preserva o manifesto-fonte e o banco; e atribui IDs de saída novos para bloquear sobrescrita de resultados anteriores. `PT_BR_NARRATION_WITH_MUSIC` não é selecionada por essa ferramenta porque tem fluxo separado de separação vocal, já exercitado nos itens 004 e 010.

Use caminhos dentro da raiz isolada e um destino novo:

```powershell
$Root = (Get-Location).Path
$RunDir = Join-Path $Root 'storage\validation\corpus14_20261017T203906621705Z'
$SourceManifest = Join-Path $RunDir 'studio-manifest.json'
$Database = Join-Path $Root 'storage\database\armored_speech_corpus14_20261017T203906621705Z.sqlite3'
$Stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$TargetManifest = Join-Path $RunDir ("studio-manifest-no-rvc-" + $Stamp + ".json")

.\.venv\Scripts\python.exe -m ArmoredSpeech.prepare_audio_route_validation --root . --source-manifest "$SourceManifest" --speech-database "$Database" --output-manifest "$TargetManifest" --stamp $Stamp
```

Confira `ROUTE_VALIDATION_MANIFEST_CREATED=True`, o número de itens, as classes selecionadas e `SQLITE_MUTATED=False`. `MISSING_CLASSES` informa as classes para as quais esse manifesto não tem candidato `READY`; não se deve inventar nem forçar uma classe. Se o manifesto for criado e houver itens, execute somente o manifesto recém-criado:

```powershell
$Report = Join-Path $RunDir ("studio-report-no-rvc-" + $Stamp + ".json")
.\.venv\Scripts\python.exe -m ArmoredSpeech.studio_batch_runner --root . --manifest "$TargetManifest" --speech-database "$Database" --report "$Report"
```

A execução real de cada classe selecionada deve concluir com `STUDIO_SPEECH_BATCH=SUCCESS`, `ORIGINALS_INTACT=True` e código de saída 0. Ouça/inspecione os resultados e confirme `route=EFFECT_ONLY`, faixa de voz silenciosa e efeito esperado. No checkout Windows validado, as rotas aplicáveis foram executadas e as cópias antigas do Studio foram removidas seletivamente após verificação SHA-256. A fixture sintética `NO_USABLE_NARRATION` não substitui uma amostra real de fala ininteligível.

