# ArmoredAudioLab — manual canônico de arquitetura, operação e evidências

**Versão:** 2.1  
**Revisão:** 20/10/2026 (horário local de São Paulo)  
**Repositório:** armoredcreator/audio  
**Branch estável de laboratório:** test/armoredstudio-speech-integration-20261015  
**Base de código consolidada antes da auditoria Sync:** a2af33c378c12a4599c7fe8bf6de3cb541575d86  
**Contrato ativo Speech → Studio:** v5  
**Finalidade:** validar a separação de responsabilidades entre Speech (áudio) e Studio (visual/renderização) antes de uma futura integração operacional.

Este é o documento canônico: reúne arquitetura, fronteiras de responsabilidade, contrato, ordem de execução, ativos, segurança, comandos e evidências verificadas. Os READMEs e documentos técnicos curtos devem apontar para este manual e não manter um segundo estado operacional divergente. Os bancos, vídeos, pacotes, relatórios e saídas citados abaixo são evidências locais do laboratório; não devem ser adicionados ao Git.

---

## 1. Resumo executivo — estado confirmado

### Decisão atual

A fronteira de funções está estabelecida e foi exercitada no pipeline real:

- **ArmoredSpeech** decide e prepara o áudio: classificação, elegibilidade da rota, separação vocal quando necessária, RVC Melody, faixa de efeito, volume, sincronização, fades, mixagem e ambas as variantes temporais do contrato.
- **ArmoredStudio** analisa apenas o domínio visual, escolhe a variante temporal que Speech já preparou, aplica o plano visual e faz o render audiovisual final.
- **armored_core** mantém utilitários realmente compartilhados, como integridade de arquivos, contenção de caminhos e helpers comuns.
- O runner de integração exige Speech completo e válido antes de iniciar a análise/renderização do Studio. Uma falha no preflight deve impedir o render.
- O laboratório não usa o banco operacional do ArmoredCreator, não executa Sync/Vision/Stock/IA/Hub e não publica conteúdo.

### Resultados de validação mais recentes

| Validação | Resultado observado | O que comprova |
|---|---|---|
| Suíte isolada após a consolidação de código | 127 testes passaram localmente; compileall, imports e auditoria estática passaram | Integridade estrutural e contratos cobertos pelos testes |
| GitHub Actions após PR #7 | Workflow de testes e workflow de diagnósticos de áudio concluídos com sucesso | CI da consolidação de código |
| GitHub Actions após PR #8 | Workflow de testes isolados concluído com sucesso no HEAD 23b398d | Documentação e branch de referência atualizadas sem regressão conhecida |
| E2E real do corpus de 17 vídeos na versão consolidada | 17/17 processados; pós-validação 17/17 | Caminho atual Speech → Studio, mídia decodificável, streams esperados e preservação dos originais |
| E2E real com banner, arquivo original_018.mp4 | BANNER_E2E=PASS; variante BANNER_PRESENT; RVC_MELODY | Caminho de banner real, handoff v5, render, codecs e decodificação |
| Inspeção visual humana do resultado BANNER_PRESENT | Usuário assistiu e confirmou que ficou perfeito | Aceitação visual do caso real testado |

A evidência sustenta a aprovação técnica do pipeline de laboratório nos cenários citados. **Não equivale a uma integração já aprovada no repositório operacional**, nem prova cenários que não estejam explicitamente listados neste manual.

### O que não fazer agora

- Não repetir o corpus de 17 vídeos apenas para obter a mesma evidência.
- Não repetir o E2E de banner que já passou.
- Não excluir nem sobrescrever relatórios, manifestos, bancos experimentais, áudio preparado, originais ou saídas válidas.
- Não usar storage/database/armoredcreator.db.
- Não copiar o trabalho para armoredcreator/armoredcreator, para branches operacionais ou para o Hub nesta etapa.

---

## 2. Registro canônico das evidências

Os nomes de diretórios de execução incluem timestamps UTC. Por isso, o diretório da execução de banner tem data UTC 20/10/2026, embora a execução tenha ocorrido na noite de 19/10 no horário de São Paulo.

### 2.1 CI e testes automatizados

