# ArmoredCreator Audio Lab

Laboratório isolado para validar ArmoredSpeech e ArmoredStudio antes de qualquer integração no ArmoredCreator.

- Implementação e documentação do Speech: [ArmoredSpeech/](ArmoredSpeech/README.md).
- Status de portabilidade do Studio: [docs/ARMOREDSTUDIO_PORT_STATUS.md](docs/ARMOREDSTUDIO_PORT_STATUS.md).
- Plano e evidências da unificação de dependências: [docs/DEPENDENCY_SHARING_PLAN.md](docs/DEPENDENCY_SHARING_PLAN.md).
- **Um único ambiente operacional:** `.venv` na raiz para ArmoredSpeech, ArmoredStudio e RVC Melody.
- O executor RVC usa somente o Python compartilhado da raiz; não depende de `runtime/rvc/env311`.
- O runner experimental `python -m ArmoredSpeech.studio_batch_runner` faz o handoff do lote Speech completo e aprovado para a fronteira pública do Studio; veja [o contrato do runner](ArmoredSpeech/docs/STUDIO_BATCH_RUNNER.md).
- Vídeos, ambientes virtuais, bancos locais, credenciais, assets privados e resultados gerados não devem ser enviados ao Git sem aprovação explícita.

## Ordem oficial do pipeline

**ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub.**

Sync, Vision e Stock devem concluir suas fases históricas na ordem acima. IA vem antes de Speech; Speech deve concluir o lote aprovado antes de Studio. Hub é a última etapa e não participa dos testes isolados atuais.

## Corpus local

O corpus fica em `C:\Users\Administrador\Downloads\ArmoredAudioLab\storage`. Os testes devem preservar os originais, usar saídas separadas e registrar os resultados por caso. Não presumir que todo o corpus foi aprovado só porque os casos de referência passaram.

## Estado da validação (16/10/2026)

- Speech: 45 testes aprovados, 1 ignorado.
- Speech real: 5/5 vídeos de referência passaram; hashes originais intactos.
- RVC: imports de Fairseq e RVCInference passaram no ambiente raiz.
- Melody: conversão real executada pelo Python da raiz, arquivo de saída não vazio e áudio escutado/aprovado pelo usuário; original, modelo e índice permaneceram inalterados.
- Speech + RVC: auditoria read-only do ambiente unificado passou; a suíte local anterior teve 63 testes. Depois do runner, a CI desta branch passou com compilação e **67 testes aprovados**; o teste copy-only validou o serviço/handoff sintético, não renderização real de vídeo.

A validação completa do corpus de Studio e o pipeline de publicação continuam fora do escopo desta aprovação isolada. Não publicar no Hub ou promover mudanças ao ArmoredCreator de produção sem auditoria e aprovação.
