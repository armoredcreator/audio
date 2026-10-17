# ArmoredSpeech → ArmoredStudio — documentação canônica do fluxo

**Repositório:** `armoredcreator/audio`  
**Branch de referência desta documentação:** `feature/speech-studio-batch-runner-20261016`  
**Escopo:** laboratório isolado; não é autorização para integrar, publicar ou alterar o ArmoredCreator operacional.  
**Estado:** classificação/preparação de Speech, RVC Melody e teste real Speech → Studio demonstrados em amostras; transferência definitiva da propriedade de RVC/mixagem para Speech ainda pendente.

## 1. Ordem do pipeline maior

A ordem acordada para o sistema completo é:

**ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub.**

- Sync, Vision e Stock concluem suas fases históricas antes das fases seguintes.
- IA vem antes de Speech.
- Speech deve concluir o lote aprovado antes do início do Studio; não intercalar Speech → Studio item por item.
- Hub/publicação não faz parte dos testes isolados descritos aqui.
- Esta branch de laboratório não substitui a integração validada contra o banco real nem autoriza publicação.

## 2. Responsabilidades pretendidas

### ArmoredSpeech — dono do processamento de áudio

Responsabilidades atuais e/ou destino da migração:
1. Extrair e preparar o áudio de entrada sem modificar o MP4 original.
2. Detectar presença de fala, idioma, música, ruído e ausência de áudio.
3. Classificar cada item e persistir a decisão no registro SQLite experimental do próprio Speech.
4. Gerar o WAV sidecar de referência e, quando exigido pela classe, separar a voz.
5. Aplicar RVC Melody somente quando a decisão e as evidências autorizarem.
6. Preparar a rota final de áudio: voz convertida, faixa silenciosa ou áudio alternativo conforme a regra aprovada.
7. Aplicar o efeito sonoro do projeto e os volumes/mixagem acordados.
8. Entregar ao Studio um artefato de áudio validado, com identidade, metadados, hashes e contrato versionado.

**Meta da migração:** RVC, escolha da rota, silêncio, efeito sonoro e mixagem passam a ser responsabilidade de Speech. Não basta mover o arquivo do conversor; é preciso mover a decisão e a preparação do áudio sem duplicar processamento.

### ArmoredStudio — dono da edição visual e do vídeo final

Permanece responsável por:
- metadados e validação do vídeo;
- detecção de barras pretas;
- detecção de marcas VEO 1/VEO 2 e Gemini;
- análise de banner, planejamento e validação de cortes/crop;
- composição visual, banner/intro e normalização para o formato final;
- sincronização da faixa recebida de Speech com cortes e duração do vídeo;
- renderização final via FFmpeg, validação do arquivo resultante e verificação de integridade do original.

O Studio não deve chamar RVC nem aplicar novamente o efeito/mixagem quando receber áudio finalizado por Speech. Ele continua responsável por sincronizar esse áudio com a edição visual e renderizar o vídeo.

## 3. Estado atual encontrado no código

### ArmoredSpeech
- `ArmoredSpeech/audio_intelligence.py`: classificação de áudio.
- `ArmoredSpeech/model_processor.py`: converte o resultado da análise em decisão tipada.
- `ArmoredSpeech/media.py`: prepara o WAV sidecar; pode usar separação vocal quando exigida.
- `ArmoredSpeech/studio_bridge.py`: exporta a decisão persistida para o contrato.
- `ArmoredSpeech/studio_integration.py`: faz o handoff e pré-valida o lote antes da primeira chamada ao Studio.
- `ArmoredSpeech/studio_batch_runner.py`: runner de laboratório para lote aprovado e completo.

