# ARMORED AUDIOLAB --- DOCUMENTO MESTRE DE INTEGRAÇÃO, UNIFICAÇÕES E CORREÇÕES

**Atualizado em:** 21/10/2026\
**Repositório:** `https://github.com/armoredcreator/audio`\
**Raiz local de referência:**
`C:\Users\Administrador\Downloads\ArmoredAudioLab`\
**Branch validada nesta etapa:**
`feat/full-pipeline-migration-20261020`\
**HEAD observado:** `572402f0636f41b2f6538e663c4b457dee1d9ab9`\
**Status:** validação isolada da pipeline concluída; promoção para
produção ainda não autorizada.

> Este documento consolida decisões de arquitetura, responsabilidades
> dos módulos, unificações, correções, evidências dos testes e limites
> de liberação. É a referência operacional única para continuar o
> trabalho sem perder decisões entre conversas. Ele não substitui os
> contratos técnicos específicos, mas deve apontar para eles e registrar
> as decisões globais. Os resultados descritos são os que foram
> observados/relatados no ambiente local; este documento não afirma que
> o assistente executou comandos na máquina do usuário.

------------------------------------------------------------------------

## 1. Objetivo e estado atual

O objetivo do ArmoredAudioLab é validar a separação real de
responsabilidades e a integração entre a pipeline de ingestão/produto,
preparação de áudio e edição audiovisual, antes de qualquer promoção ao
ArmoredCreator de produção.

A execução isolada mais recente concluiu com `SUCCESS`, processando três
fixtures históricas locais, através de Sync → Vision → Stock → IA →
Speech → Studio. Os relatórios indicam armazenamento isolado, publicação
desativada, Hub não carregado e originais preservados. Isso demonstra a
integração da pipeline no cenário de fixtures; **não demonstra
sincronização real com Telegram ao vivo, publicação pelo Hub, nem
aprovação subjetiva final da voz convertida**.

### Evidências recentes

-   Suíte completa: **301 passed in 26.97s**, `PYTEST_EXIT_CODE=0`.
-   Execução isolada:
    `storage\validation\full_pipeline_20261021T011800_5636a95092`
-   Manifesto de fixtures:
    `storage\validation\historical_e2e_input_20261021T011759Z_98f661af`
-   Relatórios presentes:
    -   `pipeline-report.json`
    -   `studio-report.json`
    -   `speech-studio-manifest.json`
-   Relatório Studio: `status=SUCCESS`, `mode=REAL_STUDIO`,
    `audio_contract_version=5`, 3 itens processados.
-   Configuração relatada:
    `source_mode=EXPLICIT_LOCAL_FIXTURE_MANIFEST`,
    `storage_isolated=true`, `publication_disabled=true`,
    `hub_loaded=false`.
-   Hashes dos originais das fixtures foram preservados conforme os
    relatórios.
-   Auditoria Git final: branch e HEAD conferidos; `git diff --check`
    não reportou erro de whitespace. Houve avisos LF/CRLF, que são
    avisos de normalização de finais de linha.
-   Não foi feito commit, push, merge ou ativação de produção nesta
    etapa.

## 2. Arquitetura e responsabilidades não negociáveis

### 2.1 Ordem canônica do pipeline

A ordem obrigatória é:

1.  **ArmoredSync** --- coleta/registro de mensagens e metadados das
    fontes.
2.  **ArmoredVision** --- análise visual e decisão de
    elegibilidade/classificação.
3.  **ArmoredStock** --- obtenção/validação do original de mídia quando
    disponível.
4.  **ArmoredIA** --- contexto e geração de texto/caption conforme
    política central.
5.  **ArmoredSpeech** --- todas as responsabilidades de áudio:
    classificação, preparação de trilhas, RVC, voz/efeitos e pacote de
    áudio contratual.
6.  **ArmoredStudio** --- edição visual, enquadramento/crop,
    banner/layout e renderização final audiovisual; consome o contrato
    de áudio do Speech.
7.  **ArmoredHub** --- distribuição/publicação, somente após aprovação
    explícita e verificações próprias.

Não inverter Speech e Studio. Não devolver execução de áudio para o
Studio. O Studio deve receber áudio já preparado por Speech e se
concentrar no trabalho visual e na composição final.

### 2.2 Princípios estruturais

-   **SQLite é a fonte de verdade** para estado durável e histórico.
-   **Coordinator é a única raiz de composição**: conecta os serviços;
    módulos de domínio não devem montar ou controlar toda a aplicação.
