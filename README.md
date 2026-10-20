# ArmoredCreator Audio Lab

Laboratório isolado que valida a separação entre ArmoredSpeech (áudio) e ArmoredStudio (análise visual e render audiovisual final) antes de qualquer integração operacional.

## Fonte canônica de informação

**[Manual canônico do ArmoredAudioLab](docs/ARMORED_AUDIO_LAB_OPERATIONS.md)**

O manual reúne a arquitetura, ownership por módulo, contrato ativo Speech → Studio v5, sequenciamento de fases, matriz de rotas, instalação/ativos, comandos de verificação, segurança, evidências e critérios ainda pendentes. Os demais documentos técnicos são índices ou referências históricas explícitas; não devem conservar um estado divergente.

## Estado de validação

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