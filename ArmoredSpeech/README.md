# ArmoredSpeech — laboratório isolado

Protótipo de classificação/preparação de áudio em lote. Esta pasta concentra o código, scripts, dependências, exemplos, testes e documentação específicos da ferramenta.

> Não integra nem altera o ArmoredCreator de produção. Os MP4s originais são somente leitura; vídeos e saídas locais não devem ser enviados ao Git.

## Evidência do experimento local

O usuário executou o modelo UVR-MDX-NET-Voc_FT.onnx em CPU em cinco vídeos. Os processos terminaram com código zero e os hashes dos MP4s permaneceram iguais. Na avaliação auditiva relatada:
- 1046: narração preservada;
- 12025_7ptrS9Gsg2: música separada, com narração restante;
- 291, 12008 e 178482: resultado ouvido como mudo/correto para os casos de teste relatados.

Isso é evidência limitada aos exemplos acima. **Não comprova a classificação automática nem aprova a integração em produção.**

## Posição no fluxo histórico

ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredSpeech completo → ArmoredIA → ArmoredStudio → ArmoredHub.

A ferramenta deve processar o lote aprovado inteiro antes da fase de IA/Studio/Hub, sem intercalar processamento item a item entre as fases.

## Contrato experimental

- WAV sidecar junto ao MP4, mesmo nome-base e extensão .wav.
- Identidade source_id + item_id e decisão registradas no SQLite experimental.
- Original preservado; SHA-256 antes/depois.
- Música, ausência de áudio, ruído sem narração útil e fala estrangeira não são elegíveis para RVC PT-BR.
- Classificação desconhecida ou falha bloqueia a elegibilidade para RVC.
- O WAV deve ser mantido até o Studio confirmar que terminou de utilizá-lo; falha no Studio não autoriza apagar o WAV.
- ArmoredSpeech não decide o destino do Hub e não publica no Telegram.

Veja o contrato em docs/ARMORED_SPEECH_CONTRACT.md, o plano de testes em docs/TEST_PLAN.md e a decisão de integração em docs/DECISION.md.

## Limite importante desta versão

O manifesto JSON opcional permite testar o contrato usando rótulos revisados; ele **não é um classificador automático**. O adaptador baseado em modelos existe no protótipo, mas a classificação precisa de benchmark reproduzível e avaliação auditiva antes de ser considerada confiável. O registro SQLite aqui é experimental e ainda não usa o schema real do ArmoredCreator.

## Ambiente

Use Python 3.11 em um ambiente virtual dedicado. Os comandos de preparação estão em docs/SETUP_WINDOWS.md.

Na raiz do repositório, instale o pacote em modo editável para que imports e testes encontrem a ferramenta:

    python -m pip install -e .
    python -m pip install -r ArmoredSpeech/requirements-analysis.txt
    python -m pip install -r ArmoredSpeech/requirements-separation.txt
    python -m pytest -q

Não execute RVC, renderização ou publicação no laboratório. Não aponte o lote para os vídeos de produção.