-   Transição operacional `CATCH-UP → LIVE`.
-   Não introduzir Redis, RabbitMQ, Celery, Kafka ou infraestrutura de
    filas externa.
-   Não introduzir `publish_queue`.
-   Não usar lotes de pré-download como comportamento baseline.
-   Preservar originais válidos, histórico SQLite, eventos, estados e
    checkpoints.
-   Não executar novamente Sync/Vision/Stock em massa sem auditoria e
    justificativa explícita.
-   Não zerar banco, checkpoints ou estados para facilitar testes de
    caption/áudio.
-   Isolar dados e artefatos de cada execução de validação; não permitir
    que fixtures gravem no armazenamento operacional.
-   Não carregar o Hub nem publicar em execução de teste integrada.
-   Não tratar duplicatas prováveis como autorização para apagar
    registros ou mídia.

## 3. Separação real entre Speech e Studio

### 3.1 ArmoredSpeech --- dono exclusivo do áudio

Speech é responsável por: - classificar áudio de entrada; - identificar
fala/narração PT-BR versus música/sem fala/idioma estrangeiro; - decidir
se RVC é permitido; - executar conversão RVC quando elegível; - produzir
a trilha de voz; - preparar a trilha de efeitos sonoros; - manter
hashes, metadados e evidência das trilhas; - criar e validar o pacote de
áudio versionado; - comunicar ao Studio a trilha e metadados via
contrato explícito; - bloquear itens incompletos/inválidos sem fingir
sucesso; - manter o áudio isolado do restante do fluxo de edição visual.

**Regra de roteamento:** - `PT_BR_NARRATION` elegível → `RVC_MELODY`. -
`MUSIC_ONLY`, sem áudio útil, idioma estrangeiro ou fala que não passe a
classificação/confiabilidade → nunca RVC; usar a política `EFFECT_ONLY`
quando definida pelo contrato, remover/substituir o áudio original
conforme a política do pipeline e aplicar o asset de efeitos aprovado. -
A elegibilidade deve ser conservadora: uma classificação incerta não
deve forçar RVC. - A voz configurada é `melody`
(`ARMORED_STUDIO_RVC_VOICE` foi o nome histórico observado; ao
consolidar configuração, evitar manter variáveis com "STUDIO" se o dono
agora é Speech sem uma política de compatibilidade documentada).

### 3.2 ArmoredStudio --- dono exclusivo do audiovisual visual/final

Studio é responsável por: - enquadramento e crop; - composição visual; -
banner/layout e variações de timeline; - composição final de vídeo; -
renderização via FFmpeg; - consumir o pacote Speech e validar seus
caminhos/metadados; - preservar a integridade do original; - registrar
relatório final e evidências da composição.

Studio **não deve**: - classificar política de áudio de domínio; -
invocar RVC diretamente; - possuir cópias duplicadas dos modelos RVC; -
manter uma segunda implementação de preparação/conversão de áudio; -
manter cópias duplicadas de `efeitosonoro.wav` como fonte de verdade; -
competir com Speech pela política de volumes, roteamento ou
classificação.

A renderização final ainda precisa combinar visual e áudio; isso não
significa transferir a propriedade da lógica de áudio para Studio. O
grafo de filtros final pode ser montado na fronteira de renderização, a
partir do contrato produzido pelo Speech.

### 3.3 Contrato Speech → Studio

O contrato de áudio versionado é a fronteira explícita entre módulos. Na
execução atual, o relatório registra `audio_contract_version=5`.

O contrato deve: - identificar item/lote e versão; - apontar para
caminhos explícitos de áudio; - registrar classificação e rota; -
registrar hashes SHA-256 das trilhas e do original; - carregar metadados
necessários à timeline; - validar que caminhos existem, são arquivos
esperados e estão dentro do root autorizado; - rejeitar caminhos
externos inesperados e pacotes incompletos; - deixar claro quais trilhas
devem ser combinadas e qual saída de áudio será usada; - impedir que
Studio adivinhe ou refaça o trabalho de Speech.

Referências relacionadas já existentes: -
`ArmoredSpeech/docs/ARMORED_SPEECH_CONTRACT.md` -
`ArmoredSpeech/docs/STUDIO_BRIDGE_V1.md` -
`ArmoredSpeech/docs/REAL_AUDIO_REVIEW_20261015.md` -
`docs/ARMOREDSTUDIO_PORT_STATUS.md`

