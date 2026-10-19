# ArmoredSpeech — proprietário do áudio

ArmoredSpeech é o único responsável pelas decisões e pela preparação de áudio. ArmoredStudio é responsável pela análise visual e renderização audiovisual final.

**Documentação operacional detalhada:** [Manual canônico do AudioLab](../docs/ARMORED_AUDIO_LAB_OPERATIONS.md).

Regras principais:
- O contrato ativo de render é Speech → Studio v5.
- Speech prepara e valida o lote inteiro, incluindo as variantes NO_BANNER e BANNER_PRESENT, antes de Studio iniciar.
- RVC Melody e o efeito ficam nos caminhos canônicos ArmoredSpeech/runtime/rvc/models e ArmoredSpeech/assets/efeitosonoro.wav.
- Hashes de arquivo são calculados por armored_core.integrity; o digest canônico do plano visual é compartilhado com Studio sem confundir esse hash com o SHA-256 do grafo de áudio ou o Git blob SHA-1 dos assets.
- Validação de limites de caminhos e opções para subprocessos Windows usam helpers compartilhados em armored_core.
- Não usar banco operacional nem fallback para ativos do Studio.
- Preservar originais, SQLite experimental, manifestos, pacotes e evidências em storage.

No commit validado nesta rodada, o CI passou a compilação e 127 testes. Comandos, configuração, testes, E2E e segurança ficam no manual canônico; não replique instruções em documentos paralelos.
