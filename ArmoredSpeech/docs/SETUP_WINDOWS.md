# ArmoredSpeech — ambiente Python unificado no Windows

O AudioLab possui **um único ambiente operacional** na raiz do repositório: `.venv`. ArmoredSpeech, ArmoredStudio e RVC Melody devem usar esse interpretador.

## Preparar o ambiente

Execute na raiz:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\ArmoredSpeech\setup_runtime.ps1
```

O script instala/reafirma as dependências do Speech no ambiente compartilhado. Não cria um ambiente virtual próprio do Speech. Se instalar dependências novas de RVC, mantenha as versões existentes congeladas e confira o inventário antes/depois.

## Validar o Speech

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_full_corpus.py
```

O validador procura vídeos em `storage` por padrão. Se necessário, defina `ARMORED_AUDIO_VALIDATION_VIDEO_ROOT`.

Na validação local de 16/10/2026, a suíte teve **45 aprovados e 1 ignorado** e a validação de áudio real passou em **5/5 casos**. Os hashes dos vídeos originais permaneceram inalterados. Ainda é necessário executar a validação completa do corpus para afirmar cobertura total.

## Validar RVC Melody

```powershell
.\.venv\Scripts\python.exe .\ArmoredStudio\processing\rvc.py .\audio_original.wav .\storage\validation\melody_output.wav melody
```

Use sempre um caminho de saída que ainda não exista e preserve os originais.

A conversão de amostra foi executada e escutada/aprovada em 16/10/2026 com o Python da raiz, modelo Melody e índice originais preservados.

## Ambiente antigo

O ambiente independente de RVC `ArmoredStudio/runtime/rvc/env311` não é mais necessário para execução. O código operacional resolve somente `.venv` na raiz. Sua pasta local pode ser apagada após atualizar o checkout para o commit que remove os últimos fallbacks, confirmando que `.venv` existe e repetindo o smoke test de imports.

Não apague modelos/índices Melody, vídeos, WAVs originais, assets, credenciais ou resultados de validação.
