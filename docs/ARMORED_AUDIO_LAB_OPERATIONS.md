# ArmoredAudioLab — manual canônico de arquitetura, operação e limpeza

**Versão:** 1.1 | **Revisão:** 19/10/2026  
**Repositório:** armoredcreator/audio  
**Branch de laboratório atual:** `test/armoredstudio-speech-integration-20261015`  
**Último merge de consolidação (PR #7):** `3e6969eeb4c42d747989fc3d865f1edfd72a92b6`  
**Contrato ativo Speech → Studio:** v5

Este é o documento único de referência operacional detalhada. O README da raiz é um índice. Os documentos técnicos anteriores são atalhos para este manual; o relatório de áudio de 15/10 e o README de amostras continuam auxiliares/históricos.

---

## 1. Estado atual e evidências

### Histórico e validação atual

- A PR #4 adicionou o runner explícito de lote Speech → Studio e foi mesclada na branch de integração do laboratório; commit 5d0a51b254bb284a25aec48435f1513c337f12e6.
- A PR #5 estabeleceu o contrato v5, a ordem lote-completo Speech antes de Studio e os dois E2Es focais descritos abaixo; seu trabalho também foi integrado à árvore de laboratório.
- A PR #6 unificou a preparação WAV e o cálculo de SHA-256 em arquivo. O commit `f919225aa755b9c0a198110d789e511b091d8583` pertence à branch feature `feature/speech-studio-batch-runner-20261016` e permanece como referência histórica; as alterações correspondentes já estavam na árvore do laboratório antes da PR #7. A contagem de 112 testes é histórica, não representa a versão atual.
- Nesta rodada, a auditoria encontrou duplicações comprovadas na materialização dos assets oficiais, no cálculo de hash canônico do plano visual, na validação de limites de caminhos e na configuração de subprocessos no Windows. Esses helpers foram centralizados em armored_core, mantendo os wrappers públicos já usados pelo restante do código.
- O workflow do CI agora observa alterações em ArmoredSpeech/**, ArmoredStudio/**, armored_core/**, scripts/** e nos arquivos de configuração/documentação listados no próprio workflow, tanto em push como em pull request.
- No commit de consolidação atual `3e6969eeb4c42d747989fc3d865f1edfd72a92b6`, os dois workflows do GitHub Actions concluíram com sucesso: **127 testes aprovados**, incluindo `compileall` ([testes isolados](https://github.com/armoredcreator/audio/actions/runs/39336661372)) e o workflow de [diagnósticos de áudio](https://github.com/armoredcreator/audio/actions/runs/39336661228). A falha de uma revisão intermediária foi corrigida antes dessa versão validada.

A revisão de módulos não encontrou um candidato seguro para exclusão como “órfão”: os processadores por manifesto/modelo, runners de lote/corpus, separador isolado e diagnósticos têm contratos ou usos próprios. Foram mantidos em vez de removidos por inferência nominal.

### E2E focal real após a correção da ordem

**Caso A — EFFECT_ONLY:** item 001 do corpus local de 17 vídeos; NOISE → EFFECT_ONLY; timeline NO_BANNER; STUDIO_SPEECH_BATCH=SUCCESS; ORIGINALS_INTACT=True. FFprobe confirmou H.264 1080×1920, AAC estéreo 44.100 Hz, duração 10 segundos e 25.923.122 bytes. Relatório: storage/validation/focal_no_banner_20261019_063742_435/studio-report.json.

**Caso B — RVC Melody:** item 002; PT_BR_NARRATION → RVC_MELODY; timeline NO_BANNER; STUDIO_SPEECH_BATCH=SUCCESS, RVC_E2E_FOCAL=SUCCESS e ORIGINALS_INTACT=True. SHA-256 do original: 0e58fcd53135e437778ee1abd787f985a5b6ebe70c638072a232eeded31f9d05. Saída: storage/Videos GRUPO_FONTE_corpus17_music-vs-speech-20261018T084252414/002/002_focal-rvc-20261019T100510241016Z.mp4, 10.664.149 bytes. Relatório: storage/validation/focal_rvc_20261019T100510241016Z/studio-report.json. O usuário confirmou que ficou correto de ponta a ponta.

O manifesto original aprovado do corpus 17, o SQLite experimental e os originais foram preservados. O lote completo não foi reexecutado após a correção.

### Evidência anterior e banner

O item source_teste/5 e o corpus de 17 vídeos passaram por E2E real na implementação v5 anterior à correção de ordem. Nesse corpus houve 17/17 renderizações, 9 rotas RVC_MELODY e 8 EFFECT_ONLY. Essas execuções são evidência da versão anterior; os dois casos focais acima validam a ordem atual.

O usuário confirmou que nenhum dos 17 vídeos tem banner inicial. Os E2Es reais atuais exercitam NO_BANNER. BANNER_PRESENT tem cobertura automatizada de seleção, não E2E com mídia real. Não procurar nem inventar vídeo com banner inexistente no corpus.

O merge ocorreu somente na branch de laboratório, não no branch main do repositório audio e nem em armoredcreator/armoredcreator, armoredcreator-test ou armoredcreator-testbase. Nenhum banco operacional foi utilizado; nada foi publicado no Hub.

---

## 2. Invariantes do projeto

A ordem global permanece:

**ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub.**

O AudioLab valida Speech → Studio; não substitui Coordinator, não reinicia coleta histórica e não executa Sync, Vision, Stock, IA ou Hub.

1. Speech conclui o lote aprovado inteiro antes de Studio começar; não intercalar fases por item.
2. O MP4 original é imutável; conferir SHA-256 antes/depois.
3. O runner usa apenas o SQLite experimental explicitamente declarado. Nunca usar storage/database/armoredcreator.db.
4. Manifesto não aprovado, decisão ausente, WAV inválido, caminho fora do laboratório, hash divergente ou timeline inconsistente bloqueiam o lote antes do render.
5. Nunca sobrescrever resultado existente. Cada teste real usa manifesto, relatório e destino novos.
6. Não publicar no Hub nem integrar em produção nesta etapa.
7. Não executar git reset --hard, git clean ou exclusões recursivas genéricas.
8. Preservar storage, originais, SQLite experimental, manifestos, relatórios, pacotes, saídas e ativos canônicos.
9. No checkout local anterior foi identificado `ArmoredStudio/assets/README.md` com texto antigo que atribuía o efeito sonoro ao Studio. O conteúdo desse arquivo local deve ser reconciliado explicitamente com o Git; não presumir que ele já esteja versionado nem substituí-lo/apagá-lo durante a atualização. O banner é responsabilidade visual do Studio; o efeito sonoro e o RVC pertencem ao Speech.
10. Não remover a .venv testada, bancos, vídeos ou evidências durante a limpeza local.

---

## 3. Arquitetura e fronteira de responsabilidade

### ArmoredSpeech — dono do áudio

Speech classifica fala, idioma, música, ruído e silêncio; determina elegibilidade RVC; extrai e prepara áudio; executa RVC Melody quando permitido; prepara faixa de voz ou silêncio e faixa de efeito; aplica ganho uma única vez; define sincronização, trims, fades, duração, intro sonora, mixagem, grafo de áudio e parâmetros de encode do contrato v5.

Módulos centrais:
- ArmoredSpeech/audio_package.py: preparação, metadados, hashes e validação dos pacotes.
- ArmoredSpeech/studio_bridge.py: exporta a decisão do SQLite em modo somente leitura.
- ArmoredSpeech/studio_integration.py: prepara o lote inteiro e as duas variantes temporais.
- ArmoredSpeech/timeline_mixer.py: grafo de áudio e cálculo do início de voz.
- ArmoredSpeech/rvc_engine.py: único motor RVC ativo.
- ArmoredSpeech/studio_batch_runner.py: preflight do lote, limite de fases e orquestração.
- ArmoredSpeech/vocal_separation.py: separação vocal validada para narração com música.

### ArmoredStudio — dono do visual e do render final

Studio analisa vídeo/imagem, barras, banner e marcas VEO/Gemini; consolida plano visual, crop 9:16, filtros e intro visual. Valida estruturalmente o contrato Speech, mas não decide rota de áudio, não executa RVC, não gera silêncio, não define volumes/fades e não cria o grafo de áudio.

Módulos centrais:
- ArmoredStudio/unified.py: análise visual, consumo do contrato e fluxo final.
- ArmoredStudio/speech_handoff.py: validador fail-closed do contrato v5.
- ArmoredStudio/processing/visual_finalizer.py: grafo de vídeo e render audiovisual.

O detector visual chamado Gemini em ArmoredStudio/analysis/gemini_detector.py usa OpenCV/template matching local; não chama a API Google Gemini.

### Uma chamada FFmpeg

Há uma única invocação FFmpeg para o render audiovisual final por vídeo, juntando grafo visual e grafo de áudio do Speech. Isso não proíbe FFmpeg em operações preparatórias próprias do Speech, como extração ou normalização de WAV, nem o processamento do motor RVC. Não deve haver renderização intermediária de áudio final seguida de outra chamada para juntá-lo ao vídeo.

---

## 4. Contrato ativo v5 e ordem do lote

O pacote é produzido por ArmoredSpeech/audio_package.py e ArmoredSpeech/studio_integration.py. Studio valida em ArmoredStudio/speech_handoff.py e consome em ArmoredStudio/unified.py e ArmoredStudio/processing/visual_finalizer.py.

O contrato registra identidade, caminho/hash do original antes/depois, voice_track e effect_track com formato/tamanho/duração/frequência/canais/hashes, grafo de áudio e hash, rótulo de saída, timeline e parâmetros de render. Também inclui as variantes completas audio_timeline_variants: NO_BANNER e BANNER_PRESENT.

Ordem obrigatória:
1. Ler manifesto explicitamente completo/aprovado; validar quantidade, identidade, caminhos e hashes.
2. Ler decisão READY de Speech no SQLite experimental em modo somente leitura; validar WAVs.
3. Fazer preflight de todos os itens, ferramentas, duração, efeito e modelo/índice Melody quando elegíveis.
4. Preparar faixas de todos os itens e criar ambas as timelines por item.
5. Validar pacotes e variantes de todo o lote. Qualquer falha bloqueia chamadas ao Studio.
6. Só então iniciar análise visual somente de leitura.
7. Selecionar a variante já preparada com base na evidência visual. Studio não recalcula nem altera o grafo de áudio.
8. Validar variante, plano visual e originais antes do primeiro render.
9. Renderizar em destino novo; verificar saída, streams e hash do original.
10. Gravar relatório novo.

NO_BANNER é a alternativa sem corte temporal adicional pelo banner. BANNER_PRESENT é criada por Speech para o cenário de banner. A detecção visual escolhe entre alternativas existentes; não transfere política de áudio ao Studio.

---

## 5. Classes e rotas de áudio

| Classe/condição | RVC Melody | Comportamento esperado |
|---|---|---|
| PT_BR_NARRATION | Só com PT-BR, fala detectada e elegibilidade persistida | RVC Melody |
| PT_BR_NARRATION_WITH_MUSIC | Só após separação vocal validada | Speech valida recibo do stem; RVC recebe só a voz separada |
| MUSIC_ONLY | Nunca | faixa de voz/silêncio e efeito sonoro |
| NOISE | Nunca | não reaproveitar áudio ruidoso como narração; rota EFFECT_ONLY |
| NO_AUDIO | Nunca | silêncio determinístico e efeito |
| NO_USABLE_NARRATION | Nunca | rota de efeito; não inferir voz utilizável |
| FOREIGN_SPEECH | Nunca para Melody PT-BR | não converter fala estrangeira para Melody; rota EFFECT_ONLY |
| UNKNOWN, decisão ausente/inconsistente | Bloqueado | erro explícito e lote não renderizado |

rvc_candidate_eligible é apenas candidatura preliminar. rvc_eligible permanece falso até que Speech valide recibo de separação ligado ao original, stem e modelo. Filtro passa-alta não prova separação vocal. Falha na separação deve bloquear a fase; não silenciar silenciosamente uma narração que deveria ser preservada.

---

## 6. Ambiente e ativos locais

O Windows validado usa a .venv compartilhada na raiz com Python 3.11.x para Speech, Studio visual e RVC chamado pelo Speech. Não criar ambiente duplicado nem substituir a .venv validada sem necessidade.

Caminhos canônicos:
- Modelos/índices Melody: ArmoredSpeech/runtime/rvc/models/melody/
- Efeito: ArmoredSpeech/assets/efeitosonoro.wav
- Banner visual: ArmoredStudio/assets/banner.png
- Manifestos/relatórios/pacotes: storage/validation/
- SQLite experimental: storage/database/ com nome de laboratório próprio.
- Proibido ao runner: storage/database/armoredcreator.db.

Pesos .pth, índices .index, efeito e banner são binários locais não versionados. Melody precisa de ao menos um .pth; índice .index é opcional. Não configurar fallback para ArmoredStudio/runtime/rvc.

ArmoredSpeech/scripts/migrate_audio_assets.ps1 serve a checkout antigo. Se ativos antigos do Studio existirem, copia e verifica SHA-256 sem sobrescrever destino divergente. Se os ativos canônicos já estiverem no Speech, apenas os valida. Se faltarem em ambas as localizações, falha sem inventar arquivos ou apagar dados. ArmoredSpeech/assets/fetch_effect_asset.py materializa o efeito oficial verificando hash; token read-only, quando necessário, fica apenas no ambiente, nunca no código, documentação ou commit.

Para checkout novo — não execute novamente no checkout já validado:

~~~powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\ArmoredSpeech\setup_runtime.ps1
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
.\.venv\Scripts\python.exe -m compileall -q ArmoredSpeech ArmoredStudio armored_core
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\scripts\audit_audio_ownership.py --root .
~~~

setup_runtime.ps1 instala o projeto e requirements-analysis.txt na .venv compartilhada. -InstallSeparation instala requirements-separation.txt somente se a tarefa exigir separação vocal.

---

## 7. Validação e execução do runner

Execute na raiz do AudioLab quando houver mudança de código ou preparação de checkout novo:

~~~powershell
.\.venv\Scripts\python.exe -m compileall -q ArmoredSpeech ArmoredStudio armored_core
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -c "import ArmoredSpeech; import ArmoredStudio.service; print('imports OK')"
.\.venv\Scripts\python.exe .\scripts\audit_audio_ownership.py --root .
~~~

A auditoria de ownership é somente leitura. PASS não substitui E2E real; E2E real tampouco substitui toda a suíte automatizada.

Modelo do runner — substituir placeholders por manifesto aprovado e SQLite experimental correspondente, com relatório/destino nunca usados:

~~~powershell
.\.venv\Scripts\python.exe -m ArmoredSpeech.studio_batch_runner --root . --manifest ".\storage\validation\<lote-aprovado>\studio-manifest.json" --speech-database ".\storage\database\<sqlite-experimental-correspondente>.sqlite3" --report ".\storage\validation\<pasta-nova>\studio-report.json"
~~~

Não usar --copy-only como prova de áudio/render real. O runner bloqueia destinos existentes. Não apagar evidências para repetir um teste.

corpus_e2e_runner documenta exemplo com 14 entradas, mas o corpus real histórico aqui tinha 17. Não copiar número 14 sem revisar a lista explícita. Não descobrir todos os MP4 de storage genericamente, pois a pasta contém saídas e evidências. Não é necessário repetir o corpus agora: os dois E2Es focais pós-correção passaram.

---

## 8. Revisão da documentação anterior

A leitura dos dez Markdown do commit de merge encontrou conteúdo útil, mas repetido e com estados antigos. Este manual é a referência operacional detalhada.

| Documento | Conclusão da revisão |
|---|---|
| README.md | Misturava arquitetura, status antigo da PR, contagem antiga e pendências; agora é índice. |
| ArmoredSpeech/README.md | Duplicava ownership, ativos e status v5; agora é índice. |
| ArmoredSpeech/docs/ARMORED_SPEECH_CONTRACT.md | Misturava schema experimental, rotas antigas e limitações anteriores; contrato/rotas atuais estão aqui. |
| ArmoredSpeech/docs/REAL_AUDIO_REVIEW_20261015.md | Relatório histórico de 15 áudios e rótulos humanos de 15/10; mantido como histórico, não estado atual nem corpus de 17. |
| ArmoredSpeech/docs/SETUP_WINDOWS.md | Setup em boa parte válido, mas repetido; passos atuais consolidados aqui. |
| ArmoredSpeech/docs/STUDIO_BATCH_RUNNER.md | Fluxo útil, mas contagem de corpus 14, testes 102 e status draft ficaram antigos. |
| ArmoredSpeech/docs/STUDIO_BRIDGE_V1.md | Handoff v1 experimental; a renderização atual usa v5. |
| ArmoredSpeech/samples/README.md | Mantido como orientação auxiliar; não adicionar vídeos ao Git. |
| docs/ADR_SPEECH_AUDIO_TIMELINE.md | Decisão útil, mas status, contagem, histórico e próximos passos estavam datados. |
| docs/ARMORED_SPEECH_STUDIO_FLOW.md | Misturava a fronteira final com planos anteriores à migração; fluxo atual consolidado aqui. |

requirements-analysis.txt e requirements-separation.txt são manifests ativos de dependências, não documentação narrativa, e não devem ser apagados.

Contradições resolvidas: v5 é o contrato ativo; PRs de laboratório não significam merge no main nem no repositório operacional; 127 testes é o resultado do commit de consolidação desta rodada, 116 e 112 são contagens históricas de versões anteriores e 102 é ainda mais antigo; nenhum dos 17 vídeos tem banner; o E2E de 17 é anterior à correção da ordem; os E2Es focais de 19/10 validam a ordem atual; o corpus com 14 entradas é exemplo do runner, não a contagem do corpus histórico; nada autoriza port para produção.

---

## 9. Sincronização e limpeza local segura

Objetivo: fazer os arquivos versionados em `C:\Users\Administrador\Downloads\ArmoredAudioLab` corresponderem à branch remota `test/armoredstudio-speech-integration-20261015`, incluindo este manual. Dados locais ignorados são preservados intencionalmente.

### Inventariar antes de trocar de branch

~~~powershell
git status --short --branch
git branch --show-current
git rev-parse HEAD
git diff --name-only
git diff --cached --name-only
~~~

Se git diff ou git diff --cached listar arquivos versionados, pare e revise antes de trocar de branch. Não descarte alterações locais automaticamente.

### Sincronizar por fast-forward

Só prossiga se não houver alterações versionadas locais a preservar:

~~~powershell
git fetch origin
$Target = 'test/armoredstudio-speech-integration-20261015'
$LocalBranch = git branch --list $Target
if ($LocalBranch) { git switch $Target } else { git switch --track -c $Target "origin/$Target" }
git merge --ff-only "origin/$Target"
$LocalSha = (git rev-parse HEAD).Trim()
$RemoteSha = (git rev-parse "origin/$Target").Trim()
Write-Output "LOCAL_HEAD=$LocalSha"
Write-Output "REMOTE_HEAD=$RemoteSha"
if ($LocalSha -ne $RemoteSha) { throw 'O checkout local não está no mesmo commit remoto; pare e investigue.' }
git diff --exit-code "origin/$Target" -- .
if ($LASTEXITCODE -ne 0) { throw 'Existem diferenças em arquivos versionados.' }
git status --short --branch
~~~

Se merge --ff-only recusar, não use reset nem force; revise commits/alterações locais.

### O que limpar e o que preservar

A árvore versionada contém .github, ArmoredSpeech, ArmoredStudio, armored_core, docs, scripts, README.md, .gitignore e pyproject.toml. .venv e storage são locais por design.

- Preservar .venv, porque é o ambiente validado.
- Preservar storage integralmente: originais, bancos, manifestos, WAVs, pacotes, relatórios, logs, saídas e evidências.
- Preservar modelos/índices de ArmoredSpeech/runtime/rvc, o efeito canônico e ArmoredStudio/assets/banner.png.
- Preservar ArmoredStudio/assets/README.md até revisar conteúdo e decidir explicitamente se deve ser versionado ou ficar local.
- .pytest_cache e armoredspeech_lab.egg-info são normalmente artefatos gerados e estão ignorados pelo .gitignore; só remover após confirmar que são artefatos deste checkout.
- tests/ na raiz não consta na árvore versionada atual; inventariar antes de remover/mover. Não confundir com ArmoredSpeech/tests/, que é versionado.
- Nunca executar git clean, git clean -xfd, remoções recursivas genéricas ou exclusões por curingas.

Paridade significa LOCAL_HEAD igual a REMOTE_HEAD, git diff sem diferenças em arquivos versionados e sem alterações rastreadas não intencionais. Dados ignorados e arquivos locais explicitamente preservados podem continuar presentes sem significar divergência do código.

---

## 10. Integração futura no repositório operacional

O AudioLab permanece isolado como laboratório; a consolidação compartilhada desta rodada está integrada e passou no CI, mas isso não equivale a declarar validados todos os cenários E2E. A integração operacional é uma etapa separada. Não editar `main` diretamente nem copiar módulos sem auditoria do layout real.

1. Criar branch exclusiva no repositório operacional e revisar arquitetura, dependências, Coordinator e schema SQLite real.
2. Manter Coordinator como composition root e SQLite como source of truth; não adicionar filas/sistemas paralelos.
3. Preservar a ordem global Sync completo → Vision completo → Stock completo → IA → Speech completo → Studio → Hub.
4. Transferir ownership e ativos sem fallback legado, adaptando o contrato às interfaces operacionais.
5. Não usar nem migrar dados operacionais durante testes do laboratório; não alterar histórico, checkpoints ou originais.
6. Executar CI, suíte, auditoria de ownership e E2Es em cópias/SQLite isolados.
7. Não publicar no Hub antes de aprovação explícita.

---

## 11. Critérios de aceitação

- Contrato Speech → Studio v5 e runner de lote validados, com Speech completando todo o lote antes de Studio iniciar.
- E2Es focais reais EFFECT_ONLY e RVC_MELODY passaram na ordem atual e preservaram os originais.
- Consolidação das implementações duplicadas de SHA-256 de arquivo, Git blob SHA-1/materialização de assets, digest canônico do plano visual, contenção de caminhos e opções de subprocesso Windows.
- CI no merge atual da PR #7, commit `3e6969eeb4c42d747989fc3d865f1edfd72a92b6`: `compileall` e **127 testes** passaram ([workflow de testes](https://github.com/armoredcreator/audio/actions/runs/39336661372)); o [workflow de diagnósticos de áudio](https://github.com/armoredcreator/audio/actions/runs/39336661228) também passou.
- Filtros do workflow corrigidos para que alterações do Studio, armored_core e scripts também iniciem a suíte em pull requests.
- Não foram removidos módulos sem prova de que eram órfãos; banco, mídia original e assets não foram incluídos nem alterados.
- E2E EFFECT_ONLY pós-correção passou, selecionou NO_BANNER, produziu H.264/AAC válido e preservou o original.
- E2E RVC_MELODY pós-correção passou, selecionou NO_BANNER, gerou saída confirmada pelo usuário como correta e preservou o original.
- Nenhum banner no corpus registrado com honestidade; BANNER_PRESENT coberto por testes automatizados, sem alegar E2E real.
- SQLite, originais, resultados, ativos canônicos e evidências preservados.
- Repositórios operacionais e branch main do AudioLab não foram alterados por esta etapa.

**Estado em 19/10/2026:** PR #7 mesclada na branch `test/armoredstudio-speech-integration-20261015`; os dois workflows do merge passaram, incluindo 127 testes e `compileall`. A documentação agora aponta para a branch e o commit corretos. A evidência E2E atual inclui os dois casos focais pós-correção (EFFECT_ONLY e RVC_MELODY); o lote inteiro de 17 vídeos não foi reexecutado após essa correção, e `BANNER_PRESENT` não tem E2E com mídia real porque nenhum vídeo do corpus contém banner. A sincronização local pode ser feita por fast-forward após inventariar alterações locais, preservando `storage`, `.venv`, assets e evidências. A integração operacional continua sendo uma mudança futura e separada.
