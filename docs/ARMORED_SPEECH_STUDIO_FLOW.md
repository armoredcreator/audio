# ArmoredSpeech → ArmoredStudio — arquitetura de áudio e edição visual

**Repositório:** `armoredcreator/audio`  
**Branch:** `design/speech-studio-audio-timeline-20261017`  
**PR:** #5, draft e não mesclada.  
**Escopo:** laboratório isolado; sem publicação no Hub nem alteração do banco operacional.

## 1. Separação real de responsabilidades

A regra é **ArmoredSpeech possui todo o processamento de áudio; ArmoredStudio possui toda a edição visual e a renderização do vídeo**.

### ArmoredSpeech — proprietário do áudio
- Extrair e preparar áudio; classificar fala, idioma, música, ruído e silêncio.
- Persistir/validar decisões na base isolada do Speech.
- Decidir a elegibilidade de RVC e executar Melody somente para narração PT-BR limpa.
- Preparar a voz ou o silêncio de rota segura; preparar o efeito sonoro e aplicar ganho/normalização uma única vez.
- Definir cortes temporais, sincronização, fades e intro sonora; mixar a trilha completa.
- Executar seu próprio FFmpeg para produzir e validar **um arquivo final AAC/M4A**, com codec, taxa de amostragem, canais, duração, hash e metadados.
- Entregar o contrato v4 contendo somente a trilha final e os dados de integridade/timeline necessários ao mux.

### ArmoredStudio — proprietário da imagem e do vídeo
- Analisar imagem/vídeo; detectar barras, banner visual e marcas VEO/Gemini.
- Consolidar um plano visual e validar o enquadramento.
- Aplicar um único crop da fonte, gerar a introdução visual quando configurada e aplicar os filtros de imagem.
- Validar estruturalmente o arquivo AAC/M4A final e o contrato, sem classificar áudio ou decidir a rota.
- Executar uma única chamada FFmpeg final por vídeo para renderizar a imagem e muxar a trilha AAC por cópia de stream (`-c:a copy`).

O Studio não recebe WAVs de voz/efeito separados, não recebe um grafo de filtros de áudio, não gera silêncio, não chama RVC, não aplica volume/ganho/fade, não mixa faixas e não recodifica a trilha sonora.

## 2. Plano de timeline sem misturar as responsabilidades

O Studio executa primeiro uma pré-análise visual somente de leitura. O plano visual — incluindo o início de vídeo e o retângulo seguro — é fornecido ao Speech como dado temporal. Quando há banner visual, o Speech também mede o início da fala a partir do WAV; o runner concilia e valida o plano temporal antes do render.

Speech usa esses dados apenas para preparar a trilha sonora sincronizada, sem editar pixels ou recortar frames. O handoff v4 inclui o hash do plano visual que serviu para a timeline. Se o plano aplicado no render divergir desse hash, o Studio bloqueia a execução.

## 3. Simplificação: um crop e um FFmpeg final no Studio

- O crop de segurança do planner e o enquadramento 9:16 são consolidados num único retângulo nas coordenadas da fonte.
- O crop é aplicado uma vez antes de dividir os frames para a cena principal e a introdução visual.
- Speech realiza previamente as operações de áudio necessárias e valida a duração final AAC/M4A.
- O FFmpeg do Studio recebe o vídeo, a trilha AAC/M4A final e, quando necessária, a imagem do banner. O grafo final é visual; o áudio é mapeado diretamente e copiado para o MP4 sem processamento ou recodificação.
- A chamada final do Studio não contém `atrim`, `amix`, `afade`, `loudnorm`, `volume`, silêncio gerado ou entrada RVC.

Isso significa **um render FFmpeg final por vídeo no Studio**, não um único processo FFmpeg para todo o programa: Speech ainda usa FFmpeg nas etapas próprias de extração, preparação, efeito e composição de áudio, além da execução independente do motor RVC.

## 4. Contrato v4

O contrato v4 em `ArmoredStudio/speech_handoff.py` valida:
- identidade de fonte/item e caminho do original;
- SHA-256 do original antes/depois;
- caminho contido no laboratório e hash da trilha final;
- formato AAC/M4A, codec AAC, 44,1 kHz estéreo, tamanho e duração;
- hash do plano visual e consistência da duração da timeline/intro.

O contrato **não contém faixas de voz/efeito separadas nem o grafo de áudio**. O Studio não valida elegibilidade de Melody nem toma decisões por classe/rota; esses controles ficam no Speech. Os campos de classe/rota no manifesto do runner são dados de auditoria, não uma instrução para o Studio processar áudio.

## 5. Módulos e ativos

- `ArmoredSpeech/audio_intelligence.py`, `model_processor.py`, `media.py`: análise e preparação de áudio.
- `ArmoredSpeech/rvc_engine.py`: único motor de conversão RVC; usa a `.venv` da raiz.
- `ArmoredSpeech/audio_package.py`: preparação das faixas internas e publicação do AAC/M4A final.
- `ArmoredSpeech/timeline_mixer.py`: política de timeline, composição de áudio e validação do arquivo final.
- `ArmoredSpeech/studio_integration.py` e `studio_batch_runner.py`: preflight do lote Speech, validação e handoff.
- `ArmoredStudio/speech_handoff.py`: validador mínimo do contrato de áudio final.
- `ArmoredStudio/unified.py`: orquestração e análise visual.
- `ArmoredStudio/processing/visual_finalizer.py`: crop único, filtros de imagem, encode de vídeo e mux AAC por cópia de stream.
- `ArmoredStudio/assets/banner.png`: asset visual de Studio.