- HEAD de referência da branch: 23b398d8150a79bb59299733c5340db307b4119c.
- PR #7 consolidou helpers e testes na branch de laboratório. O workflow de testes passou com 127 testes e compileall: [GitHub Actions — testes da consolidação](https://github.com/armoredcreator/audio/actions/runs/39336661372).
- O workflow de diagnósticos de áudio da mesma consolidação passou: [GitHub Actions — diagnósticos de áudio](https://github.com/armoredcreator/audio/actions/runs/39336661228).
- PR #8 atualizou referências canônicas e documentação. O workflow de testes no HEAD 23b398d passou: [GitHub Actions — testes após PR #8](https://github.com/armoredcreator/audio/actions/runs/39338633592).
- Na máquina local, após sincronizar o HEAD 23b398d, foram executados com sucesso: compileall, pytest (127 passed), importação de ArmoredSpeech/ArmoredStudio e scripts/audit_audio_ownership.py. A auditoria informou ownership PASS, zero referências de execução de áudio legado, zero referências inesperadas à política de áudio no Studio; modelos/índices e ativos canônicos encontrados nas localizações esperadas.
- O relatório de diagnóstico não representa, por si só, uma validação auditiva humana de todo o corpus. CI, pós-validação de mídia e escuta/inspeção humana são tipos de evidência diferentes e devem ser registrados separadamente.

### 2.2 E2E real de corpus com 17 vídeos — versão consolidada

**Execução:** post_consolidation_corpus17_20261019T234723122Z

**Entrada aprovada e histórica, preservada:**
- Manifesto original: storage/validation/corpus17_music-vs-speech-20261018T084252414/studio-manifest.json
- SQLite experimental de Speech: storage/database/armored_speech_corpus17_music-vs-speech-20261018T084252414.sqlite3

**Evidência nova da execução na versão consolidada:**
- Manifesto gerado para a nova execução: storage/validation/post_consolidation_corpus17_20261019T234723122Z/studio-manifest.json
- Relatório principal do Studio: storage/validation/post_consolidation_corpus17_20261019T234723122Z/studio-report.json
- Resultado declarado pelo runner: STUDIO_SPEECH_BATCH=SUCCESS, MODE=REAL_STUDIO, ITEMS=17, ORIGINALS_INTACT=True, contrato v5.
- Pós-validação independente dos arquivos finais: POST_CONSOLIDATION_CORPUS17_POSTFLIGHT=PASS; TOTAL=17/17.
- Para todos os 17 arquivos, ffprobe encontrou vídeo H.264 1080×1920 e áudio AAC estéreo 44.100 Hz. A decodificação completa de cada saída terminou com sucesso usando FFmpeg em modo de erro estrito. Os hashes dos originais foram confirmados.
- Todos os 17 itens usaram timeline NO_BANNER, porque este corpus não contém uma abertura de banner.

Distribuição confirmada no relatório de pós-validação:

| IDs do corpus | Classe/rota de render confirmada |
|---|---|
| 002, 004, 005, 008, 010, 011, 012, 013, 014 | RVC_MELODY — 9 itens |
| 001, 003, 006, 007, 009, 015, 016, 017 | EFFECT_ONLY — 8 itens |

Os itens de entrada incluíram narração PT-BR, narração PT-BR com música, MUSIC_ONLY, NO_AUDIO, NOISE e FOREIGN_SPEECH. A lista acima registra a rota de render observada; ela não deve ser interpretada como uma aprovação auditiva individual dos 17 áudios.

Durante a renderização foram emitidos avisos H.264 do tipo “mmco: unref short failure”. Esses avisos não impediram a conclusão do runner, e a decodificação estrita posterior de todos os 17 arquivos passou. Devem permanecer anotados como observação diagnóstica, sem transformar um aviso isolado em falha que os testes não reproduziram, e sem ignorar uma eventual falha futura de decode.

### 2.3 E2E real de banner — BANNER_PRESENT validado

**Entrada preservada:** storage/original_018.mp4  
**Tamanho observado da entrada:** 475.803 bytes  
**Hash da entrada:** capturado antes da análise e comparado depois; resultado ORIGINAL_HASH_INTACT=PASS.

**Pré-verificação visual somente de leitura:**

~~~json
{
  "arquivo": "original_018.mp4",
  "tamanho_bytes": 475803,
  "banner_analysis": {
    "banner_detectado": true,
    "confianca": 90,
    "mudanca_inicial": 0.1,
    "estabilidade": 0.0
  }
}
~~~

**Execução real Speech → Studio:**
- Batch: banner_present_20261020T004009599Z.
- Relatório Speech: storage/validation/corpus1_banner_present_20261020T004009599Z/speech-report.json.
- Manifesto aprovado: storage/validation/corpus1_banner_present_20261020T004009599Z/studio-manifest.json.
- Relatório do Studio: storage/validation/corpus1_banner_present_20261020T004009599Z/studio-report.json.
- Saída renderizada: storage/Videos GRUPO_FONTE_corpus1_banner_present_20261020T004009599Z/001/001_corpus1_banner_present_20261020T004009599Z_001.mp4.
- Bytes da saída: 6.083.999.
- Variante selecionada no relatório: BANNER_PRESENT.
- Rota de áudio: RVC_MELODY.
- Pós-validação: H.264 1080×1920, AAC 44.100 Hz estéreo, decode=OK, original copy hash OK, BANNER_E2E=PASS.
- O hash do arquivo-fonte em storage foi comparado antes/depois e permaneceu intacto.
- **Aceitação visual humana:** o usuário assistiu ao vídeo final e confirmou que ficou perfeito.

Essa execução fecha a lacuna documental anterior: já existe E2E real com mídia de banner, não apenas teste unitário. A confirmação é específica ao vídeo citado e ao resultado armazenado. Não significa que todos os estilos possíveis de banner tenham sido testados.

### 2.4 Evidências anteriores que continuam históricas

- A revisão auditiva de 15 arquivos documentada em ArmoredSpeech/docs/REAL_AUDIO_REVIEW_20261015.md é uma revisão histórica de classificação; não é o E2E de 17 itens nem o E2E de banner.
- A execução E2E focal de EFFECT_ONLY e a de RVC Melody feitas antes do corpus completo continuam úteis como testes diagnósticos, mas não substituem a evidência mais recente do corpus completo.
- A versão anterior do documento dizia que BANNER_PRESENT não tinha E2E real. Esse estado está superado pelas evidências da seção 2.3.
- Nenhum vídeo, arquivo SQLite, modelo, áudio intermediário ou render final citado deve ser adicionado ao Git. O código e a documentação descrevem o procedimento; os artefatos concretos permanecem em storage local.

---

## 3. Invariantes globais do ArmoredCreator

A ordem global do produto permanece:

**ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub.**

O AudioLab valida exclusivamente a fronteira Speech → Studio. Não reexecuta Sync, Vision, Stock ou IA; não substitui o Coordinator; não modifica checkpoints/histórico da coleta; não publica no Hub.

Invariantes obrigatórios:

1. Speech precisa preparar e validar o lote aprovado inteiro antes de Studio iniciar. As fases não são intercaladas item a item.
2. SQLite experimental declarado é a fonte das decisões de Speech para este laboratório; o runner não escolhe um banco por descoberta automática.
3. O banco operacional storage/database/armoredcreator.db é proibido nesta integração experimental.
4. MP4 original é imutável. Calcular SHA-256 antes e depois, tanto para a fonte quanto para a cópia de teste sempre que o runner fornecer as duas evidências.
5. Não permitir paths fora da raiz do laboratório, cópias vazias, WAV ausente/inválido, hash divergente, manifesto incompleto, estado não READY ou contrato/timeline inválidos.
6. Não sobrescrever resultados de execuções anteriores. Cada nova execução precisa de identificador, pasta de relatório, banco e destino novos.
7. Um erro no preflight de qualquer item bloqueia a fase de render. Não iniciar “o que der” com um subconjunto e declarar o lote aprovado.
8. Não converter avisos em sucesso sem pós-validação; não converter avisos isolados em falha sem verificar status, streams e decodificação.
9. Não adicionar Redis, RabbitMQ, Celery, Kafka, publish_queue ou outra infraestrutura de fila para esta fronteira.
10. Não executar git reset, git clean, remoções recursivas genéricas, substituições destrutivas de ativos ou limpeza automática de storage.
11. Não migrar o código para o repositório operacional até existir plano separado, revisão de interfaces/Coordinator/SQLite e validação em branch isolada.
12. Alterações de documentação precisam refletir evidências efetivamente vistas e identificar como pendente qualquer validação ainda não realizada.

---

## 4. Arquitetura e ownership de cada módulo

### 4.1 ArmoredSpeech — único proprietário do áudio

Speech é responsável por decisões e por preparar faixas/dados que Studio consumirá. Inclui:

- Classificação de áudio, idioma, fala, música, ruído e ausência de áudio.
- Decisão de elegibilidade para RVC; uma classificação preliminar não equivale a elegibilidade aprovada.
- Separação vocal/instrumental quando a classe exigir, com recibo e validação de stem.
- RVC Melody, usando o modelo e o runtime canônicos em Speech.
- Faixa de voz final ou conteúdo de voz determinístico (incluindo silêncio quando a política da classe assim determinar).
- Efeito sonoro canônico, seu posicionamento, volumes, trims e fades.
- Duração e sincronização do áudio, início do vídeo em relação à timeline, grafo de filtros de áudio, rótulo de saída e parâmetros de encode definidos pelo contrato.
- Preparação de ambas as variantes temporais NO_BANNER e BANNER_PRESENT antes de Studio iniciar.
- Integridade de trilhas, hashes, durações, frequências de amostragem, canais e metadados do pacote.

Módulos de referência:

| Módulo | Responsabilidade principal |
|---|---|
| ArmoredSpeech/audio_intelligence.py | Classificação e evidências de áudio |
| ArmoredSpeech/audio_package.py | Construção/validação do pacote de áudio e metadados |
| ArmoredSpeech/studio_bridge.py | Leitura explícita da decisão experimental e handoff |
| ArmoredSpeech/studio_integration.py | Preparação em lote e das variantes temporais |
| ArmoredSpeech/timeline_mixer.py | Timeline e grafo de áudio |
| ArmoredSpeech/vocal_separation.py | Separação vocal validada |
| ArmoredSpeech/rvc_engine.py | Único motor RVC do sistema integrado |
| ArmoredSpeech/batch.py e model_processor.py | Processamento/decisões de cada item de Speech |
| ArmoredSpeech/studio_batch_runner.py | Preflight, limite Speech → Studio, seleção de variante e orquestração |
| ArmoredSpeech/corpus_e2e_runner.py | Criação de cópias de entrada e execução E2E explícita do corpus |

### 4.2 ArmoredStudio — proprietário do visual e do render audiovisual final

Studio é responsável por:

- Análise visual somente de leitura do vídeo.
- Detecção/planejamento de barras, banner de abertura e marcas visuais VEO/Gemini.
- Plano visual, crop 9:16, filtros de vídeo e composição visual.
- Validação estrutural do contrato de Speech; não deve redecidir áudio.
- Seleção de NO_BANNER ou BANNER_PRESENT com base na evidência do detector.
- Combinação do grafo de vídeo com o grafo de áudio fornecido e render final.

Módulos de referência:

| Módulo | Responsabilidade principal |
|---|---|
| ArmoredStudio/service.py | Construção do serviço visual usado pelo runner |
| ArmoredStudio/unified.py | Coordenação da análise visual e render final |
| ArmoredStudio/speech_handoff.py | Validação fail-closed do contrato v5 |
| ArmoredStudio/analysis/banner_analyzer.py | Detector heurístico de abertura/banner |
| ArmoredStudio/analysis/blackbar.py | Evidência e análise de barras pretas |
| ArmoredStudio/analysis/gemini_detector.py | Detecção visual local por OpenCV/template matching |
| ArmoredStudio/analysis/veo_detector.py | Detecção de marcadores visuais VEO |
| ArmoredStudio/processing/visual_finalizer.py | Grafo de vídeo e render audiovisual final |

O módulo com nome gemini_detector.py não deve ser descrito como integração com a API Google Gemini: a implementação atual usa OpenCV/template matching local.

### 4.3 armored_core — helpers gerais compartilhados

armored_core centraliza helpers genéricos sem se tornar um segundo Coordenador de negócio: integridade SHA-256, tratamento seguro de caminhos, funções de armazenamento e estruturas compartilhadas. Quando existe wrapper legado público, ele pode delegar ao helper canônico para evitar duplicação sem quebrar consumidores.

O projeto não deve acumular cópias independentes do mesmo algoritmo de hash, contenção de paths, digest do plano visual ou preparação de subprocesso Windows.

### 4.4 Um render audiovisual final por vídeo

O Studio deve executar uma chamada FFmpeg para compor o vídeo com o grafo visual e o grafo de áudio que Speech definiu. Isso não proíbe chamadas preparatórias próprias de Speech, tais como extração/normalização de WAV ou processamento exigido pelo RVC. O que se evita é gerar um vídeo final sem áudio, depois um arquivo final de áudio independente e, por último, juntar ambos em outra etapa final duplicada.

---

## 5. Contrato Speech → Studio v5

### 5.1 A regra essencial

O v5 é o contrato ativo para o render audiovisual. O protótipo v1 mantido em STUDIO_BRIDGE_V1.md é histórico, não é a especificação operacional do render atual.

Speech cria o pacote e os dois grafos de timeline. Studio recebe o pacote, verifica identidade/paths/hashes/estrutura, analisa o vídeo e seleciona a variante apropriada. Studio não altera as decisões de áudio nem recalcula volumes, fades ou timing.

### 5.2 Evidências que o pacote precisa vincular

O pacote deve permitir validar, antes do render:

- Identidade do item e source_id/item_id esperados.
- Caminho do original e SHA-256 correspondente ao arquivo real.
- Referência e integridade das faixas de voz e de efeito.
- Formato de áudio, tamanho, duração, taxa de amostragem e canais onde aplicável.
- Hashes das faixas, do grafo de áudio e dos dados relevantes de timeline.
- Rótulo de saída de áudio e parâmetros de render definidos no contrato.
- Timeline ativa e ambas as entradas de audio_timeline_variants: NO_BANNER e BANNER_PRESENT.
- Relação verificável entre a timeline selecionada, o plano visual e o digest esperado.
- Caminhos resolvidos dentro da raiz do laboratório, sem escapar por paths relativos, junctions/paths inesperados ou arquivo ausente.

O nome exato dos campos e suas invariantes devem ser conferidos no validador canônico do código — ArmoredStudio/speech_handoff.py e ArmoredSpeech/audio_package.py — ao alterar o contrato. Este manual documenta a semântica e o processo; não substitui os validadores tipados do contrato.

### 5.3 Ordem fail-closed

1. Carregar manifesto explicitamente completo, aprovado e com contagem consistente.
2. Validar identidades, caminhos dentro do root, existência dos originais e hash dos originais.
3. Abrir exclusivamente a base SQLite experimental indicada, validar decisões READY e preparar WAVs válidos.
4. Preparar todos os pacotes de áudio do lote e as duas variantes temporais por item.
5. Validar cada pacote e ambas as variantes. Se qualquer item falhar, nenhum objeto Studio deve iniciar análise visual/render.
6. Somente depois do lote Speech íntegro, executar a análise visual.
7. Escolher uma variante preexistente, validar a coerência do plano e o digest.
8. Reconfirmar que os originais não mudaram antes do primeiro render.
9. Renderizar para destinos novos, verificar existência/tamanho/streams e hashes dos originais.
10. Escrever um relatório de saída novo contendo estado, rotas/variantes, caminhos, metadados e evidências de integridade.

O contrato não é uma autorização de execução por si só: manifesto explicitamente aprovado, decisão READY, preflight válido, ativos exigidos e caminhos seguros continuam obrigatórios.

---

## 6. Rotas de áudio e variantes temporais são dimensões diferentes

Não confundir rota de áudio com variante temporal visual.

- **Rota de áudio** responde “que áudio Speech preparou para o item?”
- **Variante temporal** responde “como o timing de áudio já preparado se alinha com o tratamento visual escolhido para o vídeo?”

Por isso o caso real de banner registrou ao mesmo tempo BANNER_PRESENT e RVC_MELODY. O banner não força nem desliga RVC; a classe de áudio e os critérios de elegibilidade de Speech é que governam a rota.

### 6.1 Matriz de decisão esperada

| Classe/condição de áudio | RVC Melody | Saída esperada |
|---|---|---|
| PT_BR_NARRATION | Somente com fala PT-BR detectada e elegibilidade válida | RVC_MELODY |
| PT_BR_NARRATION_WITH_MUSIC | Somente após separação vocal validada e stem vinculado ao original | RVC_MELODY se elegível; o modelo não recebe a mistura bruta como se fosse voz isolada |
| MUSIC_ONLY | Nunca | EFFECT_ONLY; música original não vira narração Melody |
| NOISE | Nunca | EFFECT_ONLY; ruído não é reclassificado silenciosamente como fala |
| NO_AUDIO | Nunca | Faixa de voz/silêncio determinístico e efeito segundo o contrato |
| FOREIGN_SPEECH | Nunca para Melody PT-BR | EFFECT_ONLY, sem conversão indevida da fala estrangeira para Melody |
| NO_USABLE_NARRATION | Nunca | EFFECT_ONLY ou o comportamento explícito codificado pelo contrato; não inventar fala |
| UNKNOWN, decisão ausente ou contraditória | Não permitido | Bloquear a execução e reportar o erro |

A tabela descreve a política pretendida pelo código atual, enquanto a saída de cada execução deve ser julgada pelo registro real de classe/rota. rvc_candidate_eligible é candidatura, não aprovação final. rvc_eligible não pode se tornar verdadeiro sem a evidência exigida pela implementação, como a validação do stem para o caso com música. Um filtro passa-alta, redução de ruído ou mera mudança de volume não prova separação vocal.

### 6.2 Efeito sonoro

O efeito canônico pertence a ArmoredSpeech/assets/efeitosonoro.wav e à preparação de áudio de Speech. Não deve existir fallback operacional para ArmoredStudio/runtime/rvc nem voltar a copiar o efeito para a pasta de assets do Studio como se Studio fosse seu proprietário. A validação deve falhar explicitamente se o ativo obrigatório estiver ausente ou tiver integridade inválida.

---

## 7. Detector visual e seleção NO_BANNER/BANNER_PRESENT

### 7.1 O que o teste real demonstrou

Para storage/original_018.mp4, a análise visual retornou banner_detectado=true, confiança 90, mudança inicial 0.1 e estabilidade 0.0. A comparação SHA-256 confirmou que a análise não alterou o arquivo.

No E2E real, o relatório Studio confirmou:
- timeline_variant = BANNER_PRESENT;
- route = RVC_MELODY;
- saída válida com H.264/AAC;
- decodificação completa aprovada;
- cópia original íntegra;
- o usuário assistiu ao resultado e o aprovou visualmente.

Essa evidência prova que o detector, a seleção de variante, o contrato v5, o render e o pós-teste funcionaram juntos para esse arquivo.

### 7.2 Limite da pontuação do detector

O detector atual em analysis/banner_analyzer.py amostra frames no início (0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8 e 2.1 segundos), redimensiona frames e compara a diferença entre imagens. Ele classifica como banner quando a maior diferença das comparações iniciais fica abaixo do limiar 5 e a estabilidade posterior fica abaixo de 1.

O campo confiança=90 é o escore retornado pela implementação atual para esse ramo de regra; não é probabilidade estatística calibrada de 90%. Um vídeo com abertura visualmente estática também pode satisfazer uma heurística de estabilidade, por isso o relatório e a inspeção do resultado importam. Novas famílias de banner devem ter amostras representativas e medição de falsos positivos/negativos antes de generalizar a taxa de acerto.

### 7.3 Seleção da variante

O runner lê o resultado do detector no plano visual. Se banner_detectado ou o campo de compatibilidade detectado for verdadeiro, seleciona BANNER_PRESENT; caso contrário seleciona NO_BANNER. A variante precisa já existir no pacote v5. Studio não calcula de novo o timing de áudio. A variante selecionada e a rota devem ser reportadas separadamente.

O corpus de 17 vídeos permanece a evidência NO_BANNER: todos os 17 itens usaram essa variante. O vídeo original_018.mp4 é a evidência real separada de BANNER_PRESENT. As duas evidências são complementares, não contraditórias.

---

## 8. Ambiente local, ativos e caminhos canônicos

Ambiente local validado: Windows, Python 3.11.x na .venv compartilhada da raiz. RVC e demais dependências de áudio devem ser acessados por Speech, mantendo o runtime testado. Não criar uma segunda .venv nem substituir a atual sem uma justificativa concreta e uma migração validada.

| Recurso | Caminho canônico no laboratório | Proprietário/uso |
|---|---|---|
| Modelo RVC Melody | ArmoredSpeech/runtime/rvc/models/melody/ | Speech |
| Índices Melody | Junto ao conjunto de modelo canônico em ArmoredSpeech/runtime/rvc/models/melody/ | Speech; opcional conforme o modelo/configuração |
| Efeito de áudio | ArmoredSpeech/assets/efeitosonoro.wav | Speech |
| Banner visual | ArmoredStudio/assets/banner.png | Studio |
| Manifestos e relatórios | storage/validation/ | Evidência local |
| Bancos experimentais | storage/database/ com nome de laboratório explícito | Speech em teste |
| Vídeos originais/cópias/saídas | storage/ e namespaces criados pelo runner | Entrada e evidência local |
| Banco operacional | storage/database/armoredcreator.db | **Proibido no AudioLab** |

Modelos .pth, índices .index, efeito e banner podem ser ativos binários locais não versionados. Não substituir os arquivos oficiais por alternativas arbitrárias. Um índice pode ser opcional para determinados modelos, mas o conjunto RVC elegível precisa ser validado pelo runtime que o chama.

ArmoredSpeech/scripts/migrate_audio_assets.ps1 serve para reconciliar checkout antigo. Se o efeito já existir no caminho canônico de Speech, a migração deve verificá-lo; se houver dois arquivos diferentes, não sobrescrever automaticamente. A ferramenta de materialização de asset oficial deve conferir o hash e manter tokens fora do código e da documentação.

Para configurar um checkout realmente novo, seguir ArmoredSpeech/docs/SETUP_WINDOWS.md e as ferramentas de setup/migração do repositório. Não executar instalação ou migração de ativos novamente no checkout já validado sem necessidade.

---

## 9. Comandos de verificação e auditoria

Execute os comandos a partir da raiz de ArmoredAudioLab. Estas verificações não substituem o E2E; são a primeira camada para detectar regressões após mudanças de código.

### 9.1 Ambiente e integridade de código

~~~powershell
$Root = "C:\Users\Administrador\Downloads\ArmoredAudioLab"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

git -C $Root branch --show-current
git -C $Root rev-parse HEAD
git -C $Root status --short --branch

Push-Location $Root
try {
    & $Python -m compileall -q ArmoredSpeech ArmoredStudio armored_core
    if ($LASTEXITCODE -ne 0) { throw "compileall falhou." }

    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest falhou." }

    & $Python -c "import ArmoredSpeech; import ArmoredStudio.service; print('IMPORTS OK')"
    if ($LASTEXITCODE -ne 0) { throw "Importações falharam." }

    & $Python .\scripts\audit_audio_ownership.py --root .
    if ($LASTEXITCODE -ne 0) { throw "Auditoria de ownership falhou." }
}
finally {
    Pop-Location
}
~~~

O comando de ownership é somente leitura. Ele verifica referências/estruturas conhecidas; PASS não significa que todo cenário audiovisual real foi aprovado.

### 9.2 Inspeção somente de leitura dos relatórios já aprovados

O exemplo seguinte lê os dois relatórios registrados neste manual. Não chama Speech, não renderiza e não altera banco, manifesto ou saída.

~~~powershell
$Root = "C:\Users\Administrador\Downloads\ArmoredAudioLab"
$Reports = @(
    (Join-Path $Root "storage\validation\post_consolidation_corpus17_20261019T234723122Z\studio-report.json"),
    (Join-Path $Root "storage\validation\corpus1_banner_present_20261020T004009599Z\studio-report.json")
)

foreach ($Path in $Reports) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Relatório não encontrado: $Path"
    }

    $Report = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    Write-Output "REPORT=$Path"
    Write-Output "STATUS=$($Report.status)"
    Write-Output "MODE=$($Report.mode)"
    Write-Output "ITEMS=$(@($Report.processed).Count)"
    Write-Output "ORIGINALS_INTACT=$($Report.originals_intact)"

    foreach ($Package in @($Report.audio_packages)) {
        Write-Output "ITEM=$($Package.item_id) ROUTE=$($Package.route) TIMELINE=$($Package.timeline_variant)"
    }
}
~~~

