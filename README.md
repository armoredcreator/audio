# ArmoredCreator Audio Lab

Laboratório isolado para validar separação de áudio e desenvolver o protótipo ArmoredSpeech.

- Implementação e documentação específicas da ferramenta: [ArmoredSpeech/](ArmoredSpeech/README.md).
- Esta branch de organização parte do experimento isolado; não integra nem altera o ArmoredCreator de produção.
- Vídeos, ambientes virtuais, bancos de dados locais e resultados gerados não devem ser enviados ao Git.

## Estado

A separação UVR/MDX-Net foi testada pelo usuário em cinco exemplos locais. Isso não aprova o classificador automático nem a integração em produção.

Antes de portar ArmoredSpeech para o repositório principal, ainda é necessário validar classificação automática, retomada após falhas, custo do lote, persistência no SQLite real e contrato com ArmoredStudio.

## Organização

O código, scripts, dependências, exemplos, testes e documentação próprios da ferramenta ficam dentro de ArmoredSpeech/. A configuração do CI permanece em .github/workflows/, como convenção do GitHub.

Sequência histórica prevista:

ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredSpeech completo → ArmoredIA → ArmoredStudio → ArmoredHub.

Nada nesta branch ativa processamento ou publicação em produção.
