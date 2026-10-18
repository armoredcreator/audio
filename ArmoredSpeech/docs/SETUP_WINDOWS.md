# ArmoredAudioLab — configuração Windows/Python 3.11

O laboratório usa um único ambiente Python: `.venv` na raiz. Speech, Studio visual e RVC Melody usam o mesmo interpretador.

## 1. Preparar o ambiente

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\ArmoredSpeech\setup_runtime.ps1
```

O setup não cria um ambiente próprio do RVC. Não recrie nem troque a `.venv` testada sem necessidade.

## 2. Provisionar e conferir os ativos canônicos do Speech

Depois de atualizar a branch, execute:

```powershell
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
```

Os modelos/índices RVC e o efeito sonoro são binários locais e não fazem parte do Git. Os caminhos canônicos são `ArmoredSpeech/runtime/rvc/models` e `ArmoredSpeech/assets/efeitosonoro.wav`; o Studio não serve como fallback. O script de migração é compatível com dois cenários: copia e confere SHA-256 se os ativos antigos do Studio existirem, ou confirma os ativos já instalados no Speech se a origem antiga não existir. Se não houver ativos em nenhum caminho, ele falha sem criar nem apagar arquivos. Restaure os arquivos de um backup confiável diretamente nos caminhos canônicos do Speech.

## 3. Conferir os caminhos

```powershell
.\.venv\Scripts\python.exe -c "from ArmoredSpeech import rvc_engine as r; print('Python:', r.resolver_rvc_python()); print('RVC root:', r.RVC_ROOT); print('Models:', r.MODELS_DIR)"
Test-Path .\ArmoredSpeech\runtime\rvc\models\melody
Test-Path .\ArmoredSpeech\assets\efeitosonoro.wav
```

O diretório Melody deve conter ao menos um `.pth`; o índice `.index` é opcional. O motor RVC não lê `ARMORED_RVC_ROOT`: a raiz de execução é fixa em `ArmoredSpeech/runtime/rvc`. Não configure fallback para `ArmoredStudio/runtime/rvc`.

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

O código ativo do Studio não usa RVC nem procura modelos/efeitos na árvore antiga. No checkout Windows desta validação, as 14 cópias RVC e o efeito antigo do Studio foram comparados por SHA-256 com os ativos do Speech; só depois da confirmação dos E2Es foram removidas essas duplicatas locais. Não remova os modelos/índices canônicos do Speech, o efeito do Speech, os originais ou os dados de `storage/`.
