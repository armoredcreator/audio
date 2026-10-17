# Decisão técnica — separação real ArmoredSpeech → ArmoredStudio

**Status:** v3 implementado na branch isolada; testes automatizados verdes no commit-base e aguardando revalidação após os últimos ajustes. O E2E histórico aprovado pelo usuário usou o contrato antigo e não substitui o E2E real do contrato v3.  
**Branch:** `design/speech-studio-audio-timeline-20261017`  
**Escopo:** somente `armoredcreator/audio`; sem integração no repositório operacional, sem reset de banco operacional e sem publicação no Hub.

## 1. Decisão de arquitetura

A fronteira correta é **Speech decide e prepara o áudio; Studio decide e renderiza o visual**. Não enviar para Studio a propriedade do RVC, classificação, extração, silêncio, ganho, efeito ou regras da mixagem.

Speech produz um pacote v3 que declara:
- decisão de áudio persistida no registro próprio de Speech;
- WAV de voz convertido por Melody quando permitido (ou descrição explícita de silêncio para rotas não RVC);
- WAV de efeito do projeto normalizado/ganho aplicado uma única vez;
- descritores permitidos para as entradas da renderização;
- grafo de filtros somente de áudio que Speech construiu;
- rota, hashes, metadados técnicos, evidência de integridade do original e metadados de sincronização.

Studio valida o contrato estruturalmente e não reclassifica nem altera a política. O finalizador visual une o grafo de vídeo do Studio ao grafo de áudio assinado por Speech no **mesmo `-filter_complex` e numa única chamada final do FFmpeg**. Esse FFmpeg é o compositor audiovisual; não representa uma segunda decisão nem uma etapa separada de processamento de áudio.

## 2. Responsabilidades exclusivas

### ArmoredSpeech
- Extrair e analisar áudio; classificar fala, idioma, música, ruído e silêncio.
- Bloquear a classe `UNKNOWN`/decisão inconclusiva.
- Autorizar RVC apenas para narração PT-BR limpa, fala detectada e ausência explícita de música.
- Invocar Melody no máximo uma vez por item elegível; falha não cai silenciosamente para voz original.
- Preparar voz/silêncio e efeito do projeto, aplicando normalização/ganho de efeito uma vez.
- Construir a receita de mixagem, corte temporal, intro/fades e concatenação de áudio em `ArmoredSpeech/timeline_mixer.py`.
- Entregar pacote `contract_version: 3`, com hashes/identidade e regras fechadas.

### ArmoredStudio
- Inspecionar vídeo e construir/validar o plano visual (barras, banner, VEO/Gemini, crop e framing).
- Ter um único crop de fonte antes de dividir os frames entre introdução e corpo.
- Validar a fronteira Speech, sem escolher a rota, chamar RVC, extrair áudio, gerar silêncio, normalizar, aplicar ganhos ou misturar faixas.
- Combinar os filtros visuais e o grafo de áudio de Speech e fazer uma chamada final do FFmpeg.
- Validar saída e hash do original.

`ArmoredStudio/speech_handoff.py` é somente o adaptador/validador do contrato; não possui política de classificação nem conversão de áudio. `ArmoredStudio/processing/visual_finalizer.py` materializa o grafo fornecido por Speech no render único. Os módulos de áudio legados `ArmoredStudio/analysis/audio_intelligence.py`, `ArmoredStudio/processing/rvc.py` e `ArmoredStudio/processing/finalizer.py` foram retirados do caminho ativo e não devem ser reintroduzidos como fallback.

## 3. Uma única composição temporal e visual

- O grafo de áudio é criado em Speech sem executar FFmpeg; o finalizador recebe o grafo/inputs e aplica tudo em uma renderização audiovisual.
- O grafo visual aplica `video_start` e o crop aprovado uma vez antes do `split` das variantes intro/corpo, evitando crops independentes duplicados.
- O grafo de áudio aplica `audio_start` à voz, mas não corta o início do efeito principal; o efeito da intro usa fades/trim próprios quando a intro está habilitada.
- Para música, ruído, silêncio, fala estrangeira ou qualquer classe sem elegibilidade Melody, Speech declara explicitamente a entrada de silêncio e a faixa de efeito. Studio não infere a rota.
- O plano visual e o grafo de áudio têm SHA-256 no pacote. Divergência, caminho fora do laboratório, hashes ou metadados inválidos bloqueiam a renderização antes do primeiro item.
- Todo o lote é preflightado e todos os pacotes v3 validados antes do primeiro render. O modo `copy-only` é apenas teste de fronteira e não demonstra processamento de áudio real.

## 4. Propriedade e localização dos ativos

O caminho canônico dos modelos/índices RVC é `ArmoredSpeech/runtime/rvc/models/<voz>/`; não há fallback implícito para `ArmoredStudio/runtime/rvc` nem redirecionamento por `ARMORED_RVC_ROOT`. O efeito canônico é `ArmoredSpeech/assets/efeitosonoro.wav`.

O script local `ArmoredSpeech/scripts/migrate_audio_assets.ps1` **copia e verifica SHA-256**, sem remover a origem. Ele bloqueia destinos com conteúdo divergente. Só após um E2E v3 aprovado é permitido remover os ativos RVC/efeito legados da pasta de Studio. O banner continua sendo um ativo visual de Studio.

## 5. Execução e critério de aceite

1. Atualizar a branch do laboratório por fast-forward, sem sobrescrever alterações rastreadas.
2. Executar `ArmoredSpeech/scripts/migrate_audio_assets.ps1` para copiar e verificar o modelo Melody, índice e efeito para Speech.
3. Conferir que `ArmoredSpeech/runtime/rvc/models/melody/*.pth`, índice quando existente, e `ArmoredSpeech/assets/efeitosonoro.wav` estão presentes.
4. Executar o preparador `--real-video` com um vídeo de corpus em cópia isolada (não abrir/modificar banco operacional).
5. Executar `ArmoredSpeech.studio_batch_runner` com o manifesto/SQLite isolado, sem `--copy-only`.
6. Abrir/escutar a saída, conferir rota RVC e logs, streams/duração, qualidade visual/crop e SHA-256 do original.
7. Repetir em rotas não-RVC antes de remover definitivamente ativos locais legados ou encerrar a PR.

O usuário aprovou em 17/10/2026 um E2E antigo do item 2 (RVC Melody, hash original preservado e áudio/imagem corretos). Essa evidência é útil como referência de qualidade, mas **não é prova do contrato v3**. O item 5 foi preparado mais recentemente, classificado como `PT_BR_NARRATION`, `pt-br`, confiança `0.9828158873485948`, `music_detected=false`, `rvc_eligible=true`; ainda falta executar o runner com o código v3 atualizado e ativos já migrados para Speech.

## 6. Testes automatizados

O último CI confirmado para o commit-base executou `compileall` e **80 testes passaram**: https://github.com/armoredcreator/audio/actions/runs/39040124557. Mudanças posteriores (caminho canônico RVC e fixtures com ativo Speech) exigem que a execução de CI mais recente seja conferida antes de tratar a branch como novamente verde.

