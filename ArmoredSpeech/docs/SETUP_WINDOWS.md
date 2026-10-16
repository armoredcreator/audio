# ArmoredSpeech — ambiente Python compartilhado no Windows

O AudioLab utiliza um único ambiente virtual na raiz: `.venv`. ArmoredSpeech, ArmoredStudio e, após a migração validada, RVC devem reutilizar esse interpretador sempre que as versões das dependências forem compatíveis.

## Preparar ou atualizar o ambiente

Execute na raiz do repositório:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\ArmoredSpeech\setup_runtime.ps1
```

O script é um ponto de entrada compatível, mas instala as dependências no `.venv` da raiz. Ele não cria nem instala pacotes em `ArmoredSpeech/runtime/venv`. Para instalar também a separação vocal opcional, execute ` .\ArmoredSpeech\setup_runtime.ps1 -InstallSeparation`; esse pacote pode exigir espaço e tempo de instalação consideráveis.

As dependências de análise ficam declaradas em `ArmoredSpeech/requirements-analysis.txt`, mas são instaladas no ambiente compartilhado. O NumPy está fixado em `1.23.5`, que passou os cinco casos de referência reais disponíveis nesta etapa; uma validação completa do corpus continua necessária.

## Validar o corpus de áudio

```powershell
$SpeechPython = ".\.venv\Scripts\python.exe"
& $SpeechPython .\ArmoredSpeech\tests\validate_full_corpus.py
```

O validador procura vídeos sob `storage` na raiz do laboratório por padrão. Se necessário, defina `ARMORED_AUDIO_VALIDATION_VIDEO_ROOT` explicitamente.

## Estado e limites

- Cinco arquivos de referência passaram com NumPy 1.23.5 e os hashes dos MP4s permaneceram inalterados.
- Os outros arquivos continuam sujeitos a revisão; não assumir que todo o corpus está aprovado.
- Testes de contrato sintéticos não substituem renderização real pelo ArmoredStudio.
- Não executar Hub ou publicação nessa validação.

## Ambiente legado

`ArmoredSpeech/runtime/venv` é legado. O setup atual não o utiliza nem o remove automaticamente. Depois de confirmar que todos os comandos locais usam `.venv`, esse ambiente duplicado poderá ser removido manualmente. Não apague modelos, vídeos, resultados, assets ou bancos locais.
