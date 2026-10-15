# ArmoredCreator Audio Lab

Laboratório isolado para validar separação de áudio e desenvolver o contrato experimental do ArmoredSpeech.

> Este repositório não integra nem altera o ArmoredCreator de produção. Os originais são somente leitura; nenhum vídeo de teste deve ser enviado ao Git.

## Evidência do experimento local

O usuário executou em `C:\Users\Administrador\Downloads\ArmoredAudioLab` o modelo `UVR-MDX-NET-Voc_FT.onnx` em CPU em cinco vídeos. O código de saída foi zero e os SHA-256 dos MP4s permaneceram iguais. Na avaliação auditiva relatada pelo usuário:
- `1046`: narração preservada;
- `12025_7ptrS9Gsg2`: música separada, narração restante;
- `291`, `12008` e `178482`: resultado ouvido como mudo/correto para os casos de teste relatados.

Isto é evidência de que o separador funcionou nesses exemplos específicos. **Não comprova ainda a classificação automática nem aprova integração em produção.**

## ArmoredSpeech — contrato e lote

Fluxo histórico planejado:

`ArmoredSync` completo → `ArmoredVision` completo → `ArmoredStock` completo → **ArmoredSpeech completo** → ArmoredIA → ArmoredStudio → ArmoredHub.

O protótipo:
- mantém o WAV junto ao MP4, com o mesmo nome-base e extensão `.wav`;
- mantém a identidade `source_id + item_id` no SQLite;
- registra classe de áudio, idioma, elegibilidade para RVC, estado, hashes e erros;
- processa por fonte/item em ordem estável;
- nunca altera nem exclui o MP4 original;
- deixa música/sem áudio/ruído/fala estrangeira inelegíveis para RVC PT-BR;
- preserva o WAV até o Studio confirmar que terminou de utilizá-lo.

Veja [o contrato detalhado](docs/ARMORED_SPEECH_CONTRACT.md).

### Limite importante desta versão

O runner usa um manifesto JSON de decisões revisadas para testar o contrato e o fluxo de arquivos. Ele **não é ainda um classificador automático** de fala PT-BR, música, ruído e silêncio. Classificação automática e integração com o SQLite/schema de produção são etapas explícitas antes de portar o código.

## Ambiente de teste

- Windows e Python 3.11 em ambiente virtual dedicado.
- CPU por padrão, sem depender de GPU.
- Nunca executar RVC, renderização ou publicação durante o teste isolado.
- Registrar tempo, uso aproximado de RAM, versão/modelo e avaliação auditiva.
- Falhas ou classificação desconhecida não liberam áudio para RVC.

## Arquivos principais

- `armoredspeech/`: contratos, runner por fonte, registro SQLite e provider experimental.
- `scripts/separate_one.py`: separação isolada de um clipe com hashes antes/depois.
- `scripts/run_armoredspeech.py`: execução em lote usando itens e decisões JSON.
- `docs/ARMORED_SPEECH_CONTRACT.md`: contrato de handoff para o Studio.
- `docs/TEST_PLAN.md`: plano de benchmark/escuta.
- `docs/DECISION.md`: estado de aprovação.
