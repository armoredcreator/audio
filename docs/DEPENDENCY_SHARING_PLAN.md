# Ambiente Python único do ArmoredAudioLab

**Branch de trabalho:** `test/armoredstudio-speech-integration-20261015`

## Decisão final

O ambiente operacional único é `.venv` na raiz do repositório. ArmoredSpeech, ArmoredStudio e RVC Melody devem executar com este interpretador:

`C:\Users\Administrador\Downloads\ArmoredAudioLab\.venv\Scripts\python.exe`

O RVC não deve criar nem selecionar `ArmoredStudio/runtime/rvc/env311` ou o ambiente RVC legado. O executor `ArmoredStudio/processing/rvc.py` resolve somente o ambiente compartilhado na raiz. O script antigo de migração para `env311` foi aposentado/removido deste branch para evitar recriá-lo por engano.

## Validações locais concluídas em 16/10/2026

- Python compartilhado: 3.11.9.
- Speech unit tests: **45 passed, 1 skipped**.
- Validação real de Speech: **5/5 arquivos passaram**; hashes dos vídeos originais inalterados.
- RVC/Fairseq: import real passou com `fairseq 0.12.3.1`, `torch 2.14.1+cpu` em runtime e `torchaudio 2.11.0+cpu`.
- Dependências de RVC adicionadas à raiz nas versões registradas no ambiente de referência, usando instalação sem resolução transitiva. A comparação informou que os pacotes existentes na raiz não foram removidos nem alterados durante essa instalação.
- Conversão real com o modelo `melody.pth` e índice `added_IVF890_Flat_nprobe_1_melody_v2.index` foi executada pelo Python da raiz. O usuário escutou e aprovou o resultado.
- O áudio de entrada, o modelo, o índice e o inventário do antigo ambiente RVC permaneceram inalterados durante essa validação.

Esses resultados comprovam a conversão de amostra e os casos reais de Speech informados. Não significam que todo o corpus de Studio ou todo o pipeline de publicação foi testado.

## Operação

No PowerShell, a partir da raiz do AudioLab:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\audit_shared_dependencies.ps1
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_real_audio.py
```

Para converter um WAV com Melody:

```powershell
.\.venv\Scripts\python.exe .\ArmoredStudio\processing\rvc.py .\audio_original.wav .\storage\validation\melody_output.wav melody
```

Escolha sempre uma saída nova para não sobrescrever os originais.

## Regras de dependências

- Não usar `pip install --upgrade` para o Torch, TorchAudio, NumPy ou Fairseq.
- O Fairseq usado é a distribuição local validada `0.12.3.1`; não deixar um resolvedor substituir por uma versão upstream.
- Instalações novas devem fixar versões, usar `--no-deps` quando necessário e conferir `pip freeze` antes/depois.
- `pip` e `setuptools` são ferramentas do ambiente; suas versões/metadados diferiam da referência, mas não foram substituídas na unificação. O Torch runtime foi confirmado como `2.14.1+cpu` nos dois ambientes.
- A validação completa do corpus, a integração completa do Studio e o pipeline de produção continuam sujeitos aos seus próprios testes; não executar Hub/publicação como parte dessa validação.

## Dados e arquivos

Não apagar modelos ou índices sob `ArmoredStudio/runtime/rvc/models`, vídeos, WAVs originais, assets, credenciais ou resultados de teste ao limpar ambientes. O único ambiente operacional é `.venv` na raiz.