Antes de mudar versão do contrato, atualizar documentação, testes,
produtor e consumidor de forma coordenada. Não manter duas versões
incompatíveis como se fossem equivalentes.

## 4. Unificações desejadas e critério de conclusão

A meta é simplificar o sistema sem apagar responsabilidades necessárias.
"Unificar" significa ter uma implementação compartilhada e uma única
autoridade para cada política, não concentrar todo o código num arquivo
monolítico.

### 4.1 RVC e assets de áudio

-   RVC, modelos e configuração de voz devem ter um único dono:
    **ArmoredSpeech**.
-   Modelo/voz padrão validado: `melody`.
-   Ambiente RVC copiado/testado no laboratório com Python 3.10.11; o
    projeto principal usa Python 3.11.9. O caminho do subprocesso RVC
    pode usar o interpretador dedicado de RVC, se necessário. Não
    presumir que dependências nativas do RVC são compatíveis com o
    Python principal sem teste.
-   Assets esperados incluem `efeitosonoro.wav`; `banner.png` é visual e
    pertence ao Studio.
-   Não substituir assets aprovados arbitrariamente.
-   Eliminar cópias legadas só após provar que não são referenciadas,
    validar o novo dono e registrar auditoria de ownership.
-   Um registro no log do processo pai e outro no subprocesso não
    significa automaticamente duas conversões. Para provar contagem,
    contar invocações/correlation IDs ou examinar o ponto único de
    chamada.

### 4.2 FFmpeg

Objetivo: um caminho canônico de execução FFmpeg e uma composição final
consistente, em vez de pipelines concorrentes que recortam, normalizam
ou muxam o mesmo conteúdo de formas diferentes.

-   Centralizar criação/validação de comandos e argumentos comuns onde
    isso realmente reduz duplicação.
-   Evitar um "FFmpeg universal" monolítico que misture políticas de
    Speech e Studio.
-   Speech prepara as trilhas; Studio monta a timeline visual e a
    composição final usando as trilhas do contrato.
-   Não fazer crop repetido em vários módulos. Definir uma única
    etapa/dono de crop visual no Studio e reutilizar o resultado.
-   Preservar a compatibilidade de codecs, resolução, fps, duração,
    SAR/DAR e sincronização.
-   Registrar comando/grafo relevante e falhas sem expor credenciais.
-   Só remover caminhos antigos depois de buscas de referências, testes
    e execução real de regressão.

### 4.3 Crop/enquadramento

-   Um dono canônico para crop/enquadramento: Studio.
-   Não duplicar crop no Speech, nem manter variantes que fazem a mesma
    transformação sem motivo explícito.
-   A decisão de crop deve ser testável e determinística; preservar
    barras pretas/variantes de fonte quando fizerem parte do teste de
    regressão.
-   Comparar saída antes/depois com fixtures VEO1, VEO2, Gemini e
    black-bars já guardadas em `ArmoredAudioLab\storage`.
-   Não degradar resolução ou distorcer proporção para forçar um
    enquadramento.

### 4.4 Integridade/hash e ownership

-   Compartilhar helpers puros de hash/integridade se houver duplicação
    real, mantendo a responsabilidade de validação no módulo que
    consome/produz o artefato.
-   Não criar abstração compartilhada sem testes que provem que ela
    substitui as implementações duplicadas.
-   Ownership de áudio: Speech. Ownership de renderização visual:
    Studio. Ownership de persistência e estado de pipeline: core/SQLite.
-   Auditorias de ownership devem ser somente leitura e não excluir
    arquivos.

### 4.5 Armazenamento isolado por execução

-   Cada execução E2E deve usar diretório de artefatos próprio, banco
    descartável e manifesto explícito.
-   A raiz de configuração/assets não deve ser confundida com a raiz de
    saída do run.
-   Nenhuma execução de fixtures pode escrever no banco operacional nem
    em diretórios de originais de produção.
-   Guardas de path devem impedir escape de diretório e caminhos
    externos não autorizados.
-   O relatório precisa registrar modo de fonte, isolamento, publicação
    desativada e se o Hub foi carregado.

### 4.6 Preparação de lote

-   Unificar preparação/manifesto de itens onde os fluxos atualmente
    repetem lógica.
