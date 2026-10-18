# ArmoredSpeech — proprietário do processamento de áudio

ArmoredSpeech é o único responsável por classificar, decidir e preparar áudio. ArmoredStudio é responsável pela análise visual, edição de imagem e montagem final do vídeo. A separação acontece por contratos explícitos; o Studio não tem fallback para o antigo processamento de áudio.

## Separação de responsabilidades

- **Speech:** classificar fala/idioma/ruído/música/silêncio; aplicar as regras de elegibilidade RVC; extrair e preparar áudio; executar RVC Melody quando permitido; preparar voz/silêncio e efeito; aplicar ganho uma única vez; sincronizar cortes temporais/fades/intro sonora e criar o grafo de áudio; declarar codec, bitrate, amostragem, canais e looping do efeito no contrato v5.
- **Studio:** analisar imagem/vídeo, detectar barras/banner/VEO/Gemini; consolidar o plano visual; combinar crop de segurança e enquadramento 9:16 em **um único crop**; montar intro visual e aplicar filtros de imagem.
- **Renderizador final:** concatena mecanicamente o grafo visual do Studio e o grafo de áudio do Speech numa chamada FFmpeg audiovisual. O Studio não escolhe filtros, volumes, fades, rota de áudio nem RVC.
- **Contrato v5:** identidade, hash do original, faixas WAV de Speech, hashes/tamanhos/durações, grafo de áudio e opções de render final. A validação bloqueia o lote se qualquer pacote não estiver consistente.
- Originais são somente leitura; hashes antes/depois. O lote inteiro deve ser preparado e validado antes da primeira renderização.

“Uma chamada FFmpeg” refere-se à composição audiovisual final. Speech pode usar FFmpeg em etapas próprias de preparação/normalização necessárias antes do render e invocar o motor RVC; não há uma segunda chamada para montar uma WAV/M4A final separada que depois é novamente combinada ao vídeo.

## Ativos locais (Windows PowerShell)

Na raiz do repositório, após atualizar a branch por fast-forward seguro:

```powershell
.\ArmoredSpeech\setup_runtime.ps1
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
.\.venv\Scripts\python.exe -m pytest -q
```

Os modelos/índices RVC e `efeitosonoro.wav` são assets locais fora do Git. O caminho canônico de runtime é `ArmoredSpeech/runtime/rvc/models` e `ArmoredSpeech/assets/efeitosonoro.wav`. O script de migração serve somente para um checkout antigo: se a cópia do Studio existir, ele a copia com verificação SHA-256 e sem sobrescrever destinos divergentes; se os ativos já estiverem no Speech e os caminhos antigos não existirem, ele valida os ativos canônicos e termina sem precisar da árvore Studio. Não há fallback de runtime para o Studio.

Não remova os ativos legados do Studio antes de um E2E real com contrato v5 aprovado.

## Diagnóstico

```powershell
.\.venv\Scripts\python.exe -m ArmoredSpeech.rvc_engine .\entrada.wav .\storage\validation\melody_output.wav melody
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_real_audio.py
```

Use uma saída que ainda não exista. O validador real precisa dos modelos/índices migrados para Speech e do corpus de teste.

## Limites

Este é o laboratório isolado `armoredcreator/audio`. Não publica no Hub e não altera o repositório operacional. CI verde não substitui E2E real das classes de áudio e inspeção audiovisual.

## Evidência E2E v5 (17/10/2026)

O teste real Windows do item `source_teste/5` passou com `STUDIO_SPEECH_BATCH=SUCCESS`, `MODE=REAL_STUDIO`, `ORIGINALS_INTACT=True` e código de saída 0. A decisão Speech foi `PT_BR_NARRATION` → `RVC_MELODY`; o pacote validado continha `voice.wav`, `effect.wav` e `package.json`. O vídeo final H.264/AAC foi produzido e o original manteve o SHA-256 declarado no manifesto: `c489fcaae803f36407c82dde3b93ff68077001ef5607909081a2c6555bee15a1`.

Evidências locais:
- Relatório: `storage/validation/speech-studio-v5-item5-20261017T185708Z.json`
- Log: `storage/validation/speech-studio-v5-item5-20261017T185708Z.log`

Esse E2E validou o caminho PT-BR/Melody no item de referência. Atualização de 18/10: o corpus de 17 vídeos também concluiu o render real em Studio, e a validação sintética separada de `NO_USABLE_NARRATION` completou a rota `EFFECT_ONLY`; a auditoria de ownership passou e 102 testes passaram localmente. As cópias locais antigas do Studio foram removidas após verificação SHA-256 e testes; o RVC e o efeito canônicos do Speech foram preservados. A PR segue draft até a revisão final do diff e do CI após a sincronização destas correções.