### ArmoredStudio
- `ArmoredStudio/service.py`: fronteira pública `build(root).process(item, speech_payload=...)`.
- `ArmoredStudio/unified.py`: orquestra análise e processamento; ainda possui decisão/fluxo de áudio e chama a conversão RVC.
- `ArmoredStudio/analysis/audio_intelligence.py`: classificador de áudio paralelo que precisa ser aposentado do fluxo de produção depois de migrar e validar a decisão de Speech.
- `armored_core/audio/rvc.py`: implementação comum do motor RVC; conserva a resolução do `.venv` da raiz e do mesmo diretório de modelos/índices em `ArmoredStudio/runtime/rvc`.
- `ArmoredStudio/processing/rvc.py`: facade de compatibilidade para imports e CLI antigos. O fluxo v1 do Studio ainda invoca o conversor por essa facade; a transferência da invocação/decisão para Speech ainda não ocorreu.
- `ArmoredStudio/processing/finalizer.py`: finalização audiovisual. Hoje recebe voz e efeito sonoro como entradas distintas, controla volumes/normalização, aplica cortes temporais e renderiza intro/vídeo principal.
- `ArmoredStudio/speech_handoff.py`: valida o payload v1, identidade do item, caminhos dentro do workspace, legibilidade do WAV, SHA-256 do original e elegibilidade de RVC.
- `ArmoredStudio/analysis/blackbar.py`, `veo_detector.py`, `gemini_detector.py`, `export_planner.py` e `export_plan_validator.py`: fluxo visual que permanece no Studio.

**Conclusão arquitetural:** existe uma fronteira de integração funcional, mas a separação de responsabilidades ainda é parcial. O contrato v1 transporta principalmente classificação e WAV de referência; não garante por si só que Speech já tenha entregue voz convertida e mixagem pronta. O Studio ainda executa etapas de áudio, então a migração exige ampliar o contrato e adaptar o finalizador.

## 4. Contrato proposto para a nova fronteira

A próxima versão do contrato deve ser versionada e validada de modo fail-closed. Campos conceituais mínimos:

- versão do contrato;
- `source_id` e `item_id` estáveis;
- caminho do original e SHA-256 antes/depois;
- classe de áudio, idioma, confiança e evidências de fala/música;
- decisão explícita de RVC e motivo;
- caminho do artefato de áudio final preparado por Speech;
- hash do artefato, formato, canais, sample rate e duração;
- estado do processamento e versão/modelo RVC usados;
- descrição da rota: voz convertida, silêncio/efeito, ou rota conservadora aprovada;
- política de limpeza/retensão do WAV e artefatos até o Studio confirmar sucesso.

O contrato deve impedir:
- inferir elegibilidade de RVC apenas pela existência de um WAV;
- aceitar caminho fora do workspace;
- aceitar original alterado ou hashes divergentes;
- processar item cuja decisão esteja ausente, desconhecida ou não READY;
- aplicar RVC ou mixagem duas vezes;
- começar o lote do Studio se algum item do lote aprovado estiver bloqueado.

Não permitir que Studio leia diretamente a tabela SQLite interna do Speech. A integração continua por payload/artefato versionado, não por compartilhamento do schema interno.

## 5. Rotas de áudio e regras de segurança

| Classe/condição | Regra segura |
|---|---|
| Narração PT-BR sem música relevante | Elegível a Melody somente com decisão válida e evidência aprovada. |
| Fala PT-BR + música | RVC bloqueado no contrato atual até que a separação vocal seja validada explicitamente; não assumir que um stem é seguro apenas por existir. |
| Música sem fala | Nunca enviar ao RVC PT-BR; seguir a rota aprovada de áudio não narrado/efeito. |
| Ruído ou sem narração útil | Não usar RVC; usar a rota conservadora definida pelo projeto. |
| Sem áudio | Gerar silêncio determinístico quando necessário; aplicar o efeito somente conforme regra aprovada. |
| Fala estrangeira | Não usar o modelo Melody PT-BR; aplicar a política conservadora definida pelo projeto. |
| UNKNOWN, erro, arquivo ilegível ou decisão inconsistente | Bloquear a elegibilidade e não presumir sucesso. |

Os volumes, normalização, duração e comportamento de introdução precisam ser preservados em testes comparativos. A mixagem deve ocorrer uma única vez.

## 6. Plano de migração — ainda não executado