Para o corpus são esperados 17 itens e as contagens de rota registradas na seção 2.2. Para o relatório de banner é esperado 1 item com timeline BANNER_PRESENT e rota RVC_MELODY. Se o relatório tiver sido movido para outro local, atualize o caminho de leitura — não recrie a execução só porque o caminho difere.

### 9.3 Modelo seguro para um novo lote explícito

O runner real requer manifesto aprovado e SQLite experimental correspondente. O destino do relatório e os IDs/paths de trabalho precisam ser novos. Exemplo estrutural — não é para reexecutar agora os cenários já aprovados:

~~~powershell
$Root = "C:\Users\Administrador\Downloads\ArmoredAudioLab"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Manifest = Join-Path $Root "storage\validation\<lote_novo>\studio-manifest.json"
$Database = Join-Path $Root "storage\database\<sqlite_experimental_correspondente>.sqlite3"
$Report = Join-Path $Root "storage\validation\<pasta_de_relatorio_nova>\studio-report.json"

if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) { throw "Manifesto ausente." }
if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) { throw "SQLite experimental ausente." }
if (Test-Path -LiteralPath $Report) { throw "Destino já existe; não sobrescrever." }

Push-Location $Root
try {
    & $Python -m ArmoredSpeech.studio_batch_runner --root $Root --manifest $Manifest --speech-database $Database --report $Report
    if ($LASTEXITCODE -ne 0) { throw "Lote Speech → Studio falhou; preserve a evidência." }
}
finally {
    Pop-Location
}
~~~

Esse é um modelo com placeholders, não um comando pronto para colar sem preencher. Nunca apontar para o SQLite operacional. Não usar --copy-only como prova de rota de áudio ou render audiovisual real.

### 9.4 Pós-validação de mídia

Para declarar um E2E aprovado, verificar no relatório que o status é SUCCESS e que os originais continuam íntegros. Em seguida, para cada saída:

- confirmar que o caminho é um arquivo válido dentro do root do laboratório e é diferente do original;
- utilizar ffprobe para confirmar streams, codec, resolução, frequência de amostragem e canais esperados;
- decodificar a saída completa com FFmpeg em modo estrito (por exemplo, -v error -xerror -f null -);
- comparar SHA-256 dos originais com a evidência coletada antes da execução;
- conferir a rota e a variante real no relatório, e não apenas o status geral do runner;
- se a inspeção visual/auditiva humana for necessária para o cenário, registrar explicitamente quem assistiu e qual arquivo foi avaliado.

Não assumir que sucesso de exit code implica imagem correta, áudio inteligível ou ausência de erro visual. Da mesma forma, não ignorar que uma saída pode ser válida tecnicamente e ainda precisar de avaliação sensorial.

---

