# ArmoredSpeech → ArmoredStudio: separação de áudio e vídeo

## Objetivo e estado

Esta branch de laboratório implementa a separação de responsabilidades antes de qualquer migração para o repositório operacional:

**ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub.**

- ArmoredSpeech é o proprietário do processamento e das decisões de áudio.
- ArmoredStudio é o proprietário das decisões visuais e da edição do vídeo.
- O renderizador final combina os grafos criados pelos dois domínios em **uma chamada FFmpeg audiovisual por vídeo**, com **um único crop de origem**.
- O E2E real do item 5 em 17/10/2026 passou no contrato v5: rota Melody, pacote Speech validado, renderização final H.264/AAC e SHA-256 original preservado. Ainda faltam testes de classes sem RVC e a auditoria final.
- A branch permanece experimental; o PR #5 está em draft e não foi mesclado. Nenhuma alteração foi aplicada ao repositório operacional ou às bases congeladas.

## 1. Responsabilidade exclusiva do ArmoredSpeech

Speech classifica o áudio, detecta idioma/fala/música/ruído/silêncio, valida se RVC Melody é permitido, extrai/prepara o áudio de entrada, executa RVC quando permitido, gera silêncio para rotas sem voz, prepara o efeito e aplica a política de ganho uma única vez. Ele também decide a sincronização, os cortes temporais, os fades, a duração e a composição das faixas.

Speech prepara dois artefatos validados por item:
- `voice.wav`: voz Melody aprovada ou faixa de silêncio para a rota sem RVC;
- `effect.wav`: efeito sonoro normalizado e com ganho aplicado, compartilhado pelo lote quando possível.

Em paralelo, Speech produz o contrato v5 com:
- identidade do item e hash do original antes/depois;
- hash e metadados dos arquivos WAV;
- grafo de filtros de áudio completo e seu SHA-256;
- rótulo de saída do grafo;
- plano temporal visual usado, início de voz/vídeo, duração principal, intro e posição;
- opções de renderização final do áudio (codec, bitrate, amostragem, canais e looping do efeito).

A política e o grafo de áudio nascem em Speech. O contrato é validado antes do primeiro render do lote.

## 2. Responsabilidade exclusiva do ArmoredStudio

Studio analisa somente o vídeo/imagem: barras, banner visual, marcas VEO/Gemini, enquadramento e plano visual. Ele valida o plano visual, escolhe o único retângulo de crop seguro e aplica efeitos de imagem, intro visual, banner e encode de vídeo.

Studio não importa nem executa Audio Intelligence, não extrai áudio, não classifica classes de áudio, não decide rota, não chama RVC, não gera silêncio, não escolhe ganho/volume/fades, não cria o grafo de áudio e não mixa as faixas.

O validador `ArmoredStudio/speech_handoff.py` é apenas a fronteira do contrato: confere identidade, integridade do original, formato/hash das faixas e integridade do grafo produzido por Speech. Não recalcula a política de áudio.

## 3. Um crop e uma única renderização final FFmpeg

O pipeline final é:

1. Studio faz a pré-análise visual somente de leitura.
2. Speech usa o plano visual como dado temporal e prepara as faixas e o grafo de áudio.
3. O runner valida o lote completo e os hashes antes de começar qualquer render.
4. O renderizador junta mecanicamente o grafo visual do Studio e o grafo de áudio do Speech e executa **uma única invocação FFmpeg para a composição audiovisual final**.

O grafo visual aplica o início/crop da fonte uma única vez, divide os frames já recortados para a cena principal e a intro visual e concatena o resultado. O grafo de áudio fornecido por Speech consome `[1:a]` (voz/silêncio) e `[2:a]` (efeito). Se houver intro visual, o banner é a entrada visual `[3:v]`.

Importante: “uma única chamada FFmpeg” refere-se à **renderização final de áudio+vídeo**. Speech ainda pode usar FFmpeg em etapas próprias de extração, preparação e normalização necessárias antes da renderização, além de executar o motor RVC. Não existe um segundo render FFmpeg que monte uma faixa sonora final intermediária e depois outra chamada que remonte o vídeo.

O renderizador recebe os parâmetros de encode de áudio que Speech declarou no contrato. Ele não inventa nem altera as regras de áudio; traduz o contrato validado em argumentos FFmpeg.

## 4. Contrato v5

O contrato v5 é produzido em `ArmoredSpeech/audio_package.py` e consumido/validado por `ArmoredStudio/speech_handoff.py`.

- `voice_track` e `effect_track`: caminhos dentro do laboratório, SHA-256, PCM WAV, 44,1 kHz estéreo, duração e tamanho;
- `audio_filter_graph`: o grafo criado por Speech;
- `audio_output_label`: `[main_a]` ou `[final_a]`;
- `audio_timeline`: hashes do grafo/plano, inícios, duração da faixa principal, intro e opções de renderização;
- original: caminho canônico e SHA-256 antes/depois.

A validação bloqueia arquivos ausentes, fora do laboratório, inválidos, hashes divergentes, grafos não permitidos e inconsistência temporal. Nenhuma renderização começa se qualquer item do lote falhar na validação.

## 5. Módulos e ativos