1. **Congelar a referência:** registrar branch/commit, hashes do modelo e índice Melody, assets necessários, configuração e evidência do teste E2E aprovado.
2. **Mapear dependências:** identificar todas as chamadas de RVC, extração, silêncio, efeitos, volume, normalização, filtros temporais e limpeza de temporários no Studio.
3. **Definir o contrato de áudio final:** Speech produz e valida o áudio que o Studio consumirá; registrar duração/formato/hash e a rota escolhida.
4. **Reaproveitar a implementação RVC comprovada:** não reescrever o conversor nem regenerar/substituir o modelo ou índice. Usar o ambiente compartilhado `.venv` / Python 3.11.9 já validado no laboratório.
5. **Mover a propriedade, não apenas o arquivo:** Speech decide e prepara a rota completa; Studio deixa de classificar/convertê-la/mixá-la novamente.
6. **Adaptar o finalizador:** Studio recebe o artefato final de Speech, alinha-o aos cortes temporais e compõe o vídeo; preservar tratamento visual e áudio da introdução conforme a regra aprovada.
7. **Manter compatibilidade temporária controlada:** caminho antigo pode existir durante a comparação, mas os modos precisam ser mutuamente exclusivos para nunca duplicar RVC ou mixagem.
8. **Executar testes isolados por rota** e depois o teste real Speech → Studio com o vídeo de referência.
9. **Comparar saída e integridade:** escuta humana, volume, efeito, sincronização, duração, streams/codificação, imagem, saída legível e SHA-256 do original. Registrar os resultados.
10. **Retirar o caminho antigo somente após aprovação:** nenhuma implementação funcional, modelo, índice, asset ou arquivo original deve ser removido antes de passar os critérios de aceitação.

### Critérios de aceitação da migração

- Speech entrega artefato válido e metadados coerentes para cada rota.
- RVC é executado no máximo uma vez e apenas para narração PT-BR elegível.
- Efeito sonoro e mixagem são aplicados no máximo uma vez e mantêm os volumes acordados.
- Studio não depende do classificador de áudio próprio para tomar uma decisão já tomada por Speech.
- Cortes temporais não causam perda de sincronização ou faixa de áudio ausente.
- Detecção visual, crop, banner, composição e renderização continuam funcionando.
- Vídeo original, modelo Melody e índice não mudam.
- Lote bloqueado/incompleto não começa parcialmente no Studio.
- Testes automatizados e E2E real passam; saída final é aberta por ferramentas de inspeção e escutada pelo usuário.

## 7. Evidências registradas em 16/10/2026

### Evidência positiva
- Ambiente unificado do laboratório com Python 3.11.9.
- Conversão real RVC Melody executada no ambiente raiz; o usuário escutou e aprovou o resultado.
- Teste E2E de um vídeo: Speech reportou `READY`, classe `PT_BR_NARRATION`, idioma `pt-br`, fala detectada, música não detectada e elegibilidade RVC verdadeira.
- O Studio chamou Melody em CPU e reportou `STUDIO_E2E=SUCCESS` / `VALIDACAO_E2E=PASSOU`.
- O MP4 de saída foi inspecionado via ffprobe: H.264, 1080×1920, AAC mono, duração reportada de 12 segundos.
- O usuário confirmou que o resultado de áudio/vídeo saiu corretamente.
- CI registrado no contexto de trabalho: compileall passou e 68 testes passaram. Essa evidência é do CI citado, não uma afirmação de que todos os cenários de produção foram cobertos.
- Os originais, modelo e índice foram preservados nos testes reportados.

### Limites da evidência
- Um E2E aprovado comprova o caminho testado, não todas as classes de áudio nem o lote histórico inteiro.
- O contrato atual ainda não transfere toda a responsabilidade de RVC/mixagem para Speech.
- A integração de produção, SQLite operacional e publicação no Hub permanecem fora do escopo desta validação.

## 8. Pendência visual conhecida — não alterar nesta migração

Na matriz visual de 14 vídeos analisada em 16/10/2026, o usuário confirmou 13 classificações corretas e um falso positivo do detector Gemini em `178482_7AeDXQWFF2.mp4`, onde não havia marca. Registrar como pendência separada para investigação posterior. **Não modificar o detector Gemini nesta migração de áudio**, para evitar misturar duas mudanças e dificultar a comparação.

Avisos FFmpeg de decodificação observados em um arquivo devem ser investigados separadamente se voltarem a aparecer ou se houver evidência de impacto. Não tratar aviso isolado como prova de falha sem verificar o resultado.

## 9. Ambiente e comandos de validação no Windows

Executar a partir da raiz local do AudioLab. Não usar Python global nem recriar um ambiente RVC legado.

