# Decisão técnica — separação real ArmoredSpeech → ArmoredStudio

**Status:** contrato v5 implementado e E2E real de uma narração PT-BR aprovado em 17/10/2026; testes E2E das rotas sem RVC e auditoria final ainda pendentes.  
**Branch:** `design/speech-studio-audio-timeline-20261017`  
**Escopo:** somente `armoredcreator/audio`; sem integração no repositório operacional, sem alteração do banco operacional e sem publicação no Hub.

## 1. Decisão de arquitetura

A fronteira é **Speech decide e prepara todo o áudio; Studio decide e renderiza o visual**.

### ArmoredSpeech

- Extrai e analisa áudio; classifica fala, idioma, música, ruído e silêncio.
- Bloqueia RVC quando a elegibilidade é incerta. A rota `RVC_MELODY` é permitida apenas para narração PT-BR limpa, com fala detectada, sem música e com decisão explícita elegível.
- Prepara uma faixa `voice_track` (voz processada ou silêncio) e uma faixa `effect_track` com efeito normalizado/ganho aplicado uma vez.
- Determina sincronização, cortes temporais, fades, duração, intro sonora, mixagem e opções de encode.
- Produz o grafo de áudio e o contrato v5; não renderiza uma faixa final AAC/M4A separada.

### ArmoredStudio

- Analisa somente o vídeo/imagem: barras, banner visual, marcas VEO/Gemini, crop e enquadramento.
- Consolida um único crop de origem, monta a intro visual e constrói o grafo visual.
- Valida o contrato Speech, mas não classifica áudio, escolhe rotas, invoca RVC, define ganho/fades nem constrói o grafo de áudio.
- O detector da marca d'água chamado Gemini em `ArmoredStudio/analysis/gemini_detector.py` usa OpenCV/template matching local; não é uma chamada à API Google Gemini.

## 2. Contrato v5

O pacote é produzido por `ArmoredSpeech/audio_package.py` / `ArmoredSpeech/studio_integration.py` e validado por `ArmoredStudio/speech_handoff.py`. Ele declara, por item:

- identidade e caminho/hash do original;
- faixas de voz/silêncio e efeito com caminhos, hashes, formato e metadados;
- `audio_filter_graph` e `audio_output_label`;
- timeline, sincronização, plano visual identificado por hash e opções de render final;
- integridade necessária para impedir render se os arquivos, as rotas, hashes ou tempos forem inconsistentes.

Studio não reinterpreta os campos de política de áudio. Qualquer divergência invalida o handoff antes do render.

## 3. Um crop canônico e uma única renderização audiovisual final

1. Studio faz uma pré-análise visual somente de leitura.
2. Speech recebe o plano visual como dado temporal e prepara o pacote e o grafo de áudio.
3. O runner prepara/valida todos os pacotes do lote antes de começar a renderização.
4. O finalizador visual une o grafo de vídeo e o grafo de áudio assinado por Speech no mesmo `-filter_complex` e invoca FFmpeg **uma vez para a renderização audiovisual final**.

Essa regra não proíbe etapas preparatórias próprias do Speech que usem FFmpeg para extrair ou normalizar áudio, nem a conversão RVC. O que foi eliminado é a renderização intermediária de uma faixa final de áudio que depois seria combinada ao vídeo em outra chamada.

## 4. Integridade, ativos e isolamento

- Original canônico é somente leitura; o runner compara SHA-256 antes/depois.
- Modelos e índices Melody ficam sob `ArmoredSpeech/runtime/rvc/models`; o efeito em `ArmoredSpeech/assets/efeitosonoro.wav`.
- O script de migração copia e confere SHA-256, sem apagar os ativos antigos nem sobrescrever destino divergente.
- O runner só recebe manifesto aprovado e SQLite de Speech isolado; não abre `storage/database/armoredcreator.db`.
- Ativos antigos de Studio não devem ser excluídos até a aprovação das rotas sem RVC e da auditoria de dependências.
- Nenhum teste desta branch publica no Hub. A integração no repositório operacional é uma fase futura, fora desta PR.

## 5. Evidência E2E real — 17/10/2026

Teste realizado pelo usuário em Windows no item `source_teste/5`, com contrato v5:

- `STUDIO_SPEECH_BATCH=SUCCESS`, `MODE=REAL_STUDIO`, `ITEMS=1`, `ORIGINALS_INTACT=True`, exit code `0`.
- Classe `PT_BR_NARRATION`, rota `RVC_MELODY`; pacotes `voice.wav`, `effect.wav` e `package.json` presentes.
- Vídeo final: `storage/Videos GRUPO_FONTE_teste/5/5_finaldomeulinknovo.mp4`.
- Streams: H.264 1080×1920, 11,420 s; AAC estéreo 44.100 Hz, 11,401 s. `ffprobe` terminou com código 0.
- Hash do original antes e depois: `c489fcaae803f36407c82dde3b93ff68077001ef5607909081a2c6555bee15a1`, igual ao hash do manifesto.
- O usuário confirmou visualmente que o tratamento de imagem removeu a marca d'água Gemini neste vídeo.
- Relatório: `storage/validation/speech-studio-v5-item5-20261017T185708Z.json`; log: `storage/validation/speech-studio-v5-item5-20261017T185708Z.log`.

O resultado prova o caminho real v5 de narração PT-BR/Melody nesse item, a composição audiovisual e a preservação do original. Não prova ainda a correção de cada rota de áudio sem RVC nem de todos os formatos de vídeo.

## 6. Próxima sequência

1. Sincronizar localmente os commits de documentação por fast-forward seguro e executar `compileall` e a suíte `pytest` completa no HEAD atualizado.
2. Verificar as Actions associadas ao HEAD atualizado e revisar o diff integral da PR.
3. Executar E2E isolado para cada classe sem RVC (música, sem áudio, ruído, fala estrangeira e narração com música), um caso por vez, preservando a base experimental e sem tocar na base operacional.
4. Auditar referências, dependências e ativos RVC/áudio antigos; só depois planejar remoção seletiva.
5. Manter a PR #5 em draft e **não mesclar** até os E2Es adicionais e a auditoria final passarem.
