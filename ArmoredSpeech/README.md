# ArmoredSpeech — proprietário do áudio

ArmoredSpeech é o único responsável pelas decisões e pela preparação de áudio. ArmoredStudio é responsável pela análise visual e pela renderização audiovisual final.

**Documentação operacional detalhada:** [Manual canônico do AudioLab](../docs/ARMORED_AUDIO_LAB_OPERATIONS.md).

## Contrato e fronteira de responsabilidade

- O contrato de render ativo é Speech → Studio v5.
- Speech precisa concluir e validar o lote inteiro antes da primeira análise/renderização de Studio.
- Speech prepara as faixas de voz/efeito, RVC Melody quando elegível, separação vocal quando necessária, sincronização, trims, fades, mixagem e ambas as variantes temporais NO_BANNER e BANNER_PRESENT.
- Studio valida estruturalmente o pacote, analisa somente o vídeo, escolhe uma variante preexistente e junta os grafos visual e de áudio no render final. Não decide rotas de áudio nem executa RVC.
- O efeito sonoro canônico fica em ArmoredSpeech/assets/efeitosonoro.wav. O RVC e seus modelos/índices ficam sob ArmoredSpeech/runtime/rvc. Não existe fallback de áudio operacional para ArmoredStudio.
- armored_core centraliza helpers técnicos compartilhados para integridade, contenção de paths e utilitários de subprocessos. O nome do helper não altera o ownership de negócio de Speech ou Studio.

## Evidência atual

- A suíte local passou com 127 testes; compileall, imports e auditoria estática de ownership também passaram.
- O CI da consolidação de código passou em teste isolado e diagnóstico de áudio. Após a atualização anterior de documentação, o workflow de teste também passou no commit 23b398d8150a79bb59299733c5340db307b4119c.
- O E2E de corpus da versão consolidada processou 17/17 itens. A pós-validação confirmou H.264 1080×1920, AAC 44.100 Hz estéreo, decodificação integral e integridade dos originais. Rotas: 9 RVC_MELODY e 8 EFFECT_ONLY; todos NO_BANNER.
- O E2E real de banner com storage/original_018.mp4 selecionou BANNER_PRESENT e RVC_MELODY. A saída passou ffprobe e decodificação completa, preservou os originais e foi aprovada visualmente pelo usuário.

Caminhos de todos os manifestos/relatórios, saída de cada teste, comandos e ressalvas estão no manual canônico. Esses artefatos são locais e não devem ser versionados.

## Segurança

Não usar storage/database/armoredcreator.db; não reexecutar Sync/Vision/Stock/IA/Hub; não apagar storage, .venv, modelos/índices, efeito, banner, originais, SQLite experimentais, manifestos, pacotes ou relatórios. O sucesso de laboratório não autoriza merge no repositório operacional.