```powershell
# Suíte automatizada do repositório
.\.venv\Scripts\python.exe -m pytest -q

# Verificar importações centrais
.\.venv\Scripts\python.exe -c "import ArmoredSpeech; import ArmoredStudio.service; print('imports OK')"

# Conversão RVC isolada: usar sempre arquivo de saída novo
.\.venv\Scripts\python.exe .\ArmoredStudio\processing\rvc.py .\audio_original.wav .\storage\validation\melody_output.wav melody

# Validar áudio real de Speech, quando o corpus local estiver disponível
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_real_audio.py
```

Para testes que usam corpus, sempre conferir os caminhos, calcular SHA-256 antes/depois, usar saída separada e não versionar vídeos, credenciais, banco local, ambientes virtuais ou saídas de `storage/`. A disponibilidade de cada arquivo local deve ser verificada antes de executar um comando.

## 10. Documentos e escopo

Este é o documento canônico para fluxo, arquitetura, evidências, riscos, testes e plano de migração Speech → Studio. Os READMEs das pastas devem servir apenas como índices para este documento e para os comandos básicos. Documentos de status/planos antigos redundantes foram consolidados para evitar instruções contraditórias. Arquivos de código, testes, assets, requisitos e dados locais não fazem parte desta limpeza documental.

## 11. Fora do escopo e limites de segurança

- Não alterar `armoredcreator-testbase` (base congelada) nem `armoredcreator-test` (repositório de prova).
- Não alterar o repositório operacional `armoredcreator/armoredcreator` nesta fase.
- Não executar Sync, Vision, Stock, IA, Hub, Telegram, Shopee ou publicação a partir deste laboratório.
- Não sobrescrever nem apagar originais, banco local, credenciais, modelo/índice RVC ou assets.
- Não corrigir o falso positivo Gemini no mesmo conjunto de mudanças.
- Nenhuma integração de produção é considerada aprovada apenas pela existência deste documento.


## 12. Auditoria estática detalhada — 17/10/2026

### Dependências reais confirmadas

- `ArmoredSpeech/studio_integration.py` exporta uma decisão READY pelo `studio_bridge.py` e prepara todas as decisões do lote antes da primeira chamada a `studio.process`.
- `ArmoredStudio/service.py` chama `UnifiedStudio.process`, que ainda valida o handoff, usa a decisão de Speech como override de classificação, volta a decidir elegibilidade RVC, extrai áudio via FFmpeg, gera silêncio quando necessário, chama `processing.rvc.converter_voz` e invoca `processing.finalizer.finalizar`.
- `ArmoredStudio/processing/rvc.py` resolve o Python da raiz e carrega o `.pth` e o índice `.index` existentes em `ArmoredStudio/runtime/rvc/models/<voz>`. Reutilizar esta implementação e os assets; não recriar nem substituir modelos.
- `ArmoredStudio/processing/finalizer.py` controla volume da voz, normalização/volume do efeito, mistura via `amix`, introdução e concatenação audiovisual. A mistura é feita no caminho com e sem intro.
- `ArmoredStudio/speech_handoff.py` só aceita contrato v1 e WAV sidecar com nome-base igual ao original. Para áudio final precisaremos validar também o hash do artefato, formato, canais, sample rate, duração e rota.
- `ArmoredSpeech/media.py` hoje prepara WAV mono 16 kHz de referência e pode gerar stem vocal para classe mista. Porém `studio_bridge.py` bloqueia RVC para classe mista, enquanto o teste de `model_processor.py` permite elegibilidade após separação. Até existir validação explícita do stem, a regra operacional segura continua bloqueando Melody para áudio misto.

### Bloqueio de sincronização que precisa de solução antes de remover a mistura

Hoje `finalizer.py` aplica `plan.audio_start` à voz antes de misturá-la, enquanto o efeito sonoro começa desde o início. Se Speech entregar um único WAV já mixado e Studio aplicar `audio_start` nesse WAV, o começo do efeito também será cortado/deslocado. Não podemos simplesmente remover o filtro temporal nem duplicar a mixagem. A migração deve preservar a semântica atual e testar `audio_start = 0` e `audio_start > 0` antes de aposentar o caminho antigo.

### Testes identificados

