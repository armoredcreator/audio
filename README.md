# ArmoredCreator Audio Lab

Laboratório isolado para validar ArmoredSpeech e ArmoredStudio antes de qualquer integração no repositório operacional.

## Documento canônico

A arquitetura e o fluxo ponta a ponta estão em [docs/ARMORED_SPEECH_STUDIO_FLOW.md](docs/ARMORED_SPEECH_STUDIO_FLOW.md). A decisão de arquitetura está em [docs/ADR_SPEECH_AUDIO_TIMELINE.md](docs/ADR_SPEECH_AUDIO_TIMELINE.md).

## Regras rápidas

- Ordem de produção preservada: **ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub**.
- Speech conclui e valida o lote aprovado antes de Studio; não intercalar as fases item a item.
- **Contrato ativo Speech → Studio: v5.** Speech fornece faixas WAV de voz/silêncio e efeito, grafo de áudio, timeline, metadados e hashes; Studio valida o pacote e renderiza o visual.
- Studio aplica um crop canônico no grafo visual e uma única invocação FFmpeg para a composição audiovisual final. Speech pode usar FFmpeg em etapas próprias para preparar áudio; não há uma renderização intermediária de áudio final separada.
- A análise da marca visual chamada “Gemini” é local (OpenCV/template matching em `ArmoredStudio/analysis/gemini_detector.py`); não é uma chamada à API Google Gemini.
- Um único ambiente Python local (.venv) é usado no laboratório para Speech, Studio e RVC Melody.
- Preservar originais, modelos/índices e assets. Não versionar vídeos, bancos locais, ambientes virtuais, credenciais nem saídas de `storage/`.
- `armoredcreator-testbase`, `armoredcreator-test` e o repositório operacional ficam intocados. Esta branch não é produção aprovada.
- Não remover ativos antigos de Studio até concluir os testes das rotas sem RVC e a auditoria seletiva de dependências.

## Evidência E2E real — 17/10/2026

O teste Windows do item 5 passou com o contrato v5:

- Resultado do runner: `STUDIO_SPEECH_BATCH=SUCCESS`, modo `REAL_STUDIO`, um item, `ORIGINALS_INTACT=True`, código de saída 0.
- Rota: `PT_BR_NARRATION` → `RVC_MELODY`.
- Pacote de áudio criado e validado: `voice.wav`, `effect.wav` e `package.json`.
- Vídeo final: `storage/Videos GRUPO_FONTE_teste/5/5_finaldomeulinknovo.mp4`, 10.307.279 bytes.
- `ffprobe`: H.264 1080×1920, duração 11,420 s; AAC estéreo 44.100 Hz, duração 11,401 s.
- SHA-256 do original antes e depois: `c489fcaae803f36407c82dde3b93ff68077001ef5607909081a2c6555bee15a1` (igual ao manifesto).
- O usuário confirmou visualmente que o tratamento de imagem removeu a marca d'água Gemini neste vídeo. Isso comprova esse exemplar, não todos os vídeos/casos de detector.

Relatório: `storage/validation/speech-studio-v5-item5-20261017T185708Z.json`  
Log: `storage/validation/speech-studio-v5-item5-20261017T185708Z.log`

A evidência acima aprova o caminho real de narração PT-BR com Melody e a renderização v5 para esse item. **Ainda faltam testes E2E das classes sem RVC**, auditoria final de dependências/ativos e sincronização da documentação no checkout local. Os ativos legados permanecem preservados; a PR continua em draft e não mesclada.

## Comandos de validação

Na raiz do laboratório:

```powershell
.\.venv\Scripts\python.exe -m compileall -q ArmoredSpeech ArmoredStudio armored_core
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -c "import ArmoredSpeech; import ArmoredStudio.service; print('imports OK')"
```

Para o fluxo real, use somente o manifesto aprovado e o SQLite isolado correspondentes ao fixture. Não use `storage/database/armoredcreator.db`, não use `--copy-only` como prova de E2E real e nunca sobrescreva resultado existente.
