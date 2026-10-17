# ArmoredSpeech — ambiente Python unificado no Windows

O AudioLab possui **um único ambiente operacional** na raiz do repositório: `.venv`. ArmoredSpeech, ArmoredStudio e RVC Melody devem usar esse interpretador.

## Preparar o ambiente

Execute na raiz:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\ArmoredSpeech\setup_runtime.ps1
```

O script instala o AudioLab em modo editável e reafirma as dependências do Speech no ambiente compartilhado. O `pyproject.toml` declara `opencv-python-headless==4.10.0.84`, necessário para importar a análise visual do Studio, sem depender do pacote GUI do OpenCV. Não cria um ambiente virtual próprio do Speech.

Se a `.venv` já existe e o único erro for `ModuleNotFoundError: No module named 'cv2'`, instale somente a versão declarada, sem resolver/alterar dependências transitivas:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps opencv-python-headless==4.10.0.84
.\.venv\Scripts\python.exe -c "import cv2, numpy; print('OpenCV:', cv2.__version__); print('NumPy:', numpy.__version__)"
```

Essa instalação deve adicionar apenas OpenCV; confira que NumPy continue em `1.23.5` e que Torch/TorchAudio mantenham suas versões validadas. Se for preparar um ambiente do zero, use o setup acima.

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
