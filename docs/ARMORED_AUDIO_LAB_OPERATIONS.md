# ArmoredAudioLab — manual canônico de arquitetura, operação e evidências

**Versão:** 2.0  
**Revisão:** 19/10/2026 (horário local de São Paulo)  
**Repositório:** armoredcreator/audio  
**Branch estável de laboratório:** test/armoredstudio-speech-integration-20261015  
**HEAD de referência:** 23b398d8150a79bb59299733c5340db307b4119c  
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