## 10. Criação de corpus e preservação de evidências

O corpus runner evita descobrir e processar MP4s de forma ambígua. A pasta storage pode conter simultaneamente originais, cópias de trabalho, resultados renderizados e clipes de validação. Usar uma lista explícita, revisar nomes/ordem e exigir exatamente a quantidade esperada.

Para qualquer lote novo:

1. Escolher a entrada-fonte e calcular seu SHA-256 antes de copiar.
2. Gerar uma lista explícita em inputs JSON dentro da raiz do laboratório, apontando apenas para fontes escolhidas.
3. Usar batch-id novo; verificar que workspace, banco e diretório de relatório ainda não existem.
4. Copiar o arquivo byte a byte para um namespace de teste novo e conferir o hash da cópia.
5. Deixar Speech gerar as decisões no SQLite experimental novo.
6. Rever as decisões e os resultados READY antes de aprovar o manifesto para Studio.
7. Quando render for solicitado, gerar pacote de áudio e contrato v5; validar todas as variantes antes da primeira análise visual.
8. Executar Studio em saída nova e conservar o relatório, manifesto, banco, pacotes e vídeos finais.
9. Calcular hashes dos arquivos-fonte novamente e registrar resultado.
10. Não apagar uma execução com falha: a falha é evidência. Corrigir a causa com um lote novo em namespace novo.

O runner pode gerar cópias e pacotes de áudio; isso não o autoriza a modificar a entrada original. Resultados antigos nunca são destino temporário para novos testes.

---

## 11. Atualização de branch e preservação local

Este manual descreve o código versionado. Os dados locais ignorados pelo Git — especialmente .venv, storage, bases experimentais, originais, pacotes WAV, saídas, modelos/índices RVC e ativos binários — são parte essencial da evidência e devem continuar no disco.

Antes de sincronizar branch no Windows:

~~~powershell
git status --short --branch
git branch --show-current
git rev-parse HEAD
git diff --name-only
git diff --cached --name-only
~~~

Se houver alterações locais em arquivos versionados, revise-as antes de qualquer troca de branch. Não use reset para descartar alterações presumidamente dispensáveis.

Sincronização segura por fast-forward, depois de confirmar que as alterações locais versionadas foram resolvidas conscientemente:

~~~powershell
git fetch origin
$Target = "test/armoredstudio-speech-integration-20261015"
git switch $Target
git merge --ff-only "origin/$Target"

$LocalSha = (git rev-parse HEAD).Trim()
$RemoteSha = (git rev-parse "origin/$Target").Trim()
Write-Output "LOCAL_HEAD=$LocalSha"
Write-Output "REMOTE_HEAD=$RemoteSha"
if ($LocalSha -ne $RemoteSha) {
    throw "Checkout não está no mesmo commit que origin; parar e investigar."
}
git diff --exit-code "origin/$Target" -- .
if ($LASTEXITCODE -ne 0) {
    throw "Existem diferenças versionadas; não limpar automaticamente."
}
git status --short --branch
~~~

O comando pressupõe que a branch local já exista e seja a branch pretendida. Se não existir, use o fluxo de criação de tracking branch apropriado. Se ff-only recusar, investigar histórico; não force.

### O que preservar

- storage integralmente: originais, cópias, bancos SQLite, manifestos, logs, WAVs, pacotes, relatórios, vídeos finais e evidências de decode.
- .venv validada.
- ArmoredSpeech/runtime/rvc, modelos .pth e índices .index.
- ArmoredSpeech/assets/efeitosonoro.wav e ArmoredStudio/assets/banner.png.
- evidência histórica que ajude a comparar uma regressão.

Artefatos temporários como .pytest_cache e egg-info só podem ser removidos após confirmar que são gerados e dispensáveis. Nunca usar git clean -xfd, exclusões por curinga ou remoções recursivas genéricas nesta etapa.

---

## 12. Critérios de aceitação — estado atualizado

| Critério | Estado | Evidência ou observação |
|---|---|---|
| Branch isolada e separada do código operacional | APROVADO | Branch de laboratório e HEAD documentados |
| Contrato v5 fail-closed | APROVADO nos cenários executados | Validador, testes e runner real |
| Speech conclui lote antes da análise Studio | APROVADO nos E2Es | Runner prepara e valida todas as variantes antes de construir/analisar o Studio |
| Suíte automatizada e CI de consolidação | APROVADO | 127 testes locais; workflows de CI registrados na seção 2.1 |
| Corpus real completo de 17 vídeos após consolidação | APROVADO | 17/17 com pós-validação de streams, decode e hashes |
| Rota RVC_MELODY e EFFECT_ONLY no corpus | APROVADO tecnicamente | Distribuição real de 9 e 8 itens no relatório |
| Variante NO_BANNER | APROVADO com corpus real | 17 itens na timeline NO_BANNER |
| Variante BANNER_PRESENT com mídia real | APROVADO tecnicamente | E2E real de original_018.mp4 |
| Inspeção visual do resultado BANNER_PRESENT | APROVADO pelo usuário | Usuário confirmou que assistiu e ficou perfeito |
| Preservação do vídeo-fonte de banner | APROVADO | SHA-256 antes/depois igual |
| Uma única chamada FFmpeg para o render audiovisual final por item | Regra arquitetural coberta por código/testes | Operações preparatórias de WAV/RVC não são proibidas |
| Integração com o Coordinator e banco SQLite operacional | PENDENTE / fora do escopo do laboratório | Exige branch e auditoria própria; não tocar no banco operacional |
| Publicação no Hub | PENDENTE / proibida nesta fase | Requer gate operacional posterior |
| Generalização para todas as famílias possíveis de banner | PENDENTE | Uma amostra real de banner passou; outras famílias ainda precisariam de cobertura |
| Avaliação auditiva individual de todos os 17 resultados | Não declarada como concluída | Decode/streams passaram; isso não equivale a escuta humana de cada saída |

### Interpretação do estado

Não existe mais justificativa para bloquear o avanço do laboratório apenas por falta de um E2E BANNER_PRESENT: o cenário real já passou e foi visualmente aprovado pelo usuário. O trabalho restante é de integração operacional e de eventuais testes adicionais deliberados, não de repetir os mesmos E2Es aprovados.

---

## 13. Plano de integração futura — separado do laboratório

A transferência para o repositório operacional é uma tarefa nova. Não fazer cherry-pick/cópia cega apenas porque o E2E de laboratório passou.

1. Criar branch exclusiva no repositório operacional.
2. Auditar Coordinator, schema/estado SQLite, paths e interfaces atuais sem alterar o histórico real.
3. Adaptar a composição para Coordinator continuar sendo a única composition root, e SQLite continuar sendo a source of truth.
4. Preservar a sequência global Sync → Vision → Stock → IA → Speech → Studio → Hub.
5. Migrar ativos para os caminhos canônicos e eliminar fallback legado com testes de integridade; não duplicar ownership.
6. Garantir que o fluxo operacional exija completion de cada fase, e que Studio só comece após Speech completo.
7. Criar testes com SQLite temporário/cópias, incluindo falhas de preflight, caminho fora da raiz, WAV inválido, hash alterado, variante ausente, saída existente e render interrompido.
8. Executar suíte e CI no repositório operacional; não utilizar o banco real para testes destrutivos.
9. Validar casos representativos de RVC, música/voz separada, EFFECT_ONLY, NO_AUDIO e banner em um ambiente operacional de teste isolado.
10. Aprovar explicitamente qualquer publicação em Hub. O sucesso deste AudioLab não publica nada e não autoriza disparo em produção.

Não adicionar sistemas paralelos de fila nem resetar bancos/checkpoints para “facilitar” os testes.

---

## 14. Histórico curto e política de documentação

- PR #7: consolidação de helpers compartilhados e cobertura de testes; seus workflows de teste e diagnóstico passaram.
- PR #8: referências à branch e documentação operacional sincronizadas; o CI de testes no HEAD 23b398d passou.
- E2E de 17 vídeos: primeira execução completa na versão consolidada; pós-validação independente 17/17.
- E2E de banner: original_018.mp4 detetado como banner, variante BANNER_PRESENT escolhida, rota RVC_MELODY mantida, codecs e decode aprovados; usuário aprovou visualmente o render.
- Estado anterior “BANNER_PRESENT somente automatizado” está superado. Não o repetir nos documentos de índice.
- Relatórios, banco, manifesto e mídia ficam locais por design. Git mantém código e documentação, não os dados pesados do corpus.
- A documentação técnica antiga permanece como referência histórica apenas onde isso estiver explicitamente indicado. Em caso de divergência, a regra operacional presente neste manual e os validadores/testes do contrato v5 têm precedência.

**Conclusão:** o pipeline Speech → Studio isolado está comprovado no corpus de 17 vídeos e no caso real BANNER_PRESENT. O contrato v5 mantém o ownership de áudio em Speech e o ownership visual/render em Studio. A integração no ArmoredCreator operacional continua separada, deliberada e condicionada a auditoria própria.

---

## 15. Próxima fase do laboratório: ArmoredSync → ArmoredVision → ArmoredStock → ArmoredIA → Speech → Studio

**Status desta seção: auditoria estática inicial; nenhuma alteração foi feita no código ou banco operacional.**  
**Data da auditoria remota:** 20/10/2026 UTC / noite de 19/10 em São Paulo.

O objetivo da próxima fase é ampliar o AudioLab para verificar o fluxo desde a descoberta Telegram até a renderização Studio, mantendo-o separado do repositório operacional. A versão congelada é referência de comportamento; a versão atual fornece capacidades posteriores, mas nenhuma branch divergente deve ser copiada por inteiro sem revisar seu ancestral, seus testes e seu contrato.

### 15.1 Repositórios e referências consultados

