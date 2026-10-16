# ArmoredCreator Audio Lab

Laboratório isolado para validar ArmoredSpeech e ArmoredStudio de ponta a ponta antes de qualquer integração no ArmoredCreator.

- Implementação e documentação de Speech: [ArmoredSpeech/](ArmoredSpeech/README.md).
- Status de portabilidade e integração de Studio: [docs/ARMOREDSTUDIO_PORT_STATUS.md](docs/ARMOREDSTUDIO_PORT_STATUS.md).
- Esta branch de teste não integra nem altera o ArmoredCreator de produção, `armoredcreator-test` ou `armoredcreator-testbase`.
- Plano para reduzir duplicações Python com segurança: [docs/DEPENDENCY_SHARING_PLAN.md](docs/DEPENDENCY_SHARING_PLAN.md). A auditoria inicial é somente leitura.
- Vídeos de teste, ambientes virtuais, bancos de dados locais, credenciais, assets privados e resultados gerados não devem ser enviados ao Git sem uma decisão explícita.

## Ordem oficial do pipeline

**ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub.**

Sync, Vision e Stock devem concluir suas fases históricas na ordem acima. IA vem antes de Speech; Speech deve concluir seu lote aprovado antes do início do processamento de Studio. Hub é a última etapa e não faz parte dos testes isolados atuais.

## Corpus local para testes Speech + Studio

O corpus de teste preparado pelo usuário fica em:

`C:\Users\Administrador\Downloads\ArmoredAudioLab\videos`

Ele contém vídeos destinados tanto aos testes de ArmoredSpeech quanto aos testes visuais de ArmoredStudio: VEO 1, VEO 2, Gemini e barras pretas de diferentes tamanhos. O diretório é local e não deve ser versionado. Os testes devem usar cópias/workspace isolados, preservar os originais e registrar resultados por caso; não presumir que todos os casos já passaram.

Os assets oficiais do Studio e o ambiente/modelo/índice RVC serão transferidos para o laboratório separadamente pelo usuário. Até essa transferência e validação, não declarar o teste real de ponta a ponta concluído.

## Estado

O CI mais recente validou testes automatizados sintéticos; isso não equivale a renderização real. A integração Speech → Studio ainda precisa ser ligada ao chamador/orquestrador do laboratório e validada com o corpus local, os assets oficiais e RVC.

Nenhum vídeo real foi processado nesta etapa. Nenhuma publicação foi executada. Não promover nem copiar alterações para o ArmoredCreator de produção sem auditoria e aprovação explícita.