-   Um lote só entrega ao Studio itens com pacote Speech válido.
-   Itens bloqueados ou falhos devem ter resultado explícito; não podem
    ser silenciosamente promovidos.
-   A idempotência deve preservar itens já prontos e impedir duplicação.
-   Não transformar a pipeline baseline em pré-download em massa nem
    disparar novamente toda a coleta histórica.

## 5. Correções técnicas consolidadas

### 5.1 Isolamento de storage e banco

Foi introduzido/validado trabalho para permitir roots de artefatos por
execução e propagar o root isolado pelo fluxo Speech → Studio. A
reconciliação histórica deve operar sobre uma cópia descartável do
snapshot, nunca sobre o SQLite operacional.

Critério de aceitação: - `storage_isolated=true`; - relatório e
artefatos dentro do diretório do run; - nenhuma mutação no banco de
produção; - integrity check OK; - originais de fixtures intactos.

### 5.2 Identidade de produto e escolha do registro proprietário

Na reconciliação histórica, escolher deterministicamente o proprietário
de uma identidade de produto, considerando evidências duráveis e
progresso do pipeline, sem reescrever indiscriminadamente registros
históricos. Evidência de publicação/confirmada e progresso durável devem
prevalecer sobre uma linha incompleta de pré-download; em empate, usar
critérios estáveis.

-   A reconciliação foi feita em clone descartável do snapshot.
-   O relatório de auditoria observado registrou 5.478 duplicatas
    classificadas e 2 casos incertos preservados.
-   A contagem de grupos de identidade e URLs é evidência para análise,
    não permissão para apagar itens.
-   Nunca deletar originais, eventos ou histórico só porque dois itens
    compartilham URL/identidade.
-   Manter casos ambíguos para revisão manual.

### 5.3 Guardas de caminhos do ArmoredStock

Há teste novo `tests/test_armored_stock_external_path_guard.py`. O
objetivo é impedir que o serviço aceite ou grave artefatos fora do root
permitido. Manter os testes e não enfraquecer a validação para "fazer
passar" um caminho externo.

### 5.4 Compatibilidade de RVC e safe globals

Há teste novo `tests/test_rvc_safe_globals_compat.py`; preservar a
compatibilidade necessária para carregamento de modelos e versões de
dependências. Não desabilitar validações de segurança de carregamento de
modelo sem análise explícita e teste.

### 5.5 Speech batch runner e Studio

Há alterações locais em: - `ArmoredSpeech/rvc_engine.py` -
`ArmoredSpeech/studio_batch_runner.py` -
`ArmoredSpeech/tests/test_studio_batch_runner.py` -
`ArmoredStudio/service.py` (confirmar status real no momento; pode ter
mudado entre checkpoints) - `armored_core/database.py` -
`armored_core/pipeline.py` - `armored_core/storage.py` -
`scripts/run_lab_pipeline.py` - `tests/test_catch_up_stages.py` -
`tests/test_run_lab_pipeline.py`

A última auditoria `git status` do checkpoint atual listou: -
modificados: `ArmoredSpeech/rvc_engine.py`,
`ArmoredSpeech/studio_batch_runner.py`,
`ArmoredSpeech/tests/test_studio_batch_runner.py`,
`ArmoredStock/service.py`, `armored_core/database.py`,
`armored_core/pipeline.py`, `armored_core/storage.py`,
`tests/test_run_lab_pipeline.py`; - não rastreados:
`AUDITORIA_LOTE_RESUMO.txt`,
`tests/test_armored_stock_external_path_guard.py`,
`tests/test_product_identity_seed_owner.py`,
`tests/test_rvc_safe_globals_compat.py`.

O `git diff --stat` no checkpoint mostrou 11 arquivos rastreados com 262
inserções e 35 remoções. A listagem de `git status` e `git diff --stat`
teve uma diferença aparente para alguns caminhos (ex.:
`ArmoredStudio/service.py`, `scripts/run_lab_pipeline.py`,
`tests/test_catch_up_stages.py` apareceram nos avisos de EOL/diff stat
mas não no `status --short` colado). Reexecutar status/diff se
necessário; não inferir estado atual além da saída mais recente.

### 5.6 Caption / ArmoredIA

