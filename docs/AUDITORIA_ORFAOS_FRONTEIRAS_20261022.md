# Auditoria integrada de órfãos e fronteiras — 2026-10-22

## Escopo e limites

- Repositório: `armoredcreator/audio`
- Branch de auditoria: `tooling/orphan-audit-20261022`
- HEAD revisado antes deste relatório: `ac078bb9f0f9053721617c7f9ebac9f9a4550622`
- Esta auditoria é estática sobre o snapshot GitHub e não executa os arquivos de mídia privados da máquina local.
- Nenhuma alteração é feita no repositório operacional `armoredcreator/armoredcreator`, em banco de produção, credenciais, originais ou históricos.
- Não excluir módulos/dependências só porque faltam referências estáticas; verificar carregamento dinâmico, CLI e consumidores externos.

## Resultado da auditoria automatizada

Workflow: [Orphan audit smoke](https://github.com/armoredcreator/audio/actions/runs/39763385497)

- 160 arquivos varridos; 133 arquivos Python.
- 1 candidato Python sem referência estática.
- 0 dependências declaradas candidatas a não utilizadas.
- 0 duplicações exatas detectadas.
- 0 artefatos de backup detectados.
- 0 erros de parsing.
- A ferramenta é somente leitura por padrão; não apaga código automaticamente.

## Classificação do único candidato

`armored_core/production_contracts.py`

Define `SourceMessage`, `SourceAdapter` e `ProductionAdapterError`. Buscas por nome e inspeção dos pontos de composição não localizaram consumidor interno no snapshot. A fronteira operacional atualmente visível é definida por `armored_core.services` (`IngestMessage` e protocolos Vision/Studio/AI/Publisher), enquanto `Coordinator` compõe SyncService, Pipeline, Recovery e StartupReconciler. O contrato candidato não é importado por esses pontos.

**Decisão:** `UNREFERENCED_REVIEW` — candidato forte a órfão interno; mantido por segurança. A ausência de referências no repositório não prova ausência de consumidor externo, uso por reflexão ou uso por ferramenta de terceiros. Não remover sem decisão explícita e confirmação dos consumidores externos.

## Fronteiras de arquitetura inspecionadas

- **Composition root:** `armored_core/coordinator.py` instancia os serviços e o pipeline; não deve haver composição paralela em módulos de domínio.
- **Pipeline/state machine:** `armored_core/pipeline.py` consome contratos de `armored_core.services` e estados de `armored_core.models`.
- **Speech → Studio:** `ArmoredSpeech/studio_bridge.py` exporta um payload versionado e neutro; `ArmoredSpeech/studio_integration.py` prepara a diretiva de áudio; `ArmoredStudio/service.py`/ `unified.py` recebem o payload e mantêm a análise visual/render no Studio.
- **Execução de laboratório:** `scripts/run_lab_pipeline.py` declara não ler histórico Telegram, não abrir o banco operacional, criar SQLite novo sob `storage/validation`, desabilitar publicação/Hub e preservar artefatos em falha.
- **Dependências:** o CI instala o projeto em Python 3.11 e executa compilação/importação e testes; RVC depende de runtime separado e precisa de evidência específica para ser considerado validado.

## Evidência automatizada atual

- [Suíte isolada ArmoredSpeech](https://github.com/armoredcreator/audio/actions/runs/39763385440): sucesso; 303 passaram, 1 foi ignorado por dependência/runtime opcional.
- [Auditoria de órfãos](https://github.com/armoredcreator/audio/actions/runs/39763385497): sucesso; relatório acima.
- O teste ignorado não comprova execução real do RVC. Sucesso de CI não equivale a E2E de mídia.

## Evidência histórica de E2E (não reexecutada neste commit)

O manual canônico `docs/ARMORED_AUDIO_LAB_OPERATIONS.md` registra execuções locais anteriores: corpus consolidado de 17 vídeos (17/17 pós-validados) e caso real com banner. Esses resultados são evidência histórica documentada, não uma nova execução deste commit; dependem de arquivos e relatórios locais que não estão necessariamente no GitHub.

## Portões que continuam abertos

1. Confirmar no ambiente local, com corpus privado e runtime RVC real, que a execução integrada atual passa; o GitHub CI sem esse corpus não substitui isso.
2. Confirmar visual/auditivamente os outputs da execução atual quando houver alterações relevantes no render.
3. Não executar Sync/Vision/Stock operacionais, não conectar credenciais nem publicar durante esta auditoria.
4. Não integrar/mesclar esta branch de auditoria à branch operacional. PR #18 continua em rascunho e isolada.

## Conclusão

A auditoria estática do snapshot passou com zero parse errors, zero duplicações exatas, zero backups e zero dependências candidatas. Há um único módulo a revisar, classificado acima e preservado. Os testes automatizados atuais passaram. A aprovação de execução real de ponta a ponta com mídia privada e RVC no HEAD atual permanece **não comprovada por esta execução de GitHub**; não será marcada como aprovada por inferência.