### Em ArmoredSpeech
- `audio_intelligence.py`, `model_processor.py`, `media.py`: classificação e preparação do áudio.
- `rvc_engine.py`: único motor RVC, com ativos sob `ArmoredSpeech/runtime/rvc/`; não faz fallback para diretórios de Studio.
- `audio_package.py`: cria/valida as faixas de voz/silêncio, o efeito e o contrato.
- `timeline_mixer.py`: cria o grafo de áudio e detecta início de fala quando necessário; não faz renderização separada do áudio final.
- `studio_integration.py`, `studio_batch_runner.py`: preflight de lote, validação e handoff.

### Em ArmoredStudio
- `unified.py`: análise visual, validação do contrato e chamada ao renderizador.
- `processing/visual_finalizer.py`: um crop, grafo visual, concatenação da intro visual e uma única chamada FFmpeg audiovisual.
- `speech_handoff.py`: validador estrutural do contrato v5; não processa áudio.

Os módulos de processamento de áudio legados `ArmoredStudio/analysis/audio_intelligence.py`, `ArmoredStudio/processing/rvc.py` e `ArmoredStudio/processing/finalizer.py` não fazem parte do caminho ativo da branch e não devem ser recriados como fallback. A presença de um validador de contrato não representa transferência do processamento de áudio ao Studio.

## 6. Migração local dos ativos (cópia verificada)

Na raiz de `ArmoredAudioLab`, depois de atualizar esta branch por fast-forward seguro, execute:

```powershell
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
```

O script copia modelos/índices RVC e `efeitosonoro.wav` dos caminhos antigos para:
- `ArmoredSpeech\runtime\rvc\models\melody\`
- `ArmoredSpeech\assets\efeitosonoro.wav`

Ele confere SHA-256, não sobrescreve destinos diferentes e **não apaga nem altera a origem**. Não remova manualmente ativos antigos do Studio antes de um E2E real com contrato v5 aprovado. O banner visual permanece em Studio.

## 7. Storage, isolamento e segurança

- O corpus fica em `storage/Videos GRUPO_FONTE_teste/<ID>/<arquivo_descritivo>.mp4`.
- O runner usa um banco SQLite isolado do Speech; nunca abre `storage/database/armoredcreator.db`.
- Manifestos, relatórios, grafos e artefatos de lote ficam sob `storage/validation/`.
- Um artefato existente nunca é sobrescrito automaticamente.
- Os arquivos do corpus, originais canônicos, modelos/índices e assets não são apagados por testes.
- Nenhum teste publica no Hub.

## 8. Evidência E2E v5 e próximos critérios

Em 17/10/2026, o usuário executou no Windows o runner real no contrato v5 para a fonte `source_teste`, mensagem `5`, usando o manifesto aprovado e um SQLite experimental isolado. Resultado observado:

- `STUDIO_SPEECH_BATCH=SUCCESS`, `MODE=REAL_STUDIO`, `ITEMS=1`, código de saída 0.
- Pacote Speech validado com `voice.wav`, `_shared/effect.wav` e `package.json`; classe `PT_BR_NARRATION` e rota `RVC_MELODY`.
- Vídeo final: `storage/Videos GRUPO_FONTE_teste/5/5_finaldomeulinknovo.mp4`, H.264 1080×1920, duração de 11,420 s; áudio AAC estéreo a 44.100 Hz, duração de 11,401 s.
- O SHA-256 do original antes/depois foi `c489fcaae803f36407c82dde3b93ff68077001ef5607909081a2c6555bee15a1`, igual ao manifesto.
- O usuário confirmou visualmente que a edição tratou e removeu a marca d'água Gemini neste vídeo.
- Relatório: `storage/validation/speech-studio-v5-item5-20261017T185708Z.json`; log: `storage/validation/speech-studio-v5-item5-20261017T185708Z.log`.

Esse teste valida o caminho real v5 de narração PT-BR/Melody nesse item, a saída audiovisual e a preservação do original. Não generaliza para todas as classes, modelos, vídeos ou detecções.

## 9. Próximos passos seguros

1. Atualizar o checkout local por fast-forward seguro para os commits de documentação e executar a suíte completa de testes.
2. Executar E2E isolados para cada rota sem RVC (música, sem áudio, ruído, fala estrangeira e fala com música), um cenário por vez, sem compartilhar nem resetar o SQLite experimental existente.
3. Auditar referências a motores RVC/áudio antigos e dependências antes de qualquer remoção; manter os ativos legados preservados durante essa auditoria.
4. Atualizar evidências/CI e revisar o diff completo. A PR #5 permanece em draft e não mesclada até a auditoria e os testes adicionais passarem.

## 10. Segurança e isolamento

- Não tocar em `armoredcreator/armoredcreator`, `armoredcreator-test` nem `armoredcreator-testbase`.
- Não abrir nem alterar `storage/database/armoredcreator.db`; usar somente o SQLite experimental declarado no comando.
- Não publicar no Hub.
- Não executar `git reset --hard`, `git clean` nem limpeza recursiva genérica.
- Preservar vídeos originais, resultado aprovado, banco isolado, modelos/índices, assets e evidências.
