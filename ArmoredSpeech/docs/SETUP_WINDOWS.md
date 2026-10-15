# ArmoredSpeech — ambiente isolado no Windows

O ArmoredSpeech mantém seu ambiente Python próprio em \`ArmoredSpeech/runtime/venv\`. Ele não deve reutilizar a \`.venv\` da raiz nem o ambiente RVC do ArmoredStudio.

## Preparar ou atualizar o ambiente

Execute na raiz do repositório:

\`\`\`powershell
.\ArmoredSpeech\setup_runtime.ps1
\`\`\`

O script cria o ambiente com Python 3.11, instala o pacote do laboratório e as dependências de análise apenas dentro de \`ArmoredSpeech/runtime/venv\`. O ambiente, caches e modelos são locais e não devem ser enviados ao Git.

## Validar o corpus de áudio

\`\`\`powershell
$SpeechPython = ".\ArmoredSpeech\runtime\venv\Scripts\python.exe"
& $SpeechPython .\ArmoredSpeech\tests\validate_full_corpus.py
\`\`\`

Por padrão, o validador procura MP4s em \`storage\` na raiz do laboratório. Confira \`CORPUS_COUNT=15\`. O primeiro uso pode baixar modelos e consumir CPU; não execute se o disco estiver quase cheio.

## Limites da validação

- Cinco arquivos com referências conhecidas são comparados com rótulos e hashes registrados no teste.
- Os outros arquivos são classificados, mas permanecem como \`REVIEW\` até confirmação humana.
- \`ORIGINAL_HASHES_UNCHANGED=True\` confirma que os vídeos não foram modificados durante aquela execução.
- Um relatório sem erro não prova integração com o ArmoredStudio. O consumidor/handoff do Studio ainda precisa ser implementado e testado separadamente.
- Não executar RVC, renderização do Studio, Hub ou publicação nesta validação.

## Limpeza

Para remover o ambiente privado, feche os processos Python e exclua apenas \`ArmoredSpeech/runtime/venv\`. Não apague \`ArmoredStudio/runtime/rvc\`, os vídeos de \`storage\`, nem saídas de outros módulos.
