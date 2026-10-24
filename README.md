# ArmoredCreator Audio Lab

Laboratório isolado que valida a separação entre ArmoredSpeech (áudio) e ArmoredStudio (análise visual e render audiovisual final) antes de qualquer integração operacional.

## Fonte canônica de informação

**[Manual canônico do ArmoredAudioLab](docs/ARMORED_AUDIO_LAB_OPERATIONS.md)**

O manual reúne a arquitetura, ownership por módulo, contrato ativo Speech → Studio v5, sequenciamento de fases, matriz de rotas, instalação/ativos, comandos de verificação, segurança, evidências e critérios ainda pendentes. Os demais documentos técnicos são índices ou referências históricas explícitas; não devem conservar um estado divergente.

## Estado de validação

### Candidato de integração em continuação — PR #19

O alvo é Python **3.11.9 unificado**, com instalação isolada reproduzível
descrita em [PYTHON311_RUNTIME](docs/PYTHON311_RUNTIME.md). O pacote RVC de
compatibilidade é um fork explícito; modelos Melody e ambientes existentes
permanecem preservados.

O provedor padrão de legenda agora é **Ollama local**, modelo `gemma3:1b`,
configurável por `ARMORED_IA_OLLAMA_MODEL`. Gemini é opcional e só é selecionado
explicitamente. O adaptador não baixa modelos nem inventa legendas/hashtags:
campos gerados pelo modelo passam pela política comum. Consulte `.env.example`.

O contrato completo separa geração criativa, normalização apenas de formato,
Policy contextual estrita, persistência de candidatas e recuperação auditável de
legendas `BLOCKED`: [Contrato de legendas ArmoredIA](docs/ARMOREDIA_CAPTION_CONTRACT.md).

O Hub está incluído no pacote e na composição do Coordinator. Validações usam
transporte simulado e banco isolado; `ARMORED_HUB_DRY_RUN=1` bloqueia envio e
verificação externa antes de qualquer alteração de publicação. A aprovação
dos testes não autoriza publicar em canais reais.

### Inicialização do Coordinator no Windows

O pacote instala o comando `armoredcreator` e também fornece `run_coordinator.py`.
Configure as três fontes e seus três destinos Hub em `.env`; guarde os valores
de autenticação somente em `credentials/project.env`. A validação abaixo não
abre SQLite nem conexão Telegram e oculta os valores sensíveis:

```powershell
& .\.venv\Scripts\python.exe .\run_coordinator.py --root . --validate-config
```

Para iniciar a esteira, o Coordinator recupera estados, conclui CATCH-UP e só
então entra em LIVE. Sem `--publish`, o Hub permanece em dry-run como padrão:

```powershell
& .\.venv\Scripts\python.exe .\run_coordinator.py --root . --run
```

O envio real exige a opção explícita `--publish`. Não use essa opção para
validação local; as rotas e as credenciais reais devem ser revisadas pelo
operador antes de iniciar a operação.


Para um envio **real e explicitamente autorizado** em tópico de teste, use
somente uma cópia isolada da configuração e configure **a rota efetiva** para
o chat/tópico de teste, além de definir
`ARMORED_HUB_TEST_CHAT_ID` e `ARMORED_HUB_TEST_TOPIC_ID` com os mesmos
identificadores. A trava rejeita env incompleto, rota operacional ou destino
pré-existente diferente **antes** de criar uma intenção de publicação ou abrir
o transporte Telegram. Ela **não redireciona** rotas e não supera
`ARMORED_HUB_DRY_RUN=1`; para envio real autorizado essa variável deve
estar desativada. Não alterar rotas operacionais nem reaproveitar registros
de publicação de produção apenas para testar.


Uma amostra histórica isolada concluiu IA local → Speech → Studio e retomada
após interrupção com Hub simulado, mantendo o render e o original. Isso não
comprova descoberta Telegram e consulta Shopee atuais: a aprovação Vision
histórica foi reaproveitada. Recuperação dos originais WAITING_VISION, revisão
manual restante e autorização operacional continuam gates separados.

### Evidência histórica anterior do laboratório

Código testado de referência: commit 23b398d8150a79bb59299733c5340db307b4119c, branch de laboratório test/armoredstudio-speech-integration-20261015.

- **Testes locais:** 127 testes aprovados; compileall, imports e auditoria estática de ownership passaram.
- **CI:** workflows de teste e diagnósticos de áudio passaram na consolidação de código. O workflow isolado também passou depois da atualização documental da PR #8.
- **E2E do corpus completo:** 17/17 vídeos renderizados e decodificados; H.264 1080×1920 + AAC estéreo 44.100 Hz; hashes dos originais preservados. Distribuição observada: 9 RVC_MELODY e 8 EFFECT_ONLY; todos NO_BANNER.
- **E2E real BANNER_PRESENT:** storage/original_018.mp4 ativou banner_detectado=true, e o Studio selecionou BANNER_PRESENT mantendo a rota RVC_MELODY. O arquivo final passou a validação de streams e decodificação; o usuário assistiu ao resultado e confirmou que ficou perfeito.
- **Contrato ativo:** v5. Speech prepara o lote completo e as duas variantes temporais; Studio só começa depois do preflight de todo o lote.
- **Isolamento:** não utiliza o SQLite operacional, não reexecuta Sync/Vision/Stock/IA, não publica no Hub e não integra automaticamente código no ArmoredCreator.

Os artefatos E2E (bancos, manifestos, pacotes, relatórios, originais e saídas) ficam em storage local e são preservados; não são enviados ao Git. Não repetir os lotes aprovados apenas para reconstruir a evidência. Consulte o manual para os caminhos exatos e a pós-validação.

## Regra de arquitetura

**ArmoredSpeech é o dono de todas as decisões e do processamento de áudio. ArmoredStudio é dono da análise visual, da seleção de uma variante temporal previamente criada por Speech e do render audiovisual final.** armored_core contém helpers técnicos compartilhados, não regras paralelas de negócio.

A próxima integração no repositório operacional exige uma branch separada, auditoria do Coordinator/schema SQLite e testes isolados próprios. A aprovação do AudioLab não autoriza publicação em produção.