Política alvo: - frase principal com exatamente 2--3 palavras, excluindo
o coração fixo e hashtags; - exatamente um coração laranja `🧡`
adicionado centralmente; - não aceitar emojis fornecidos pelo modelo
(incluindo ™️/©️/®️, bandeiras, ZWJ e variações de apresentação); - 1--2
hashtags válidas, sem duplicação por caixa; - respostas inválidas devem
ter fallback determinístico; - Gemini e Ollama devem obedecer à mesma
política final; não rejeitar lote de Ollama de maneira diferente sem
regra explícita.

Não afirmar que esta política foi revalidada pelo E2E de 21/10 sem
relatório específico. Ela é um requisito de arquitetura/regressão a
preservar.

### 5.7 ArmoredVision e elegibilidade Shopee

-   Resolver/classificar URL antes de instanciar a API de afiliados,
    para que links não elegíveis possam ficar em espera sem exigir
    credenciais.
-   A aprovação afiliada deve depender de correspondência exata do
    produto resolvido, não de adivinhação por URL.
-   Não promover automaticamente registros `WAITING_VISION` sem
    evidência.
-   A auditoria de 6.227 `WAITING_VISION` foi descrita como leitura
    apenas; a maioria não deve ser aprovada por inferência.
-   Não usar a auditoria de API para alterar estados ou reprocessar
    registros sem aprovação explícita.

### 5.8 Mídias históricas indisponíveis

Dois itens históricos ficaram adiados/indisponíveis: -
`-1002039708059_8845` - `-1002698134896_104718`

Não fazer retry cego nem alterar estados para simular sucesso. Preservar
a condição de adiamento até haver evidência ou política explícita de
recuperação.

## 6. Execução E2E isolada de 21/10/2026

### 6.1 Lote e modo

-   Run: `full_pipeline_20261021T011800_5636a95092`
-   Batch ID: `lab-20261021T011800_5636a95092`
-   Contrato Speech/Studio: v5
-   Fonte: manifesto explícito de fixtures locais históricas; não
    Telegram ao vivo.
-   Três fontes sintéticas/isoladas correspondentes aos três cenários de
    fixture.
-   `publication_disabled=true`; `hub_loaded=false`.
-   Relatório geral e relatório Studio em `SUCCESS`.

### 6.2 Itens processados

  ----------------------------------------------------------------------------------------
  Fixture             Item Classe de áudio     Rota            Duração final Tamanho final
  ---------- ------------- ------------------- --------------- ------------- -------------
  A                    460 `PT_BR_NARRATION`   `RVC_MELODY`          10,00 s    18.713.294
                                                                                     bytes

  B                 178492 `MUSIC_ONLY`        `EFFECT_ONLY`       15,5667 s    15.603.220
                                                                                     bytes

  C                  12040 `MUSIC_ONLY`        `EFFECT_ONLY`         14,80 s    11.028.876
                                                                                     bytes
  ----------------------------------------------------------------------------------------

Saídas: - A:
`workspace\storage\Videos GRUPO_FONTE_Lab5636a95092A\460\460_AUuwN0WCTk.mp4` -
B:
`workspace\storage\Videos GRUPO_FONTE_Lab5636a95092B\178492\178492_gR0ewGR0h.mp4` -
C:
`workspace\storage\Videos GRUPO_FONTE_Lab5636a95092C\12040\12040_AAI5yOko1i.mp4`

Formatos reportados: H.264, 1080×1920, AAC estéreo 44.1 kHz. Hashes
SHA-256 dos originais: - A:
`7d4abadb1cd6c13b14966bd8bd99cd923368cd8848cf1e304a1528a9b01648af` - B:
`5f561d79ef1c71cba933cc0e60ebcba782483d4024f77418a12deb35154cca61` - C:
`e4c7ee1ce59425efc25a285d53aee116be15f78a1f2319d92bfead1285536dcd`

### 6.3 Evidência de áudio

`studio-report.json` registrou para A: - `audio_class=PT_BR_NARRATION` -
`route=RVC_MELODY` - `timeline_variant=NO_BANNER` - voice track e effect
track com caminhos e hashes - `audio_output_label=[final_a]` - hash do
original preservado.

Para B e C: - `audio_class=MUSIC_ONLY` - `route=EFFECT_ONLY` - não
elegíveis para RVC; - trilhas de efeito registradas.

Medições reportadas pelo FFmpeg: \| Item \| Duração \| Volume médio \|
Pico máximo \| \|---\|---:\|---:\|---:\| \| 460 \| 10,00 s \| −18,7 dB
\| −3,4 dB \| \| 178492 \| 15,57 s \| −19,9 dB \| −3,2 dB \| \| 12040 \|
14,80 s \| −19,9 dB \| −3,3 dB \|