- `test_studio_bridge.py`, `test_studio_adapter.py`: payload, caminhos, hashes e elegibilidade.
- `test_studio_integration.py`, `test_studio_integration_contract.py`: rotas e contrato de consumo; o caminho de integração usa modo copy-only, não prova RVC/renderização real.
- `test_studio_batch_runner.py`, `test_studio_batch_integration.py`: lote completo, preflight e preservação dos originais/saídas.
- `test_armoredspeech_model_processor.py`, `test_armoredspeech_audio_intelligence.py`, `test_armoredspeech_contracts.py`: classificação e decisão.
- `validate_real_audio.py`, `validate_full_corpus.py` e `diagnose_real_audio.py`: ferramentas para corpus/áudio real; não substituem testes automatizados.

Esta foi uma auditoria estática do código remoto; não executei testes nem renderização nesta sessão. O E2E aprovado anteriormente é evidência do caminho antigo, não da migração ainda não implementada.

### Portões obrigatórios antes de aposentar v1

1. Testes de contrato v2 para identidade, rota, estado, hash, formato, canais, sample rate, duração e caminhos dentro do workspace.
2. Testes provando que Melody não é executado para música, silêncio, ruído, fala estrangeira, classe mista ou decisão desconhecida.
3. Teste que o Studio não reclassifica, não chama RVC e não mistura novamente quando recebe contrato v2.
4. Testes temporais para `audio_start = 0` e `audio_start > 0`.
5. Compatibilidade v1 temporária, mutuamente exclusiva com v2 por item, para impedir RVC/mixagem duplicados.


## 13. Decisão da fronteira temporal

A decisão técnica sobre `audio_start`, a faixa de voz, o efeito e o contrato v2 está em [ADR — áudio Speech → Studio e alinhamento temporal](ADR_SPEECH_AUDIO_TIMELINE.md). A **implementação inicial v2 agora existe na branch de laboratório** e passou na suíte automatizada; isso ainda não é aprovação do E2E real em Windows.

Speech prepara e valida as faixas e aplica a política/ganho do efeito. Studio mantém o alinhamento temporal, corta apenas a faixa de voz e compõe as faixas uma vez no render final. A mixagem final não foi removida do grafo FFmpeg, pois a semântica temporal depende do plano visual que só fica disponível no Studio. Mudar essa ordem de fases não faz parte desta migração.


## 14. Primeira extração estrutural do motor RVC — 17/10/2026

Foi movida a implementação de baixo nível do motor RVC para `armored_core/audio/rvc.py`. `ArmoredStudio/processing/rvc.py` foi reduzido a uma facade de compatibilidade que reexporta a interface pública anterior. O caminho de modelo/índice continua apontando para o diretório existente `ArmoredStudio/runtime/rvc`, e a resolução do ambiente continua usando a `.venv` da raiz.

A extração manteve a facade v1 e o caminho do ambiente/modelos. Em commits posteriores nesta mesma branch, Speech passou a ter um produtor de pacote v2 que usa o motor compartilhado para Melody, gera silêncio determinístico para rotas não elegíveis e prepara a faixa de efeito uma vez por lote. O Studio tem validação v2 e um caminho de render que não reclassifica nem executa RVC/ganhos duplicados. Nenhum modelo, índice, asset ou arquivo de vídeo foi movido. O CI atual passou em compilação e **98 testes automatizados**. Isso não substitui o E2E real no Windows com Melody, ainda pendente.


## 15. Implementação do pacote v2 e portões do lote — 17/10/2026

O código experimental agora inclui:

- `ArmoredSpeech/audio_package.py`: preflight por item, conversão Melody elegível, faixa silenciosa para outras rotas, artefatos WAV PCM 44,1 kHz estéreo e metadados SHA-256/duração/formato.
- `ArmoredSpeech/studio_integration.py`: carrega todas as decisões READY, valida todo o conjunto antes de iniciar conversões, prepara um único efeito comum para o lote, e só retorna pacotes depois que todos foram preparados e conferidos.
- `ArmoredStudio/speech_handoff.py`: contrato v2 valida versão/estado/identidade, original e hashes, rota, elegibilidade, faixas legíveis, metadados e efeito ganho.
- `ArmoredStudio/unified.py` e `ArmoredStudio/processing/finalizer.py`: caminho v2 consome faixas preparadas; não chama RVC nem aplica loudness/volume novamente. O plano temporal continua aparando apenas a voz; o efeito mantém o início da parte principal.
- `ArmoredSpeech/studio_batch_runner.py`: pre-valida todos os pacotes e os hashes antes da primeira chamada ao Studio; se um pacote não passar, a lista de itens processados permanece vazia.
- `armored_core/audio/rvc.py`: conversor comum reaproveitado e usado por Speech; a antiga entrada do Studio ficou como facade de compatibilidade para o caminho v1.

