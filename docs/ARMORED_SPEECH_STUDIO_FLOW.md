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
- `ArmoredStudio/processing/rvc.py`: conversor RVC usado no Studio; a implementação testada deve ser reutilizada, não reescrita sem necessidade.
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
