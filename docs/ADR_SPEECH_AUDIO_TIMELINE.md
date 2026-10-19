# Decisão técnica — separação real ArmoredSpeech → ArmoredStudio

**Status (18/10/2026):** contrato v5 implementado; E2E real do item PT-BR/Melody e corpus de 17 vídeos aprovados; fixture sintética `NO_USABLE_NARRATION` passou por `EFFECT_ONLY`; auditoria local de ownership passou e 102 testes locais passaram. Correções documentais/proteções Git ainda aguardam CI verde e sincronização local.  
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

1. Speech conclui primeiro a classificação, preflight, preparação de faixas e criação dos grafos do lote.
2. Antes de Studio iniciar, Speech gera duas opções temporais completas por item: `NO_BANNER` e `BANNER_PRESENT`. A segunda incorpora o início de voz como trim próprio da faixa vocal e o corte da fonte; o efeito continua começando no início da parte principal.
3. O runner valida os pacotes e ambas as opções para todos os itens. Só então Studio analisa os visuais e seleciona uma opção pronta com base no detector de banner; não pode alterar os filtros nem recalcular a timeline de áudio.
4. Depois de validar a opção selecionada e o plano visual, o finalizador une o grafo de vídeo e o grafo de áudio escolhido por Speech no mesmo `-filter_complex` e invoca FFmpeg **uma vez para a renderização audiovisual final**.

Essa regra não proíbe etapas preparatórias próprias do Speech que usem FFmpeg para extrair ou normalizar áudio, nem a conversão RVC. O que foi eliminado é a renderização intermediária de uma faixa final de áudio que depois seria combinada ao vídeo em outra chamada.

## 4. Integridade, ativos e isolamento

- Original canônico é somente leitura; o runner compara SHA-256 antes/depois.
- Modelos e índices Melody ficam sob `ArmoredSpeech/runtime/rvc/models`; o efeito em `ArmoredSpeech/assets/efeitosonoro.wav`.
- O script de migração é opcional: lê os caminhos antigos somente se existirem, confere SHA-256, não sobrescreve destinos divergentes e também valida os assets já instalados no Speech.
- Os caminhos canônicos de runtime são `ArmoredSpeech/runtime/rvc/models` e `ArmoredSpeech/assets/efeitosonoro.wav`; Studio não tem fallback para ativos legados.
- Neste checkout Windows, as 14 cópias RVC e o efeito antigo do Studio foram removidos seletivamente depois de confirmar hashes idênticos aos assets Speech e concluir os E2Es. O banner visual foi preservado.
- O runner só recebe manifesto aprovado e SQLite de Speech isolado; não abre `storage/database/armoredcreator.db`. `storage/` e o efeito binário do Speech são ignorados pelo Git.
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

O resultado prova o caminho real v5 de narração PT-BR/Melody no item de referência. Atualização de 18/10: corpus de 17 vídeos passou por análise Speech e renderização Studio real em 17/17, com hashes dos originais inalterados; o usuário verificou todos os resultados. Rotas observadas: 9 `RVC_MELODY`, 8 `EFFECT_ONLY`. Uma fixture sintética separada também validou `NO_USABLE_NARRATION` → `EFFECT_ONLY` e faixa de voz silenciosa. Isto não substitui um exemplo real de fala ininteligível.

## 6. Próxima sequência

1. Sincronizar o checkout Windows por fast-forward após as atualizações documentais/proteção do Git e repetir `compileall`, `pytest` e a auditoria de ownership.
2. Confirmar CI verde no HEAD atualizado e revisar as 53 alterações e o histórico declarado de 301 commits contra a branch-base antes da revisão formal.
3. Manter rastreáveis os resultados do corpus de 17 vídeos e da fixture `NO_USABLE_NARRATION`; não sobrescrever evidências anteriores.
4. Manter a PR #5 em draft e **não mesclar** até a revisão do diff/histórico e o CI final. Nenhuma integração no repositório operacional faz parte desta etapa.