Para reduzir I/O em lotes extensos, a faixa de efeito é preparada uma vez por lote; a identidade/size/mtime de faixas compartilhadas alimentam um cache SHA-256 durante a validação; e o hash completo do modelo/índice Melody é conferido no preflight e no fechamento do lote, sem re-hash do modelo/índice por vídeo. Cada conversão ainda compara rapidamente o caminho e os metadados de arquivo antes de aceitar a saída.

**Validação CI mais recente:** compileall passou e 98 testes passaram em 0,73 s no GitHub Actions: [run 39026971257](https://github.com/armoredcreator/audio/actions/runs/39026971257).

**Limite essencial:** os testes incluem WAVs sintéticos, stubs e o contrato/runner de laboratório. Não houve conversão real RVC nem renderização FFmpeg real no Windows usando o pacote v2 nesta etapa. A aprovação histórica em Windows vale para o caminho v1 anterior; o caminho v2 precisa repetir o E2E com o vídeo isolado de referência, comparar áudio/imagem/duração e validar SHA-256 antes/depois. A PR continua draft, sem merge, e nenhuma mudança foi portada ao repositório operacional.


## 16. Preparar o E2E v2 real no Windows sem tocar no banco/original de origem

A branch inclui `ArmoredSpeech/prepare_v2_smoke_fixture.py`, um utilitário de laboratório para preparar **um único item** já marcado READY e elegível para Melody:

1. Lista itens candidatos no SQLite experimental, sem escrever nele.
2. Copia o MP4 e o WAV sidecar para um workspace exclusivo sob `storage/Videos GRUPO_FONTE_1`, conferindo SHA-256.
3. Usa o backup de SQLite para criar uma **cópia descartável do banco Speech**, insere nela uma linha de teste apontando para as cópias e grava um manifesto de lote completo aprovado.
4. Não altera a mídia de origem nem o banco passado em `--speech-database`. O artefato novo fica sob `storage/validation/speech-v2-smoke-*/`; a cópia de mídia fica em uma pasta de teste identificada com `v2smoke_`.

PowerShell, a partir da raiz do AudioLab:

```powershell
# 1) Aponte somente para o SQLite experimental do ArmoredSpeech.
$SpeechDb = '.\\storage\\database\\armored_speech_experimental.db'

# 2) Liste candidatos READY, narração PT-BR limpa e RVC elegível.
.\\.venv\\Scripts\\python.exe -m ArmoredSpeech.prepare_v2_smoke_fixture `
  --root . --speech-database $SpeechDb --list-ready

# 3) Use exatamente os source_id/item_id exibidos para preparar a cópia isolada.
.\\.venv\\Scripts\\python.exe -m ArmoredSpeech.prepare_v2_smoke_fixture `
  --root . --speech-database $SpeechDb `
  --source-id 'COLE_SOURCE_ID' --item-id 'COLE_ITEM_ID'
```

O utilitário imprime `MANIFEST_PATH` e `SPEECH_DATABASE_COPY`. Copie os caminhos mostrados para executar o lote real:

```powershell
.\\.venv\\Scripts\\python.exe -m ArmoredSpeech.studio_batch_runner `
  --root . `
  --manifest 'COLE_MANIFEST_PATH' `
  --speech-database 'COLE_SPEECH_DATABASE_COPY'
```

**Não acrescente `--copy-only` ao teste real.** O runner exigirá FFmpeg, o banner, o efeito sonoro existente e, se a linha for elegível, o modelo/índice Melody existentes. Um lote bem-sucedido gera um relatório JSON em `storage/validation/` e a saída MP4 na pasta de teste; confira o relatório, ouça o áudio, inspecione o vídeo/streams e compare o SHA-256 do original de teste antes/depois.

Se o arquivo SQLite tiver outro nome, substitua o exemplo pelo caminho real **do banco experimental**. O utilitário recusa o nome comum do banco operacional `armoredcreator.db`, exige a tabela de registro experimental de Speech e abre a fonte em modo somente leitura. Caso não apareça nenhum candidato, não force a elegibilidade: selecione um áudio READY classificado explicitamente, ou conclua primeiro a preparação Speech desse item.