Esses números, isoladamente, não provam naturalidade da voz,
inteligibilidade, equilíbrio subjetivo ou audibilidade dos efeitos. O
log de FFmpeg pode mostrar PCM como formato de saída de análise; não
concluir que o MP4 tem dois áudios sem confirmar o mapeamento de streams
com `ffprobe`.

### 6.4 Revisão visual

Quadros extraídos para:
`storage\validation\full_pipeline_20261021T011800_5636a95092\quality_review`

-   `460_PTBR_RVC.jpg`
-   `178492_MUSIC.jpg`
-   `12040_MUSIC.jpg`

A inspeção de quadros verifica somente os instantes capturados. A
aprovação visual final deve considerar o vídeo inteiro: crop,
enquadramento, barras pretas, cortes, banner quando aplicável,
continuidade, sincronização e ausência de deformação.

A avaliação auditiva humana permanece necessária para: - confirmar que a
fala PT-BR foi efetivamente convertida e soa natural como Melody; -
confirmar que não houve voz indevida em itens `MUSIC_ONLY`; - confirmar
que efeitos estão presentes, audíveis e não encobrem a voz; - verificar
início/fim, cortes, ruídos, clipping audível e sincronização.

## 7. Histórico e proteção de produção

O banco operacional de produção fica em:
`C:\Users\Administrador\Downloads\ArmoredCreator-final\storage\database\armoredcreator.db`

Princípios: - nunca abrir esse banco para escrita durante testes do
laboratório; - nunca copiar fixtures para os diretórios operacionais; -
não resetar estados/checkpoints; - não apagar registros por coincidência
de URL; - reconciliação histórica só em clone descartável; - preservar
hash dos originais e auditar antes de qualquer migração; - não rodar
`START_ALL` como atalho de validação; - não ativar Hub/publicação
automaticamente.

Snapshot de auditoria histórico reportado: 19.966 itens; 0 grupos
duplicados de `(source_id, telegram_message_id)`; 862 grupos de URL
duplicada envolvendo 2.084 itens; 2.938 grupos canônicos de identidade
de produto envolvendo 8.418 itens. Esses números são de um snapshot
específico e não devem ser tratados como contagens atuais sem nova
auditoria read-only.

### Fontes Telegram esperadas em produção

1.  `-1003788989075` --- `GRUPO GRÁTIS DESAFIO 30 DIAS - SHOPEE VÍDEOS`
2.  `-1002698134896` --- `PACK DE VÍDEOS GRATUITO`
3.  `-1002039708059` --- `Videos Achadinhos Afiliados Shopee`, username
    `achadosshopebrasil`, `broadcast=True`, `forum=False`; esta fonte
    não exige `topic_id`.

Destino Hub: `-1004341972306`; tópicos 228, 1160 e 1327. Esses valores
são dados de configuração sensíveis ao ambiente: confirmar no arquivo de
configuração local, não duplicar tokens/segredos no documento nem
imprimir credenciais.

## 8. Configuração e ambientes

-   Projeto principal: Python 3.11.9 em `.venv\Scripts\python.exe`.
-   RVC testado com ambiente dedicado Python 3.10.11 em
    `ArmoredStudio\runtime\rvc\env\Scripts\python.exe` no histórico de
    migração; após centralizar a propriedade, a localização canônica
    deve ficar sob Speech e a documentação precisa refletir o caminho
    realmente vigente.
-   Configuração preferida no ArmoredCreator de produção:
    `credentials/project.env` como fonte única, sem alterar layout
    congelado.
-   Local Telegram Bot API foi usado historicamente na porta 8081.
-   Nunca incluir credenciais reais em relatórios, testes, documentos ou
    logs.
-   Antes de ajustar variáveis, verificar nomes realmente lidos pelo
    código e documentar compatibilidade/depreciação.

## 9. Testes e critérios de aceitação

### Já evidenciado neste checkpoint

-   [x] 301 testes passam.
-   [x] Pipeline isolada com três fixtures termina em `SUCCESS`.
-   [x] Contrato Speech → Studio v5 aparece no relatório.
-   [x] Rotas `RVC_MELODY` e `EFFECT_ONLY` registradas para cenários
    diferentes.
-   [x] Originais das fixtures preservados por hash.
-   [x] Storage isolado, publicação desativada e Hub não carregado
    conforme relatório.
