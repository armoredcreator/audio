# Decisão de arquitetura — timeline de áudio e fronteira Speech → Studio

A decisão vigente e implementada é: **ArmoredSpeech decide, prepara e valida o áudio; ArmoredStudio analisa o visual e renderiza o audiovisual final**. Studio seleciona uma timeline preparada por Speech com base no plano visual; não define rota, volume, fade ou grafo de áudio.

O contrato de integração ativo é v5. Speech prepara ambas as variantes temporais por item, NO_BANNER e BANNER_PRESENT, e valida o pacote de todo o lote antes de qualquer análise do Studio. O render final combina os grafos fornecidos por cada domínio em uma única invocação FFmpeg por vídeo. Operações preparatórias de WAV/RVC continuam permitidas no Speech.

## Evidência real atual

- Corpus consolidado com 17 vídeos: 17/17 pós-validados, timeline NO_BANNER em todos, 9 rotas RVC_MELODY e 8 EFFECT_ONLY; H.264 1080×1920, AAC estéreo 44.100 Hz, decodificação integral e hashes originais OK.
- Caso com banner real: storage/original_018.mp4 gerou banner_detectado=true e selecionou BANNER_PRESENT; a rota de áudio continuou RVC_MELODY. A saída final passou validação de mídia/decodificação e o usuário confirmou a inspeção visual positiva.
- O relatório, os comandos, a matriz de ownership e a lista de critérios que ainda ficam fora do laboratório estão no [Manual canônico](ARMORED_AUDIO_LAB_OPERATIONS.md).

## Limites

O sucesso de laboratório não autoriza merge operacional. A integração no repositório do ArmoredCreator requer branch separada, auditoria do Coordinator e schema SQLite, testes isolados próprios e aprovação explícita antes de qualquer publicação no Hub. O estado anterior de “BANNER_PRESENT somente automatizado” está superado pela evidência real da seção canônica.