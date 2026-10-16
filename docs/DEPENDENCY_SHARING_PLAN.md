# Plano incremental de compartilhamento de dependências

**Repositório/branch:** `armoredcreator/audio`, `test/armoredstudio-speech-integration-20261015`.

## Objetivo

Reduzir instalações Python duplicadas entre o ambiente principal do AudioLab, ArmoredSpeech e RVC/ArmoredStudio sem quebrar a conversão Melody já validada. Esta alteração não toca no ArmoredCreator de produção, em `armoredcreator-test` ou em `armoredcreator-testbase`.

## Evidência inicial fornecida pelo teste local (16/10/2026)

| Biblioteca | `.venv` raiz | RVC `env311` | Situação |
|---|---:|---:|---|
| Python | 3.11.9 | 3.11.9 | Compatível |
| NumPy (auditoria inicial) | 2.4.6 | 1.23.5 | Depois testado no `.venv` com 1.23.5 |
| PyTorch | 2.14.1 | 2.14.1+cpu | Aparente coincidência; conferir build CPU e dependências |
| SciPy | Ausente | 1.10.1 | Presente apenas no RVC |
| Fairseq | Ausente | 0.12.3.1 | Presente apenas no RVC; build validado localmente |
| rvc-python | Ausente | 0.1.5 | Presente apenas no RVC |
| librosa | Ausente | 0.11.0 | Presente apenas no RVC |
| numba | Ausente | 0.68.0 | Presente apenas no RVC |
| transformers | 4.57.6 | Ausente | Presente apenas na raiz |
| faster-whisper | 1.2.1 | Ausente | Presente apenas na raiz |
| silero-vad | 6.2.3 | Ausente | Presente apenas na raiz |
| onnxruntime | 1.31.0 | Ausente | Presente apenas na raiz |

O usuário depois testou NumPy 1.23.5 no `.venv` da raiz: 45 testes passaram (1 ignorado), `pip check` informou `No broken requirements found`, e a validação real de Speech passou em 5/5 vídeos com `ORIGINAL_HASHES_UNCHANGED=True`. Isso autoriza compartilhar a versão nos casos testados, mas não aprova ainda todo o corpus.

## Estratégia

1. Auditar as distribuições instaladas em `.venv`, `ArmoredSpeech/runtime/venv` (se existir) e `ArmoredStudio/runtime/rvc/env311` (se existir).
2. Comparar nome e versão normalizados. O sufixo `+cpu` só é normalizado para comparação de Torch/Torchaudio; isso não substitui um teste de importação/conversão.
3. Compartilhar apenas dependências com versões compatíveis e um mesmo interpretador. Manter os arquivos `requirements-*.txt` organizados por ferramenta, mesmo quando forem instalados no mesmo ambiente.
4. NumPy 1.23.5 já passou a suíte unitária e os cinco casos reais de Speech; mantê-lo no `.venv` enquanto preparamos o teste RVC. A conversão Melody ainda precisa ser executada pelo interpretador da raiz para concluir essa etapa.
5. Instalar dependências RVC na raiz e testar: imports, `pip check`, e conversão real do WAV de amostra, comparando o SHA-256 do original antes/depois.
6. Só após a conversão real pelo interpretador da raiz ser aprovada, remover o ambiente transitório `ArmoredStudio/runtime/rvc/env311`. Modelos e índices Melody permanecem em `ArmoredStudio/runtime/rvc/models`.

## Limites importantes

- Dois ambientes virtuais não compartilham automaticamente os pacotes um do outro. Se uma ferramenta continuar usando seu próprio ambiente, as distribuições presentes nos dois ambientes permanecem duplicadas.
- Não alterar NumPy, Torch ou Fairseq automaticamente apenas para produzir uma única versão.
- A distribuição local/validada de Fairseq 0.12.3.1 é diferente da versão solicitada nos metadados públicos do `rvc-python`; não deixar o resolvedor pip substituir silenciosamente esse pacote.
- `ArmoredSpeech/runtime/venv` não deve ser apagado sem confirmar que existe e que nenhuma rotina ativa depende dele.
- Não remover arquivos, modelos, índices, WAVs originais, assets, bancos de dados ou resultados deste processo.

## Auditoria

Executar na raiz do AudioLab:

```powershell
.\scripts\audit_shared_dependencies.ps1
```

O comando só consulta os ambientes. O CSV é gravado no diretório temporário do usuário; não altera pacotes nem apaga ambientes.