| Referência | HEAD consultado | Função na auditoria |
|---|---|---|
| [armoredcreator-test / main](https://github.com/armoredcreator/armoredcreator-test/tree/main) | `385a9d49c1d5a07cd9c0fd76ed16a7e421500e5a` | Base congelada para comparar o Sync antigo e contratos históricos |
| [armoredcreator / main](https://github.com/armoredcreator/armoredcreator/tree/main) | `c9eeefe2449b5de26b3534c94090032313c9c8e8` | Implementação atual examinada em modo de leitura |
| [audio / branch de integração do laboratório](https://github.com/armoredcreator/audio/tree/test/armoredstudio-speech-integration-20261015) | Antes desta auditoria: `b07aed6af3f93730988d56c63ce9d9f0efebc990` | Base estável com Speech/Studio E2E aprovados |

Os hashes identificam os snapshots examinados; a auditoria não altera essas referências. Os ativos binários, bancos e vídeos locais não são clonados nem adicionados ao Git como parte desse inventário.

### 15.2 Achado prioritário: o dedupe atual é por fonte, não global

Na base congelada, `ArmoredSync/service.py` verifica se a URL original já existe em `items` comparando `LOWER(TRIM(original_url))`, **sem filtrar `source_id`**. Isso deduplica a mesma string de URL entre os conteúdos já registrados, mesmo quando os Telegram IDs são diferentes. É uma proteção limitada à URL textual exata, mas a regra é global. [Código congelado](https://github.com/armoredcreator/armoredcreator-test/blob/main/ArmoredSync/service.py#L258-L274).

Na versão atual, `ArmoredSync/service.py` consulta `items` com `WHERE source_id=? AND LOWER(TRIM(original_url))=LOWER(TRIM(?))`. Ou seja, o mesmo texto de link é considerado novo quando está em outra fonte. O teste atual `test_shopee_url_deduplication_is_source_scoped` declara esse comportamento como esperado: a URL já registrada na fonte 1 é marcada existente na fonte 1, mas não na fonte 2. [Implementação atual](https://github.com/armoredcreator/armoredcreator/blob/main/ArmoredSync/service.py#L302-L365) · [Teste multisource](https://github.com/armoredcreator/armoredcreator/blob/main/tests/test_multisource_routing.py#L76-L98).

Isso entra em conflito com o contrato de conteúdo global informado para o pipeline de várias fontes: a identidade da mensagem Telegram precisa continuar sendo `(source_id, telegram_message_id)`, mas a identidade do produto/link que não pode passar repetido para as fases de aprovação precisa ser global. Aumentar o número de fontes não deve aumentar o número de vezes que o mesmo produto passa por Vision/Stock/IA.

#### Há uma segunda lacuna, independente da primeira

A checagem atual compara apenas a URL recebida como texto, com trim e diferença de maiúsculas/minúsculas. Não compara uma identidade de produto canônica. URLs curtas, redirecionadas ou diferentes que terminem no mesmo produto podem escapar dessa verificação textual.

A Vision já consegue resolver uma URL e extrair `shop_id` e `item_id` por meio de `ArmoredVision/modules/v1/shopee_resolver.py`. Em seguida busca o produto exato e retorna campos de produto em `ia_context`. Porém, `ArmoredVision/service.py` não verifica se o mesmo par `(shopId, itemId)` já foi aprovado por outra fonte antes de retornar um novo `VisionResult`. [Resolver de produto](https://github.com/armoredcreator/armoredcreator/blob/main/ArmoredVision/modules/v1/shopee_resolver.py) · [Identificação Vision](https://github.com/armoredcreator/armoredcreator/blob/main/ArmoredVision/service.py#L41-L116).

A tabela operacional, por sua vez, tem uma identidade única para `(source_id, telegram_message_id)`, não para URL nem produto. Isso protege contra a ingestão repetida da mesma mensagem Telegram, mas não protege contra duas mensagens/fontes que apontem para o mesmo produto. [Migração/índice de identidade da base](https://github.com/armoredcreator/armoredcreator/blob/main/armored_core/database.py#L155-L213).

**Conclusão inicial:** os duplicados entre fontes são uma lacuna real no contrato implementado, confirmada por código e teste. URLs distintas para o mesmo produto também podem passar porque falta uma barreira global por identidade canônica. A auditoria estática demonstra essa possibilidade; quantos duplicados históricos existem e quais registros são equivalentes só pode ser medido em uma cópia/snapshot do SQLite, sem inferir quantidades antes da consulta.

### 15.3 Política de identidade a implementar e testar no laboratório

Não confundir identidade de mensagem, identidade de URL e identidade de produto. São três chaves com funções diferentes:

1. **Identidade de mensagem:** `(source_id, telegram_message_id)`. Continua sendo a chave de idempotência para reprocessar a mesma mensagem Telegram e preservar topic/checkpoints.
2. **Identidade de URL de entrada:** chave normalizada conservadora, global entre fontes, para detectar o mesmo link textual apresentado com diferenças de formatação que sejam comprovadamente irrelevantes. Não remover parâmetros de URL por palpite; cada normalização precisa de testes explícitos.
3. **Identidade canônica de produto:** `(shop_id, item_id)`, obtida pelo resolver Shopee. Esta é a barreira global definitiva antes de aceitar o produto para download/Stock e IA. Links curtos diferentes que resolvem para o mesmo produto devem convergir à mesma identidade.

O hash SHA-256 de um vídeo pode ser recolhido como sinal de auditoria de mídia repetida, mas não deve, sozinho, se transformar em regra de supressão de produto: dois posts podem conter o mesmo vídeo por motivos diferentes. Dedupe de produto, dedupe de link e cópia de mídia idêntica devem continuar categorias distintas no relatório.

#### Gate exigido no pipeline

- Sync bloqueia novamente links globais já representados, sem condicionar essa verificação a `source_id`.
- Vision obtém a identidade canônica e, **antes de declarar o item aprovado para Stock**, verifica/claim essa identidade em SQLite de forma durável e global.
- Stock só materializa itens que possuem aprovação Vision válida; duplicado, URL não resolvida e oferta ausente não baixam um segundo original.
- A reserva de identidade deve ser transacional para que duas fontes nunca possam vencer simultaneamente. Não adicionar imediatamente um índice UNIQUE a uma base que já contém duplicados históricos; primeiro inventariar e planejar migração. A regra nova precisa ser testada em bancos descartáveis e em cópias de referência.
- WAITING_VISION continua sendo persistente e não deve ser automaticamente retentado no startup. Para uma URL conhecida que já está em WAITING_VISION, o guard de deduplicação impede criar uma segunda entrada equivalente. A recuperação de um mesmo registro não é um novo item.
- Ao encontrar duplicado na fase nova, o pipeline deve registrar a razão e a referência do item canônico de forma auditável; não apagar nem fundir silenciosamente registros históricos.

Critérios mínimos de teste: URL idêntica em fonte 1/2/3; IDs Telegram iguais em fontes diferentes; URLs diferentes que resolvem para o mesmo `shop_id/item_id`; mesmo produto em mensagens de tópico/álbum; primeira ocorrência em WAITING_VISION; registro aprovado existente; falha de Vision/Stock; candidato em RECOVERY; reinício do processo; checkpoints CATCH-UP por fonte; e transação concorrente de claim. O item original e o histórico `state_events` devem permanecer intactos.

### 15.4 Outra divergência a resolver: vídeo sem link Shopee

Na `main` atual, Sync também reserva vídeos sem URL atribuível e os encaminha à Vision para ficarem em WAITING_VISION. Os testes `test_video_without_shopee_link_is_still_reserved_for_vision` e `test_album_without_links_keeps_each_video_for_vision` explicitamente esperam esses candidatos. [Testes atuais](https://github.com/armoredcreator/armoredcreator/blob/main/tests/test_sync_topic_discovery.py#L211-L256).

Existe uma branch `fix/vision-only-linked-telegram-videos` que muda a regra para não criar candidato Vision sem link; no momento desta auditoria ela está divergente da `main` (2 commits à frente e 13 atrás). Não fazer cherry-pick nem merge dessa branch inteira como atalho. Ela é evidência de uma proposta de contrato diferente, não uma implementação diretamente integrável.

Contrato seguro para a migração: **nenhum vídeo sem link atribuível pode ser aprovado por Vision ou baixado por Stock**. Se uma reserva WAITING_VISION sem link for mantida para permitir enriquecimento posterior, precisa ser explicitamente uma reserva pendente, sem aprovação nem download, e o vínculo futuro deve atualizar o mesmo item idempotente — não criar uma segunda identidade. Se for escolhido o comportamento congelado (pular inteiramente itens sem link), o enriquecimento posterior precisa ser tratado como caso de uso separado, também testado. Essa decisão deve ser coberta por testes antes de modificar o fluxo. Não transformar um item sem link em “aprovado” nem inventar a associação entre um vídeo e um link distante.

### 15.5 Auditoria de sobreposição de módulos e do Core

O inventário das árvores demonstra que as três versões não são intercambiáveis em bloco:

- A base congelada contém Sync, Vision e IA, mas não possui o módulo ArmoredStock independente nem o provider Ollama.
- A `main` atual acrescenta MultiTelegramSource, roteamento por três fontes, ArmoredStock, classificação de falhas Vision e Ollama; também alterou significativamente o Core e os contratos ao longo de várias branches.
- O AudioLab contém Speech/Studio e helpers compartilhados, mas ainda não contém a implementação operacional de Sync/Vision/Stock/IA, nem o Core completo de produção (Database, SyncService, Coordinator, Pipeline, Recovery, routing e gates de catch-up).
- `armored_core/models.py` no AudioLab e na `main` operacional é byte a byte idêntico. Vários arquivos visuais também são idênticos. Isso é uma boa base para integração, mas não significa que os pacotes completos tenham a mesma API.
- `armored_core/storage.py` difere apenas na regra de mapeamento do identificador de fonte para pasta física: o AudioLab permite namespaces isolados como `source_teste`; o código operacional se concentra em `source1`, `source2`, etc. A consolidação deve preservar os dois usos através de uma única função testada, não manter implementações paralelas nem retirar o namespace de testes.
- AudioLab tem `armored_core/path_utils.py`, helper de contenção de caminhos que não está presente na mesma forma na `main` operacional. O helper deve ser aproveitado/adaptado ao integrar módulos que abrem/copi am arquivos.
- ArmoredStudio no AudioLab separou execução de áudio para Speech. Não substituir seu `unified.py`, `service.py` ou finalizer pelas cópias operacionais sem revisão: isso poderia reintroduzir um segundo dono de RVC/efeito/volume/timeline.

**Regra:** unificar implementações onde a API e a semântica são realmente iguais; onde diferirem, escrever um wrapper/adapter e teste de contrato, não manter duplicados silenciosos nem eliminar módulos baseado apenas no nome.

Branches de correção relacionadas à visão, URL canônica, catch-up e gates estão divergentes entre si e da `main`. Exemplo: `fix/shopee-canonical-product-url` tem mudanças úteis no resolver, mas seu ancestral está 22 commits atrás da `main`; `fix/received-items-resume-normal-pipeline` também é divergente. Usar commits/diffs como evidência pontual, reimplementando somente o comportamento aprovado na branch do AudioLab; nunca mesclar essas branches inteiras sem reconciliar a história e os testes.

### 15.6 Dependências e runtime: conflito que precisa ser resolvido antes de instalar

A exigência operacional para a próxima fase é Python **3.11.9** e um só ambiente compartilhado do AudioLab para Sync, Vision, Stock, IA, Speech e Studio. O setup ainda não está configurado para garantir esse requisito:

- `pyproject.toml` do AudioLab declara apenas `opencv-python-headless==4.10.0.84` e `imageio-ffmpeg==0.6.0`, e `requires-python = ">=3.11"`.
- O workflow CI do AudioLab usa `python-version: "3.11"`, sem fixar o patch 3.11.9.
- `ArmoredSpeech/requirements-analysis.txt` fixa `numpy==1.23.5`.
- `requirements.txt` operacional fixa um intervalo incompatível com isso: `numpy>=1.26,<3`. Esses dois requisitos não podem ser resolvidos juntos sem revisão e validação.
- O Sync/Vision/IA operacional utiliza dependências adicionais como Telethon, Requests e python-dotenv. O provider Ollama usa HTTP local por Requests, portanto o binário/daemon Ollama e o modelo local também precisam ser verificados separadamente do pacote Python.

Não instalar agora todos os requirements no `.venv` que já passou os testes de Speech/Studio. A próxima etapa de dependências é resolver as restrições num CI/ambiente descartável com Python 3.11.9, rodar a suíte completa e repetir os diagnósticos de áudio antes de atualizar o ambiente local validado. Se o motor RVC exigir um runtime separado, sua versão só muda depois de teste real de compatibilidade; não fazer upgrade especulativo.

### 15.7 ArmoredIA local via Ollama

A `main` operacional já contém `ArmoredIA/providers/ollama.py`, configuração `ARMORED_IA_PROVIDER=ollama` e os testes automatizados em `tests/test_ollama_provider.py`. O gerador comum aplica o mesmo `CaptionSelector`/Policy às opções do provider; não deve haver política permissiva específica para Ollama nem fallback silencioso para Gemini se o modelo local falhar.

O que os testes mockados demonstram: estrutura JSON, endpoint configurável, extração de candidatas e integração da seleção/policy. **Eles não comprovam que o daemon Ollama está instalado/iniciado na máquina, que o modelo configurado está baixado, nem que a inferência real termina no tempo permitido.** Para o gate local, verificar endpoint, nomes de modelos existentes, um teste curto de geração com o modelo realmente instalado e depois a bateria de legendas com validação pela policy compartilhada. Não exigir GEMINI_API_KEY quando Ollama estiver explicitamente selecionado; erros de modelo/daemon devem ser explícitos e rastreáveis.

### 15.8 Sequência de implementação no AudioLab

A ordem de trabalho, preservando a ordem global do projeto, será:

1. **Snapshot e inventário read-only:** preservar HEADs, testar apenas em SQLite descartável/cópia consistente, registrar a distribuição de duplicados por URL textual e produto canônico. Nenhum `UPDATE`, `DELETE`, `VACUUM`, reset de checkpoint ou reinício de coleta histórica no banco operacional.
2. **Contrato de identidade e testes red:** definir chaves de mensagem, URL e produto; reproduzir os casos cross-source e aliases de URL antes da correção. Registar separadamente reserva sem link, WAITING_VISION e aprovação Vision.
3. **ArmoredSync no laboratório:** adaptar descoberta/catch-up, fóruns/álbuns, anexação de link, fontes múltiplas, retries e checkpoints preservando CATCH-UP → LIVE. O dedupe global começa antes de criar candidatos repetidos.
4. **ArmoredVision:** reaproveitar o resolver/API V1 atual revisados; resolver produto canônico e aplicar gate global de identidade antes de aprovar o item. Visão não deve aprovar duplicados, criar links de afiliado para um segundo item equivalente ou solicitar download em nome deles.
5. **ArmoredStock:** manter a fase obrigatória do fluxo operacional entre Vision e IA. Só materializar um item aprovado; verificar tamanho/hash, nunca sobrescrever ORIGINAL, e bloquear duplicate/unresolved.
6. **ArmoredIA/Ollama:** importar o provider e a Policy/Selector compartilhados, resolver dependências, testar endpoint/modelo local real e garantir o mesmo contrato de legendas para Ollama/Gemini.
7. **Speech → Studio v5:** adaptar o item aprovado do Core ao pacote de Speech já validado e ao finalizer Studio atual. O conjunto de estados de pipeline e as decisões de áudio devem permanecer no mesmo SQLite laboratorial; `armored_speech_results` pode ser uma tabela adicional nesse mesmo arquivo, em vez de uma segunda fonte de verdade. Este desenho precisa de teste de integração antes de declarar o E2E totalmente unificado.
8. **E2E isolado de ponta a ponta:** fixture controlada de entradas, três fontes fake/offline ou sandbox de Telegram deliberada, SQLite de laboratório novo, rota de dedupe, Vision, Stock, IA real local, Speech, NO_BANNER/BANNER_PRESENT e render. O Hub/publicação fica desligado; um publisher de teste deve falhar se chamado.
9. **Fechamento documental:** acrescentar caminhos de relatório, hashes, versões, classes/rotas, motivos de dedupe e resultados observados ao manual único. Só depois considerar uma auditoria para futura portabilidade ao repositório operacional.

### 15.9 Proteção do histórico operacional — não negociável

- O banco existente em `storage/database/armoredcreator.db` do projeto operacional não é banco de teste.
- Nenhum código do laboratório deve apontar para ele implicitamente, migrá-lo, abrir conexão de escrita, regravar originais, zerar checklists/checkpoints, reiniciar catch-up histórico ou limpar estados para testar dedupe.
- O diagnóstico de duplicados históricos deve ler uma cópia consistente e gerar relatórios externos com grupos e razões. Uma ocorrência duplicada já salva não pode ser apagada só para satisfazer uma constraint.
- Cada registro histórico conserva seu conteúdo, IDs de mensagem/fonte, eventos e original. Uma futura reconciliação deve apontar explicitamente o item canônico e o alias/duplicado, preservando o histórico e sem republicar/rebaixar itens silenciosamente.
- A primeira correção altera comportamento de candidatos **novos** no laboratório. A reconciliação de linhas já coletadas é uma tarefa separada, medida e revisada, depois de provar o algoritmo em uma cópia.
- Não há publicação, acesso ao Hub de produção, nem merge no repositório operacional neste plano.

### 15.10 Gates para considerar esta fase concluída

- Um URL já representado numa das três fontes não chega a ser aprovado novamente por estar noutra fonte.
- URLs diferentes resolvidas para o mesmo `shop_id/item_id` não podem ter duas aprovações Vision ativas/globalmente aceitas.
- A mesma mensagem reiniciada é idempotente; fontes e IDs Telegram distintos continuam auditáveis.
- WAITING_VISION é persistente, não é aprovado sem oferta canônica e não é retentado automaticamente; RECOVERY continua possível sem duplicar a identidade.
- Stock não baixa item sem aprovação Vision, não repete download de duplicado, não sobrescreve ORIGINAL e verifica integridade.
- IA local funciona com o modelo/daemon realmente instalados e passa pela Policy/Selector compartilhada sem regras especiais nem fallback de provider.
- Runtime do projeto na .venv é exatamente Python 3.11.9, dependências resolvem sem conflito e os testes de Speech/Studio continuam passando. A compatibilidade RVC é comprovada separadamente se o runtime tiver de permanecer isolado.
- Um E2E novo isolado completa Sync → Vision → Stock → IA → Speech → Studio, com logs/relatórios reproduzíveis, SQLite local única e publicação desativada.
- Nenhum banco, original, evento histórico, checkpoint ou saída operacional é apagado ou modificado.
- Somente depois desses gates será atualizada a conclusão do manual e considerada uma nova revisão para integração operacional.

**Estado no momento:** o E2E Speech → Studio está comprovado no AudioLab. Sync/Vision/Stock/IA ainda não foram portados nem executados nesse ambiente; os achados da auditoria aqui são conclusões estáticas verificadas no código e nos testes, não um relatório ainda não executado da base SQLite histórica. A próxima implementação é dedupe global e identidade canônica em testes do laboratório, não alteração do banco operacional.


### 15.11 Auditor automático de duplicados — primeiro gate executável

O script scripts/audit_sync_duplicates.py foi adicionado ao AudioLab com testes automatizados. Ele **mede antes de corrigir**; não muda a política de Sync, não aprova itens e não faz reconciliação.

CI do PR #11:
- [Push — testes do auditor](https://github.com/armoredcreator/audio/actions/runs/39352091801): compileall e pytest passaram.
- [Pull request — testes do auditor](https://github.com/armoredcreator/audio/actions/runs/39352107274): compileall e pytest passaram.
- Merge do código: a2af33c378c12a4599c7fe8bf6de3cb541575d86.

#### O que o script faz

1. Recebe um caminho de banco de dados explicitamente informado; não escolhe o SQLite por busca automática.
2. Abre a origem usando SQLite mode=ro e PRAGMA query_only=ON.
3. Usa a API de backup do SQLite para criar source-snapshot.sqlite3 numa pasta nova em storage/validation.
4. Fecha a conexão de origem e só então lê/anota os dados a partir do snapshot.
5. Analisa grupos de URL textual (normalização conservadora: trim + case-fold; não remove parâmetros) e grupos de identidade canônica (shop_id,item_id), usando contexto Vision persistido ou IDs em URL explícita. Não faz chamadas HTTP para resolver links curtos.
6. Gera duplicate-audit.json, duplicate-groups.csv, duplicate-items.csv e unlinked-items.csv, registrando o hash do snapshot, esquema, avisos e contagens.
7. Bloqueia diretórios de relatório fora de storage/validation e recusa sobrescrever uma pasta já existente.

Arquivos sem ia_context_json/campo equivalente podem ter identidade canônica extraída apenas quando o URL contém IDs de produto diretamente. Links curtos que ainda não possuam identidade Vision persistida ficam **não resolvidos**, não são adivinhados. Os CSVs incluem URLs originais; são evidência local e não devem ser colados em tickets públicos nem adicionados ao Git.

#### Execução controlada no Windows

Primeiro atualize o checkout local para receber scripts/audit_sync_duplicates.py. Não continue se houver alterações locais em arquivos versionados. Não use reset nem clean.

~~~powershell
$Root = "C:\Users\Administrador\Downloads\ArmoredAudioLab"
$Target = "test/armoredstudio-speech-integration-20261015"

if ((git -C $Root branch --show-current).Trim() -ne $Target) {
    throw "Branch incorreta. Não alterei o checkout."
}
$Dirty = (git -C $Root status --porcelain --untracked-files=no | Out-String).Trim()
if ($Dirty) { throw "Há alterações locais versionadas. Revise antes de atualizar." }

git -C $Root fetch origin
if ($LASTEXITCODE -ne 0) { throw "git fetch falhou." }
git -C $Root merge --ff-only "origin/$Target"
if ($LASTEXITCODE -ne 0) { throw "Fast-forward falhou; pare sem usar reset." }

$LocalSha = (git -C $Root rev-parse HEAD).Trim()
$RemoteSha = (git -C $Root rev-parse "origin/$Target").Trim()
"LOCAL_HEAD=$LocalSha"
"REMOTE_HEAD=$RemoteSha"
if ($LocalSha -ne $RemoteSha) { throw "HEAD local e remoto divergem." }

git -C $Root status --short --branch
~~~

Depois de confirmar que a aplicação aponta ao banco correto, execute a auditoria. O caminho abaixo corresponde ao checkout operacional local conhecido neste ambiente; ajuste apenas o caminho da origem se sua pasta tiver outro nome. O parâmetro --database precisa indicar o arquivo SQLite real desejado, não uma base de teste vazia.

~~~powershell
$ErrorActionPreference = "Stop"
$Root = "C:\Users\Administrador\Downloads\ArmoredAudioLab"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$SourceDb = "C:\Users\Administrador\Downloads\ArmoredCreator-final\storage\database\armoredcreator.db"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python do AudioLab não encontrado: $Python"
}
if (-not (Test-Path -LiteralPath $SourceDb -PathType Leaf)) {
    throw "SQLite de origem não encontrado. Corrija o caminho explicitamente; nenhum banco foi criado."
}

$Stamp = [DateTime]::UtcNow.ToString("yyyyMMdd'T'HHmmssfff'Z'")
$ReportDir = Join-Path $Root "storage\validation\sync_duplicate_audit_$Stamp"
if (Test-Path -LiteralPath $ReportDir) {
    throw "Diretório de evidência já existe; não sobrescrever."
}

Push-Location $Root
try {
    & $Python .\scripts\audit_sync_duplicates.py --root $Root --database $SourceDb --report-dir $ReportDir
    if ($LASTEXITCODE -ne 0) {
        throw "Auditoria falhou. Preserve qualquer artefato criado; não repita no mesmo diretório."
    }
}
finally {
    Pop-Location
}

$ReportPath = Join-Path $ReportDir "duplicate-audit.json"
$Report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
Write-Output "=== CONTAGEM DA AUDITORIA (sem URLs) ==="
$Report.counts | Format-List
Write-Output "REPORT=$ReportPath"
Write-Output "SNAPSHOT=$(Join-Path $ReportDir 'source-snapshot.sqlite3')"
~~~

A auditoria executa apenas leitura sobre a origem e escreve o snapshot/relatórios no AudioLab. O backup SQLite oferece um snapshot consistente mesmo se a origem for usada por outro processo; se SQLite informar busy, locked, erro de I/O ou falha de backup, não force a operação nem tente migrar o arquivo — preserve a mensagem, execute em período menos movimentado ou use um snapshot consistente previamente produzido pelo procedimento de backup aprovado.

#### Como interpretar o relatório

- url_duplicate_groups: grupos com mais de um content_id usando a mesma URL textual conservadoramente normalizada.
- url_duplicate_groups_with_2plus_vision_approved: desses grupos, quantos têm pelo menos dois itens com affiliate_url persistida — um sinal de aprovação Vision a investigar, não evidência suficiente para apagar nada.
- canonical_product_duplicate_groups: grupos com mais de um content_id para o mesmo (shop_id,item_id) encontrado em contexto Vision ou em URL de produto explícita.
- canonical_product_duplicate_groups_with_2plus_vision_approved: grupos de identidade de produto com duas ou mais URLs afiliadas persistidas.
- items_without_original_url: reservas sem URL; devem ser examinadas separadamente e não podem ser consideradas produtos duplicados sem identidade suficiente.
- warnings: schema legado, ausência de contexto Vision ou JSON malformado que limite a capacidade da análise.

Uma mesma família pode aparecer nos dois tipos de grupo, pois URL e identidade de produto medem coisas distintas. As contagens de grupos não devem ser somadas para estimar total de itens duplicados sem deduplicar os content_id. O script não resolve URLs encurtadas via rede e não infere IDs ausentes.

**O que enviar de volta:** somente a saída resumida do PowerShell e warnings do JSON. Não envie o snapshot SQLite, credenciais nem os CSVs com URLs completos pelo chat. O resultado servirá para definir a correção para novos candidatos no AudioLab; não altera nem reconcilia a coleta histórica.

**Limite atual:** a ferramenta foi testada no CI com bases sintéticas e schemas de exemplo. A primeira auditoria sobre a base local real ainda precisa ser executada. Nenhum número de duplicados reais é declarado até essa execução retornar.

## 16. Migração coordenada do pipeline completo (20/10/2026)

A branch de trabalho isolada `feat/full-pipeline-migration-20261020` deriva da integração validada `test/armoredstudio-speech-integration-20261015` no commit base `15733ce80c25184a18f108afdd2d0b57e4531a1c`. A migração não modifica o repositório de produção nem a referência congelada.

### Escopo trazido para auditoria no AudioLab

- `ArmoredSync`: descoberta Telegram, histórico, checkpoints, tópicos e idempotência.
- `ArmoredVision`: resolvedor Shopee, API de afiliados, classificação e contexto canônico do produto.
- `ArmoredStock`: materialização/download do original e comportamento de falha.
- `ArmoredIA`: política de legenda compartilhada e provedores Gemini/Ollama.
- Core de produção ausente no AudioLab: banco SQLite, serviços, pipeline, Coordinator, Recovery, auditoria de startup, roteamento, catch-up e trace. Os modelos são idênticos pelo SHA já auditado; `armored_core/storage.py` do laboratório é preservado porque mantém a adaptação de caminhos do AudioLab e precisa ser reconciliado por testes, não sobrescrito às cegas.
- Testes de regressão relevantes dos módulos foram copiados para `tests/`; a descoberta de testes cobre esse diretório e `ArmoredSpeech/tests/`.

### Sequência de validação obrigatória

1. Core/SQLite: schema, estados, eventos, leases, checkpoints, modelos e caminhos; teste em banco temporário.
2. Sync isolado: fontes controladas, identidade de mensagem por `(source_id, telegram_message_id)`, deduplicação de link global, histórico idempotente e transição `CATCH-UP -> LIVE`.
3. Vision isolada: links ausentes/encurtados, classificação, erros recuperáveis e identidade `(shopId, itemId)`.
4. Stock isolado: descarga única, original imutável, hash, arquivo parcial e recuperação.
5. IA isolada: política única de legenda, provedores e inferência real com Ollama local; testes simulados não contam como prova da inferência.
6. Integração na ordem Sync -> Vision -> Stock -> IA -> Speech -> Studio, com Hub/publicação desligados.
7. Corpus de ponta a ponta, verificação de hash dos originais, saída H.264/AAC e preservação de relatórios/manifests/pacotes.

### Regras de segurança

- Não usar a base operacional como base de testes; as execuções funcionais devem usar bancos temporários do AudioLab.
- A auditoria SQLite existente é exclusivamente de leitura sobre a origem e produz snapshot em `storage/validation`.
- Não apagar, fundir, reprocessar ou alterar os 19.966 registros históricos para fazer testes passarem. Os grupos encontrados são evidência para análise, não autorização para modificar itens.
- Não adicionar um índice UNIQUE diretamente a todas as linhas históricas sem reconciliação. A deduplicação nova deve ser global e atômica, preservando a identidade da mensagem, registrando candidatos duplicados e impedindo que uma segunda cópia chegue ao Stock.
- O Core compartilhado é o único proprietário da coordenação e das transições de estado; ferramentas específicas não devem manter bancos/checkpoints paralelos.
- Preservar `.venv`, Python local 3.11.9, runtime/modelos RVC e assets. Não instalar `requirements.txt` de produção por inteiro: as dependências nativas/NumPy de áudio precisam continuar isoladas e resolvidas explicitamente.
- Nenhuma publicação no Hub durante esta migração. Produção e referência permanecem imutáveis até que o laboratório passe os critérios acima.

### Estado desta entrega

Esta revisão adiciona os módulos de entrada e a suíte de regressão ao AudioLab. Ela não declara a migração concluída: a primeira execução de CI após a importação deve expor incompatibilidades, que precisam ser corrigidas em commits/PRs de seguimento antes da integração final.

#### 16.1 Proteção global de links e identidade canônica

A branch de migração adiciona uma correção com testes:

- O Sync consulta a URL normalizada globalmente, não apenas no `source_id` atual.
- Depois que Vision retorna `shopId` e `itemId` exatos, o Core cria uma reserva atômica SQLite por identidade canônica. Antes de criar uma reserva nova, consulta registros históricos que já tenham `affiliate_url` e contexto Vision persistido.
- Uma nova mensagem que representa um produto já reivindicado fica no estado durável `DUPLICATE`, com `duplicate_of_content_id`, `state_events` e `last_error`. A linha da mensagem é preservada e não recebe `affiliate_url`, impedindo avanço para Stock/IA/Speech/Studio.
- A criação da tabela de reserva não apaga, funde nem reescreve grupos históricos. O owner histórico é escolhido deterministicamente por `created_at, content_id`, apenas para bloquear novas cópias.
- URLs encurtadas sem identidade retornada pelo Vision não são classificadas como duplicadas por adivinhação.

Esta proteção só pode ser considerada aprovada depois dos testes CI e dos testes locais do AudioLab. Não deve ser executada sobre o banco operacional como teste de migração.

#### 16.2 Estados explícitos de hand-off Speech e saída segura do Studio

O Core do AudioLab oferece agora os estados duráveis `SPEECH` (IA concluída, aguardando o lote Speech) e `STUDIO_COMPLETE` (renderização concluída, publicação proibida nesta execução de laboratório). `Pipeline.run(..., stop_after_ia=True)` persiste o caption e termina em `SPEECH`, sem chamar Studio. `Pipeline.run(..., stop_after_studio=True)` grava o resultado, termina em `STUDIO_COMPLETE` e retorna antes do bloco de Hub/publicação. Reentrada nesses estados é bloqueada, impedindo que a recuperação pule etapas ou publique acidentalmente.

O runner de contrato v5 do Speech/Studio continua sendo a implementação canônica de preparação de áudio, variantes temporais, validação de pacotes e renderização; seus manifests aceitam o estado novo `SPEECH` e continuam aceitando `IA` para fixtures legadas. Isso não substitui o teste real de Speech/RVC/Ollama: esses componentes precisam de runtime/assets locais e são verificados separadamente.

Os testes de estágio usam SQLite temporário e verificam que a saída no limite Studio não chama Publisher, que o original permanece idêntico e que estados de hand-off não repetem trabalho.

#### 16.3 Executor E2E isolado para um lote explícito de fixtures

O comando `scripts/run_lab_pipeline.py` executa a sequência Sync -> Vision -> Stock -> IA -> Speech -> Studio para um manifest JSON revisado pelo operador. Não percorre o histórico Telegram: Sync lê somente os caminhos e links listados no manifest. As três fontes são namespaces fictícios únicos por execução, e Core SQLite + Speech SQLite + relatórios ficam em `storage/validation/full_pipeline_<run-id>/`. Os originais de entrada são hash-verificados antes/depois, e Stock copia cada vídeo para workspace de teste; o manifest de entrada nunca é usado como banco.

O executor constrói `Coordinator` com `publisher=None` e não importa ArmoredHub. Os hand-offs pós-IA e pós-Speech usam estados persistidos `SPEECH` e `STUDIO_BATCH`; uma renderização v5 bem-sucedida termina em `STUDIO_COMPLETE`. Se uma etapa falhar, o relatório, SQLite, WAVs, pacotes e saídas parciais permanecem para auditoria; nenhum cleanup em massa é realizado.

O modo CLI real exige `--approve-real-processing`, credenciais Shopee, Ollama/modelo local (por padrão), FFmpeg/FFprobe, asset de efeito e modelos de análise de áudio disponíveis. O preflight real ocorre antes de reservar candidatos. O executor não é um comando para coletar novamente os grupos Telegram reais nem para usar `ArmoredCreator-final/storage/database/armoredcreator.db`; só use vídeos de teste explicitamente selecionados dentro do AudioLab.

Exemplo de invocação após preencher uma cópia local do template `docs/lab-pipeline-input.example.json` com os vídeos que quer testar e URLs Shopee válidas:

~~~powershell
$Root = "C:\Users\Administrador\Downloads\ArmoredAudioLab"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Manifest = Join-Path $Root "storage\validation\lab-input-reviewed.json"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python do AudioLab ausente." }
if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) { throw "Manifest revisado não encontrado." }
& $Python (Join-Path $Root "scripts\run_lab_pipeline.py") --root $Root --manifest $Manifest --ia-provider ollama --approve-real-processing
if ($LASTEXITCODE -ne 0) { throw "Pipeline de laboratório falhou; preserve o run_dir e consulte pipeline-report.json." }
~~~

Os testes sintéticos do executor injetam Vision/IA/Speech/Studio determinísticos para verificar estado, ordem, deduplicação e integridade sem chamar rede, Ollama ou RVC. Eles não substituem a execução local real do executor.

#### 16.4 Enriquecimento tardio de link

Quando uma reserva existente está em `WAITING_VISION` sem URL e uma nova passagem do Sync descobre o link para a mesma identidade `(source_id, telegram_message_id)`, o Core tenta enriquecer a reserva existente. Se a URL já estiver representada por outro item, a atualização do link e a transição para `DUPLICATE` ocorrem na mesma transação SQLite; o Sync não pode voltar a transicionar o item para `RECEIVED` com base em um estado antigo lido antes da atualização. O ID original da reserva e os eventos permanecem preservados.

#### 16.5 Ownership local dos assets de RVC/áudio

O código de `ArmoredSpeech/rvc_engine.py` já define `ArmoredSpeech/runtime/rvc/models` como caminho canônico, e o WAV de efeito fica em `ArmoredSpeech/assets/efeitosonoro.wav`. Os arquivos são binários locais ignorados pelo Git; por isso, a migração não os transporta pelo repositório. O script `scripts/migrate_rvc_assets_to_speech.ps1` copia apenas modelos/índices RVC e o efeito sonoro canônico. Ele não copia o ambiente Python legado do Studio, banner, credenciais, bases, vídeos ou outputs; não remove arquivos do Studio e não sobrescreve destinos divergentes. Primeiro execute sem `-Apply` para auditar plano/conflitos e hashes; só use `-Apply` depois de revisar o relatório.





---

## 16. Atualização da auditoria integrada — 21/10/2026

Esta seção complementa e prevalece sobre afirmações antigas da seção 15 que diziam que nenhuma alteração havia sido feita no código. O trabalho desta etapa está isolado no repositório `armoredcreator/audio`; não foi promovido para a aplicação operacional `armoredcreator/armoredcreator`.

### 16.1 Correção de Sync implementada e validada em CI

- Branch isolada: `fix/audit-blockers-sync-preservation-20261021`.
- PR em rascunho: [#15 — Preserve cross-source duplicate identity in SQLite](https://github.com/armoredcreator/audio/pull/15).
- Commit de código/testes validado: `adf722290923c2f5cab61abe7591478b246c59e1` (inclui os testes adicionais descritos abaixo).
- GitHub Actions: **337 passed, 1 skipped** e `compileall` passou em Python 3.11.9; [execução de validação](https://github.com/armoredcreator/audio/actions/runs/39654219511).

O pre-check do Sync não descarta mais silenciosamente uma URL encontrada em outra fonte. O candidato segue para `Database.reserve_item()`, que grava a identidade (fonte + mensagem), estado `DUPLICATE`, proprietário `duplicate_of_content_id` e evento em `state_events`. O teste de regressão cobre a reserva durável. A mudança não toca banco operacional, checkpoints locais, Telegram, Shopee ou publicação. Recovery também passou a remover links simbólicos derivados sem atravessar seus destinos, preservando o caminho original e o alvo resolvido.

**Limite:** o PR continua em rascunho e não foi mesclado. A validação é a suíte de CI do laboratório, não um teste contra Telegram real nem uma auditoria de base histórica de produção.

### 16.2 Matriz atual de evidências

| Área | Estado | Evidência / lacuna |
|---|---|---|
| Sync — URL repetida entre fontes | **VALIDADO EM CI** | Teste regressivo de candidato cross-source + reserva durável SQLite; suíte atual: 337 passaram, 1 ignorado. |
| SQLite — identidade e evento de duplicata | **VALIDADO EM CI** | `reserve_item()` grava a linha `DUPLICATE`, proprietário e `state_events`; teste de regressão. |
| Vision — identidade Shopee canônica | **PARCIAL** | Resolver e commit transacional têm testes; ainda falta comprovação E2E de todos os links curtos/redirecionamentos e dos três sources com serviços reais. |
| Stock — original e caminho canônico | **PARCIAL** | Guardas de raiz e hash; novo teste injeta falha SQLite após movimentação e comprova que a repetição reconcilia o caminho sem alterar bytes. Falta validação com originais históricos reais. |
| IA — política de legenda | **VALIDADO POR TESTES DE POLÍTICA** | Política central e testes; inferência real Gemini/Ollama não é provada pela suíte isolada. |
| Speech → Studio v5 | **PARCIAL** | Contrato e runner têm testes isolados; relatório de Studio incompleto agora é rejeitado pelo Coordinator sem liberar Hub. CI Linux não prova o runtime Windows RVC Melody nem qualidade auditiva real. |
| Recovery — preservação do ORIGINAL | **PARCIAL** | CI cobre preservação do caminho original, symlink do próprio ORIGINAL e remoção de symlink para diretório externo sem tocar no alvo. Stock também cobre falha no reparo do caminho SQLite após movimentação. Permanecem pendentes outros modos de falha e validação no Windows real. |
| Dependências órfãs / ambiente único | **PENDENTE** | `scripts/audit_shared_dependencies.ps1` ainda precisa ser executado no `.venv` Windows real; o conflito documental de NumPy entre `requirements-analysis.txt` e `requirements.txt` requer resolução explícita por ambiente. |
| E2E real Sync → Hub | **NÃO VALIDADO** | Não foi executado nesta etapa; não inferir aprovação a partir de fixtures ou mocks. |

### 16.3 Gates que permanecem obrigatórios

1. Não mesclar o PR #15 antes de revisar o diff e seus checks no GitHub.
2. Resolver e documentar os requisitos NumPy por ambiente antes de qualquer instalação; não combinar cegamente os dois arquivos de requirements.
3. Executar a auditoria de imports/dependências no Windows e registrar o CSV real, sem instalar/remover pacotes durante a auditoria.
4. Os testes de falha para migração de caminho do Stock e symlinks na Recovery já foram adicionados e passam no CI. Continuar a revisão dos demais modos de falha de Recovery em ambiente Windows.
5. Confirmar a localização/migração do modelo Melody e dos assets reais no caminho canônico de Speech; não presumir que o modelo copiado para o caminho legado foi migrado.
6. Fazer E2E de mídia real somente no laboratório isolado e com os fixtures já existentes; nenhuma coleta ou publicação operacional está autorizada por esta auditoria.

### 16.4 Novos gates de falha Speech → Studio

A suíte agora cobre duas condições adicionais: (a) o Coordinator rejeita um relatório que declare `SUCCESS` mas omita itens do lote, persiste `RECOVERY` e não chama Hub; (b) o runner Studio detecta quando o hash do ORIGINAL muda durante o render, registra `FAILED`, `originals_intact=false` e `changed_originals`, e lança erro. O segundo teste é uma injeção deliberada de falha: comprova a detecção e o bloqueio do sucesso, não afirma que um arquivo já modificado possa ser restaurado automaticamente. O runner de produção real já compara hashes antes/depois e não deve escrever no ORIGINAL.

A execução CI do commit `adf722290923c2f5cab61abe7591478b246c59e1` terminou com 337 testes aprovados, 1 ignorado e `compileall` aprovado. Isso é evidência automatizada Linux/Python 3.11.9, não fechamento dos gates locais de Windows, assets RVC ou E2E real.

#### 16.5 Segurança do script de migração dos assets RVC

A auditoria do script encontrou um risco de escopo: a versão anterior enumerava todos os arquivos dentro de `ArmoredStudio/runtime/rvc/models`, apesar de a migração autorizar somente pesos e índices. O script agora considera exclusivamente extensões `.pth` e `.index`; caches, notas e outros artefatos não entram no plano nem são copiados.

Foi adicionado um teste PowerShell executado em runner Windows com fixture descartável sintética. O teste cobre preview sem cópia, apply, igualdade SHA-256 dos pesos/índice/efeito, preservação das fontes e exclusão de arquivos não autorizados. A execução [RVC asset migration safety (Windows)](https://github.com/armoredcreator/audio/actions/runs/39654593363) registrou `RVC_ASSET_MIGRATION_SCRIPT_TEST=PASS`. Isso valida o comportamento do script com arquivos sintéticos, não confirma que os assets reais do usuário já foram migrados ou que o runtime Melody funciona.