Os módulos de áudio legados `ArmoredStudio/analysis/audio_intelligence.py`, `ArmoredStudio/processing/rvc.py` e `ArmoredStudio/processing/finalizer.py` não estão no caminho ativo desta branch e não devem ser recriados como fallback.

O modelo/índice RVC passam a morar em `ArmoredSpeech/runtime/rvc/models/<voz>/`; o efeito em `ArmoredSpeech/assets/efeitosonoro.wav`. O motor Speech não faz fallback para os diretórios antigos do Studio.

## 6. Migrar localmente os ativos antes do E2E v4

Na raiz de `ArmoredAudioLab`, depois de atualizar a branch por fast-forward seguro, execute:

```powershell
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
```

O script copia os modelos/índices RVC e o efeito antigo para os novos diretórios do Speech e verifica SHA-256. Não apaga nem altera a origem. Se o destino já existir com conteúdo diferente, bloqueia em vez de sobrescrever.

Confirme que existem pelo menos:
- `ArmoredSpeech\runtime\rvc\models\melody\*.pth`
- o índice Melody, se usado no ambiente local;
- `ArmoredSpeech\assets\efeitosonoro.wav`.

**Não apague os ativos antigos do Studio até o E2E real com contrato v4 passar.** O banner visual continua pertencendo ao Studio.

## 7. Evidências e estado da validação

### E2E anterior aprovado pelo usuário — contrato antigo

Em 17/10/2026, o usuário executou Speech → Studio com `storage/Videos GRUPO_FONTE_teste/2/2_narração_pt.mp4`. O runner reportou `STUDIO_SPEECH_BATCH=SUCCESS`, `MODE=REAL_STUDIO`, `ITEMS=1`, `ORIGINALS_INTACT=True`. O usuário assistiu ao vídeo final `2_finaldomeulinknovo.mp4` e confirmou áudio/imagem corretos; os hashes do original e da cópia conferiram.

Essa evidência confirma o pipeline anterior e a convenção de Storage; **não prova ainda o novo contrato v4**, pois aquela versão entregava componentes/grafo de áudio ao render final do Studio.

### Validação automatizada da separação v4

- CI passou na compilação e em **79 testes automatizados**: https://github.com/armoredcreator/audio/actions/runs/39048512818.
- Os testes cobrem a fronteira de arquivo AAC final, integridade/hash, validação temporal, bloqueio de mismatch entre plano visual e Speech, um crop e um único FFmpeg visual com `-c:a copy`.
- Esses testes não substituem o E2E real no Windows com os ativos RVC migrados para Speech.

### Próximo E2E real proposto

Foi preparado anteriormente um fixture de narração PT-BR do item 5:

- Entrada descritiva: `storage/Videos GRUPO_FONTE_teste/5/5_narração_pt_gemini1.mp4`
- `TEST_ITEM_ID=5`
- SHA-256 do vídeo de referência: `c489fcaae803f36407c82dde3b93ff68077001ef5607909081a2c6555bee15a1`
- Manifesto: `storage/validation/speech-v2-real-smoke-20261017T151136454023Z-62a58b/manifest.json`
- SQLite isolado: `storage/database/armored_speech_lab_20261017T151136454023Z_62a58b.sqlite3`

Esses arquivos foram preparados antes da última refatoração; a preparação de áudio v4 será refeita pelo runner ao consumir esse manifesto. Primeiro atualizar o checkout e migrar/verificar os ativos, depois executar o runner v4 atualizado. O runner deve preparar e validar todos os AAC/M4A do lote antes da primeira renderização.

Após o render, verificar relatório `SUCCESS`, contrato `4`, codec/duração do AAC, saída canônica esperada `5_finaldomeulinknovo.mp4`, imagem/crop/intro, sincronia, áudio e SHA-256 do original antes/depois. O usuário deve assistir ao resultado antes de aprovar essa arquitetura em produção.

## 8. Ordem de trabalho e segurança

1. Atualizar localmente a branch por fast-forward seguro.
2. Executar `ArmoredSpeech/scripts/migrate_audio_assets.ps1` e conferir os novos caminhos.
3. Executar o E2E real com contrato v4 e inspecionar o MP4.
4. Validar as rotas sem RVC (música, silêncio, ruído, fala com música e vídeos com barras), ainda sem publicar no Hub.
5. Auditar referências e módulos legados; manter só o que tem chamadores/testes válidos.
6. Somente após os E2Es passarem, remover definitivamente os ativos RVC/efeito legados do diretório do Studio e limpar os intermediários temporários do Storage.
7. Revisar a PR; ela permanece draft e não mesclada até a aprovação completa.

- Nenhum teste publica no Hub.
- O fixture/runner usa SQLite isolado; não abre o banco operacional.
- Os originais do corpus, o modelo/índice Melody e o efeito devem permanecer intactos durante a migração.
- `armoredcreator-testbase`, `armoredcreator-test` e o repositório operacional permanecem intocados.