-   [x] Relatórios pipeline, Studio e manifesto presentes.
-   [x] `git diff --check` sem erro reportado; avisos LF/CRLF presentes.
-   [x] Nenhum commit/push/merge feito neste passo.

### Ainda não demonstrado / bloqueios para produção

-   [ ] Naturalidade subjetiva e qualidade final da voz Melody aprovadas
    por escuta.
-   [ ] Audibilidade e equilíbrio dos efeitos aprovados por escuta.
-   [ ] Revisão do vídeo completo dos três casos, não só quadros
    extraídos.
-   [ ] Teste de sincronização/áudio em players reais ou inspeção final
    de streams.
-   [ ] Sync real com Telegram ao vivo validado nesta versão integrada.
-   [ ] Hub e publicação testados em ambiente controlado com aprovação
    explícita.
-   [ ] Revisão final do diff funcional completo e de todos os arquivos
    não rastreados.
-   [ ] Plano de promoção/rollback e janela de migração aprovados antes
    de produção.

Não marcar itens pendentes como concluídos apenas porque o relatório
contém `SUCCESS`.

## 10. Auditoria Git e alterações locais

Checkpoint final informado: - Branch:
`feat/full-pipeline-migration-20261020` - HEAD:
`572402f0636f41b2f6538e663c4b457dee1d9ab9`

Último `git status --short` informado:

``` text
 M ArmoredSpeech/rvc_engine.py
 M ArmoredSpeech/studio_batch_runner.py
 M ArmoredSpeech/tests/test_studio_batch_runner.py
 M ArmoredStock/service.py
 M armored_core/database.py
 M armored_core/pipeline.py
 M armored_core/storage.py
 M tests/test_run_lab_pipeline.py
?? AUDITORIA_LOTE_RESUMO.txt
?? tests/test_armored_stock_external_path_guard.py
?? tests/test_product_identity_seed_owner.py
?? tests/test_rvc_safe_globals_compat.py
```

`git diff --stat` informado: 11 arquivos rastreados, 262 inserções, 35
remoções. Os avisos de CRLF citavam também arquivos que não apareceram
no `status --short` colado; portanto, a lista acima é o status
explicitamente apresentado, e uma nova captura é necessária antes de
staging/commit.

**Não usar `git checkout --`, `git reset --hard`, `git clean`, nem
apagar arquivos não rastreados.** Essas alterações são trabalho local
deliberado até que o usuário autorize outra ação. Não commitar ou enviar
para remoto sem pedido explícito.

## 11. Próximos passos seguros

1.  Fazer revisão auditiva/visual completa e registrar
    aprovado/reprovado por item.
2.  Reexecutar `git status --short --branch`, `git diff --check` e
    revisar o diff funcional, sem editar nada durante a auditoria.
3.  Revisar os testes de guardas de caminho, owner de identidade,
    compatibilidade RVC e contrato Speech/Studio.
4.  Atualizar documentação técnica específica se uma decisão ou
    assinatura de contrato mudou; manter este documento mestre
    sincronizado.
5.  Preparar plano de promoção isolado e reversível, incluindo backup
    verificável, diff de configuração e critério de rollback.
6.  Só com autorização explícita, planejar teste controlado com
    integração externa/Telegram. Hub/publicação permanecem desativados
    até aprovação específica.
7.  Não fazer reprocessamento histórico indiscriminado; qualquer
    catch-up deve ser planejado, auditado e idempotente.

## 12. Regra para futuras alterações

Toda mudança futura deve registrar: - problema observado; - causa raiz
demonstrada; - módulo proprietário antes/depois; - arquivos alterados; -
unificação feita e duplicação removida (com busca de referências); -
testes focais e regressão completa; - resultado da execução real; -
efeitos sobre contrato, paths, hashes e armazenamento; - riscos
residuais e rollback; - confirmação de que originais, banco operacional
e publicação não foram tocados.

Se uma alteração não puder ser comprovada, rotulá-la como **proposta**,
**parcial** ou **não validada**, nunca como concluída.

------------------------------------------------------------------------

**Regra final:** preservar os originais e a história; uma única fonte de
verdade para cada responsabilidade; Speech é dono do áudio; Studio é
dono do visual/render final; SQLite é fonte de verdade do pipeline;
Coordinator compõe os módulos; validar isoladamente antes de promover;
nenhuma publicação sem autorização explícita.
