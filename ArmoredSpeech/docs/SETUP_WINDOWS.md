# ArmoredAudioLab — configuração Windows/Python 3.11

O laboratório usa um único ambiente Python: `.venv` na raiz. Speech, Studio visual e RVC Melody usam o mesmo interpretador.

## 1. Preparar o ambiente

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\ArmoredSpeech\setup_runtime.ps1
```

O setup não cria um ambiente próprio do RVC. Não recrie nem troque a `.venv` testada sem necessidade.

## 2. Copiar os ativos de áudio para Speech

Depois de atualizar a branch, execute:

```powershell
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
```

O script copia `ArmoredStudio/runtime/rvc/models` para `ArmoredSpeech/runtime/rvc/models` e o efeito sonoro para `ArmoredSpeech/assets/efeitosonoro.wav`. Confere SHA-256 e bloqueia um destino existente com conteúdo diferente. Não copia o ambiente legado `env311`, não apaga a origem e não modifica vídeos.

## 3. Conferir os caminhos

```powershell
.\.venv\Scripts\python.exe -c "from ArmoredSpeech import rvc_engine as r; print('Python:', r.resolver_rvc_python()); print('RVC root:', r.RVC_ROOT); print('Models:', r.MODELS_DIR)"
Test-Path .\ArmoredSpeech\runtime\rvc\models\melody
Test-Path .\ArmoredSpeech\assets\efeitosonoro.wav
```

O diretório Melody deve conter ao menos um `.pth`; o índice `.index` é opcional. Se a variável `ARMORED_RVC_ROOT` estiver definida, ela continua sendo um override explícito e deve apontar para o conjunto de modelos pretendido.

## 4. Validar

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_real_audio.py
```

Para testar RVC isoladamente, use uma saída que ainda não exista:

```powershell
.\.venv\Scripts\python.exe -m ArmoredSpeech.rvc_engine .\entrada.wav .\storage\validation\melody_output.wav melody
```

## 5. Limpeza do legado

Não apague `ArmoredStudio/runtime/rvc` nem o efeito antigo durante a cópia. Primeiro rode a validação RVC, o E2E Speech → Studio com a nova localização, confira o vídeo e os hashes. Só então remova a cópia antiga manualmente. Os modelos, índices e ativos originais não devem ser descartados se a migração falhar.
