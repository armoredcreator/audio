# ArmoredSpeech — proprietário do áudio

ArmoredSpeech é o único responsável pelas decisões e pela preparação de áudio. ArmoredStudio é responsável pela análise visual e renderização audiovisual final.

**Documentação operacional detalhada:** [Manual canônico do AudioLab](../docs/ARMORED_AUDIO_LAB_OPERATIONS.md).

Regras principais:
- O contrato ativo de render é Speech → Studio v5.
- Speech prepara e valida o lote inteiro, incluindo as variantes NO_BANNER e BANNER_PRESENT, antes de Studio iniciar.
- RVC Melody e o efeito ficam nos caminhos canônicos ArmoredSpeech/runtime/rvc/models e ArmoredSpeech/assets/efeitosonoro.wav.
- Não usar banco operacional nem fallback para ativos do Studio.
- Preservar originais, SQLite experimental, manifestos, pacotes e evidências em storage.

Comandos, configuração, testes, E2E e limpeza ficam no manual canônico; não replique as instruções em documentos paralelos.