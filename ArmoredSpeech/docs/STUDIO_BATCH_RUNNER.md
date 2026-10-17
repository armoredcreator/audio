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
.\\.venv\\Scripts\\python.exe -m compileall -q ArmoredSpeech ArmoredStudio armored_core
.\\.venv\\Scripts\\python.exe -m pytest -q
.\\.venv\\Scripts\\python.exe -m ArmoredSpeech.studio_batch_runner --root . --manifest '.\\storage\\validation\\<manifesto-aprovado>\\manifest.json' --speech-database '.\\storage\\database\\<sqlite-isolado>.sqlite3'
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

O resultado comprova o caminho PT-BR/RVC v5 para esse item e preservação do original. Ainda é preciso testar cada classe sem RVC, revisar todas as rotas e terminar a auditoria de dependências antes de remover os ativos legados. Nenhum teste desta branch publica no Hub.

## Segurança e isolamento

- Sem escrita/leitura do banco operacional pelo runner, sem publicação e sem integração automática no repositório principal.
- Não remover os assets antigos antes de testar rotas não-RVC e revisar referências.
- Preservar originais, corpus, manifestos, relatórios, pacotes e vídeos de evidência.
- A PR #5 permanece em draft até a revisão final e os testes de rotas adicionais.
