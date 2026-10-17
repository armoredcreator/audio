# ArmoredSpeech — proprietário do processamento de áudio

ArmoredSpeech é o único responsável por classificar, preparar e finalizar o áudio. ArmoredStudio é responsável pela análise/edição visual e pelo render final do vídeo.

## Separação de responsabilidades

- **Speech:** detectar fala/idioma/ruído/música/silêncio; selecionar a rota segura; extrair e preparar áudio; executar RVC Melody quando elegível; preparar efeito e aplicar ganho uma única vez; sincronizar/trimar voz, criar fades e intro sonora, mixar e produzir o arquivo final AAC/M4A.
- **Studio:** analisar imagem/vídeo, detectar barras/banners visuais e VEO/Gemini; consolidar o plano visual; aplicar um único crop; renderizar os frames e copiar a trilha AAC/M4A pronta para o MP4 com `-c:a copy`.
- **Contrato v4:** `ArmoredStudio/speech_handoff.py` valida identidade, integridade do original, caminho/hash/duração do AAC/M4A e hash/timing do plano. Não recebe faixas de voz/efeito separadas nem o grafo de áudio.
- **Um crop, um render final:** o Studio chama FFmpeg uma vez por vídeo e executa apenas filtros visuais no grafo final. Speech usa FFmpeg em seu próprio pipeline de áudio quando necessário.
- Originais são somente leitura; hashes antes/depois. O lote todo deve ser preparado/validado antes da primeira renderização Studio.

## Ativos locais (Windows PowerShell)

Na raiz do repositório, após atualização segura da branch:

```powershell
.\ArmoredSpeech\setup_runtime.ps1
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
.\.venv\Scripts\python.exe -m pytest -q
```

O script copia modelos/índices do RVC e o efeito dos caminhos antigos para `ArmoredSpeech/runtime/rvc/models` e `ArmoredSpeech/assets/efeitosonoro.wav`, confere SHA-256 e não apaga nem sobrescreve a origem. Não remova ativos de Studio antes de um E2E v4 real aprovado.

## Diagnóstico

```powershell
.\.venv\Scripts\python.exe -m ArmoredSpeech.rvc_engine .\entrada.wav .\storage\validation\melody_output.wav melody
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_real_audio.py
```

Use uma saída que ainda não exista. O validador real precisa dos modelos/índices locais e do corpus de teste.

## Limites

Este é o laboratório isolado `armoredcreator/audio`. Não publica no Hub e não altera o repositório operacional. CI verde não substitui E2E real das classes de áudio e inspeção audiovisual.
