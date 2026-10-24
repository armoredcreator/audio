# ARMORED CREATOR — DOCUMENTO MESTRE OPERACIONAL

**Versão de referência:** 24/10/2026
**Repositório:** `armoredcreator/audio` — PR #19 em draft
**Runtime operacional validado:** Windows, `.venv` Python 3.11.9
**Escopo:** arquitetura, contratos, dados, operação segura e evidências realmente obtidas.

> Esta é a documentação mestre única do sistema. A fonte de verdade para o comportamento é o código da branch da PR #19 mais a instalação operacional verificada. Este documento separa validação técnica, envio de certificação e autorização de operação contínua. Ele não declara quota Shopee disponível nem autoriza novos envios.

## 1. Estado operacional confirmado

O pipeline é composto pelo Coordinator e pelos sete módulos na ordem **ArmoredSync → ArmoredVision → ArmoredStock → ArmoredIA → ArmoredSpeech → ArmoredStudio → ArmoredHub**. A inicialização, recuperação e persistência são coordenadas por `armored_core`; o SQLite operacional é a fonte durável dos itens, estados, evidências e recibos.

Nesta campanha foram corrigidos e validados no ArmoredIA o limite local de candidatas Ollama, uma tentativa delimitada de reparo de saída truncada, a incorporação dos motivos de rejeição na nova tentativa e uma rejeição falsa causada por concatenar palavras de hashtags separadas. O provedor padrão é Ollama local (`qwen3:4b`); Gemini é opcional e só é usado quando configurado explicitamente.

O caminho de consulta Vision agora é fechado por padrão. Cada chamada Shopee efetivamente enviada — consulta exata, criação de link afiliado e cada retry permitido — consome uma unidade do orçamento persistente antes do I/O de rede. Sem autorização, evidência, validade, ID e limite válidos, Vision mantém o item pendente com `SHOPEE_QUERY_BUDGET_BLOCKED`; não envia a consulta. O orçamento é imutável ao retomar.

Foram autorizadas e concluídas três publicações reais de certificação, uma por rota. O Coordinator usou itens existentes, aprovados e renderizados; o ArmoredIA gerou e persistiu legendas válidas com Ollama real; originais e renders foram preservados. O Hub local confirmou os três novos recibos:

| Item | Recibo Telegram | Destino | Legenda v3 validada |
|---|---:|---|---|
| `-1003788989075_2447` | `1356` | `-1004341972306`, tópico `228` | `Canecas 🧡 douradas` / `#Canecas #douradas` |
| `-1002698134896_178492` | `1357` | `-1004341972306`, tópico `1160` | `Rádio 🧡 bonito` / `#Rádio #Autoradio` |
| `-1002039708059_12042` | `1358` | `-1004341972306`, tópico `1327` | `Menina 🧡 linda` / `#MENINA #ALCINHA` |

As três verificações foram `CONFIRMED`. Os recibos históricos foram mantidos. A mensagem de origem `1040` e o recibo `1339` continuam em estado incerto/quarentena e bloqueados para reenvio. Os 12 vídeos da execução `published12-replay-20261023-v3` não foram renderizados novamente nem publicados nesta certificação.

A certificação de três envios comprova o Hub, as legendas selecionadas e as três rotas. Ela **não equivale** a liberar o processamento contínuo de toda a fila ou a afirmar que todos os itens históricos passaram novamente por Sync, Vision, Stock, IA, Speech e Studio. Os itens ambíguos permanecem isolados.

## 2. Arquitetura e estrutura

### 2.1 Composição

`run_coordinator.py` é a entrada Python. `START_ALL.bat` valida primeiro o runtime de áudio/RVC, compõe os serviços e roda preflight em cópia consistente; só então, para um modo de execução, valida configuração e Bot API local. O Coordinator adquire o lock do runtime, constrói serviços reais e chama os módulos em sequência. Não iniciar módulos individualmente para contornar estados ou orçamento.

Áreas principais:

- `armored_core/`: Coordinator, CLI, modelos/estados, SQLite, pipeline, recovery, preflight, checkpoints, orçamento de lote, quarentena, storage, roteamento e integridade.
- `ArmoredSync/`: leitura Telethon, descoberta por fonte/tópico, ingestão e cursores.
- `ArmoredVision/`: resolução segura de URL Shopee, identidade canônica, aprovação/oferta, contexto do produto e orçamento comercial.
- `ArmoredStock/`: reutilização ou aquisição controlada do original e sua integridade.
- `ArmoredIA/`: provedores, contexto, geração, normalização, Policy, seleção e evidências de candidatas.
- `ArmoredSpeech/`: classificação e preparação de áudio, voz PT-BR, efeitos e RVC Melody, contrato de áudio v5.
- `ArmoredStudio/`: composição visual, integração FFmpeg e MP4 final; consome o pacote Speech e não assume a propriedade do áudio.
- `ArmoredHub/`: validação do payload, idempotência, tópicos, Bot API local e registro/verificação do recibo.
- `storage/database/`: banco e ledgers duráveis. `credentials/`: segredos locais, nunca versionados.
- `START_ALL.bat`, `run_audio_preflight.py`, `scripts/ensure_bot_api.py`: entrada e verificações Windows.

### 2.2 Responsabilidades e contratos

1. **Sync** registra a mensagem com identidade da fonte, tópico de origem e cursor. Deduplicação é aplicada por mensagem, URL normalizada e identidade canônica; uma duplicata não deve alcançar Vision nem download.
2. **Vision** resolve apenas identidade Shopee exata. Reutiliza identidade/aprovação persistida; URL sem produto resolvido, cache negativo válido, quota indisponível ou erro técnico mantém o item pendente e informa a causa. Uma URL externa ou encurtada não classificada não é automaticamente “sem oferta”.
3. **Stock** reutiliza original existente íntegro. Downloads só acontecem para itens aprovados e necessários, sempre verificando caminho/hash e preservando o original.
4. **IA** recebe contexto aprovado. Gera conteúdo criativo, normaliza só formatação segura e audita as correções; a Policy verifica o texto final; só então selector e SQLite persistem candidato selecionado, resultado e evidência. O Hub reutiliza a legenda persistida válida.
5. **Speech** executa a rota de áudio v5. Narrativa PT-BR elegível pode usar RVC Melody; áudio não elegível segue a rota prevista de música/efeito. A classificação incerta não força RVC.
6. **Studio** consome o contrato v5, verifica caminhos e hashes, preserva o original e produz o render audiovisual. Não deve repetir render válido na retomada.
7. **Hub** exige vídeo íntegro, legenda válida, link afiliado válido e destino correspondente. Dry-run deixa o conteúdo preparado em `PUBLISHING`; somente confirmação externa permite `PUBLISHED`. `UNKNOWN` jamais significa `ABSENT` ou `CONFIRMED`.

Contrato Speech → Studio: versão `v5`; inclui identificação do item, caminhos autorizados das trilhas, classificação/rota, hashes e metadados de timeline. O contrato e o consumidor devem evoluir juntos.

## 3. Rotas Telegram

A configuração operacional exige três pares de rota únicos. Os IDs são carregados de `credentials/project.env` (sem imprimir segredos):

| Fonte Sync | Destino Hub | Tópico Hub |
|---|---|---:|
| `-1003788989075` | `-1004341972306` | `228` |
| `-1002698134896` | `-1004341972306` | `1160` |
| `-1002039708059` | `-1004341972306` | `1327` |

A terceira origem é um canal sem `topic_id` de origem; o destino Hub ainda é o tópico `1327`. Sync usa Telethon/sessão de usuário. Hub envia por Telegram Bot API local, porta `8081`, usando token do bot; não substituir por endpoint público. `START_ALL.bat` chama `scripts/ensure_bot_api.py` para verificar serviço já existente ou iniciar um serviço gerenciado pelo launcher, autenticar por `getMe` e encerrar apenas o processo que ele próprio iniciou. O preflight sem modo de execução não inicia Coordinator nem serviço Bot API.

## 4. SQLite, estados e histórico

`armored_core/database.py` cria/migra o esquema preservando dados antigos. Tabelas centrais incluem:

- `items`: identidade canônica `content_id`, fonte/mensagem/tópico, URL e caminhos, hashes, dados de afiliado, legenda, tentativas, estado e erro.
- `state_events`: trilha de transições.
- `product_identity_claims` e metadados: proprietário canônico de produto para deduplicação global.
- `vision_product_lookup_events`: evidência de consultas positivas/negativas e validade do cache.
- `caption_candidates`, `ia_generation_attempts`, `caption_recovery`, `caption_recovery_events`: geração, candidatas, Policy, retries e recuperação auditável.
- `publications`: chave idempotente, destino, ID de mensagem e `verification_status`.
- `sync_sources`, `sync_topics`, `sync_source_topics`, `sync_state`: cursores e `historical_complete`.
- `runtime_locks`: exclusão de duas execuções operacionais concorrentes.

Estados do item: `RECEIVED`, `VISION`, `WAITING_VISION`, `IA`, `SPEECH`, `STUDIO_BATCH`, `STUDIO`, `STUDIO_COMPLETE`, `PUBLISHING`, `PUBLISHED`, `RECOVERY`, `FAILED`, `DUPLICATE`. O ledger de publicação distingue `CONFIRMED`, `ABSENT` e `UNKNOWN`. O pipeline não promove automaticamente um item com recibo desconhecido. Quarentena impede qualquer etapa/reenvio do item correspondente, sem parar por padrão itens independentes; o cursor não pode passar por mensagem histórica não resolvida que realmente o bloqueie.

Orçamentos ficam separados do estado dos itens: `catch-up-work-budget.sqlite3` fixa os IDs e limite de um lote; `continuous-work-budget.sqlite3` persiste configuração diária, itens únicos admitidos e lotes; `shopee-query-budget.sqlite3` persiste tentativas comerciais. IDs de orçamento e limites existentes, inclusive `lote-001` e `primeira-ativacao-20261023`, não podem ser reciclados ou aumentados para contornar uma barreira.

## 5. Legendagem ArmoredIA

Regra contratual do texto de legenda: frase principal de 2–3 palavras, exatamente um coração laranja 🧡 na posição central, 1–2 hashtags distintas e contextualizadas, sem emoji extra, texto comercial agressivo, benefício inventado, medidas/termos técnicos vedados, marca/modelo proibidos ou cópia excessiva do nome completo. Link afiliado é anexado pelo Hub, separado do texto validado.

Fluxo: contexto aprovado → geração → normalização determinística apenas de formatação → Policy → selector → persistência → Hub. Normalização trata espaços/quebras, emojis excedentes e duplicação/posição de hashtags quando a operação não inventa conteúdo. Se a semântica não passa, ArmoredIA recebe motivo real da rejeição para uma nova solicitação limitada. Falha transitória tem retry limitado; truncamento de Ollama admite uma única solicitação de reparo de formato; bloqueio irrecuperável persiste como `BLOCKED` e exige recuperação autorizada explícita. Não usar texto genérico para mascarar falha.

O defeito localizado nesta campanha foi: configuração geral pedia 10 candidatas ao Ollama embora o contexto/token budget só sustentasse uma resposta estruturada; Qwen terminava com `done_reason=length` e JSON incompleto. Além disso, a Policy combinava palavras de hashtags distintas ao procurar frase de produto, gerando um falso positivo. A configuração Ollama agora limita candidatas por provedor e faz no máximo um reparo estruturado; a Policy verifica a frase principal sem concatenar hashtags. A próxima tentativa recebe feedback para evitar repetir a expressão rejeitada.

Variáveis locais: `ARMORED_IA_PROVIDER=ollama`, `ARMORED_IA_OLLAMA_MODEL=qwen3:4b`, `ARMORED_IA_OLLAMA_MAX_CANDIDATES=1`, `ARMORED_IA_OLLAMA_FORMAT_RETRIES=1`. Gemini é opcional e exige seleção explícita/configuração de chave; Ollama não usa cota Gemini.

## 6. Vision, orçamento Shopee e Stock

A resolução suporta URL Shopee direta e URL curta conforme resolver seguro. Identidade canônica `shop_id:item_id` já representada bloqueia processamento duplicado. Cache negativo só representa consulta exata concluída sem oferta, com tempo; ausência de cache, produto não resolvido, erro de rede/autenticação, falta de URL e resultado inconclusivo são categorias diferentes.

Configuração de autorização comercial necessária em `credentials/project.env` (ou mecanismo local já usado, nunca no Git):

- `ARMORED_SHOPEE_QUERIES_AUTHORIZED=1`
- `ARMORED_SHOPEE_QUERY_BUDGET_ID` (identidade estável)
- `ARMORED_SHOPEE_QUERY_LIMIT` (limite total aprovado)
- `ARMORED_SHOPEE_QUOTA_EVIDENCE` (referência da autorização/quota)
- `ARMORED_SHOPEE_QUOTA_VALID_UNTIL` (ISO-8601 com fuso)
- `ARMORED_SHOPEE_QUERY_LEDGER_PATH` (opcional; por padrão ao lado do banco)

Sem todos os campos válidos, a consulta é bloqueada. O limite existente não pode ser reconfigurado; para autorização futura, use uma identidade nova somente após aprovação, e nunca apague ledger. A contagem é por tentativa de rede: consulta exata, eventual mutação `generateShortLink` e retries. O slot é consumido antes do envio e não devolvido em falha/crash.

Stock verifica o arquivo original, hash e caminho permitido antes de reutilizar. Downloads antecipados ou reaquisição de original íntegro não fazem parte do fluxo normal.

## 7. Speech e Studio

Speech é o único dono do áudio: classificação, decisões de narração, efeitos, preparação de trilhas e RVC Melody. RVC roda pelo ambiente/runtime validado; não alterar Python 3.11.9, Torch, NumPy ou os modelos sem evidência técnica e regressão específica. Studio é dono da montagem visual, recorte/enquadramento, composição e render FFmpeg; verifica contrato Speech v5, valida MP4 e mantém original e resultados anteriores. Na retomada, manifestos e hashes válidos evitam repetir IA, Speech, RVC ou Studio.

## 8. Hub, idempotência e republicação

Hub usa Bot API local em `127.0.0.1:8081`. Antes do envio verifica resposta do Bot API, bot, permissões/destino, tópico, arquivo, hash, legenda final, URL e idempotência. Cada envio é uma janela de efeito externo; recibo incerto fica em `UNKNOWN`/quarentena e não é repetido sem reconciliação externa. Recibos antigos permanecem imutáveis.

Os três recibos de certificação novos são `1356`, `1357`, `1358` e não substituem histórico. A republicação v3 dos 12 itens é um fluxo especial/versionado e não pode ser admitido automaticamente no pipeline normal. O recibo `1339`/origem `1040` segue bloqueado.

## 9. CATCH-UP, LIVE, lote e retomada

`--run` executa um lote durável limitado e encerra; não equivale a operação contínua. O lote é identificado por `--catch-up-run-id`, e o limite global abrange itens únicos históricos, novos e retomados; atribuições e IDs não podem mudar em retomada.

`--continuous` mantém o Coordinator ativo: conclui o CATCH-UP somente quando cursores e pendências permitem legitimamente, então transita a LIVE e monitora novas mensagens. Ele requer um ID contínuo imutável, teto diário UTC e tamanho de lote persistidos. Não permite publicar por si só; envio real requer `--publish` independente. Com Hub dry-run, render válido fica preparado em `PUBLISHING`, sem ser declarado `PUBLISHED`.

Comandos (executar apenas após aprovações e liberação do proprietário):

```bat
cd /d C:\Users\Administrador\Downloads\ArmoredCreator-final
START_ALL.bat
```

O comando sem argumentos roda preflight de runtime e composição em clone, valida configuração e termina sem iniciar Coordinator ou Bot API. Para lote limitado novo (não reutilizar IDs existentes):

```bat
START_ALL.bat --run --catch-up-run-id <ID-IMUTAVEL-NOVO> --max-catch-up-items 3
```

Para operação contínua com limites explícitos, ainda sem publicação real:

```bat
START_ALL.bat --continuous --continuous-operation-id <ID-IMUTAVEL-NOVO> --max-items-per-day 3 --max-items-per-batch 3
```

A operação contínua permanece desativada até liberação final. Publicação real exige autorização independente e `--publish`; não adicionar essa opção automaticamente. Consultas Shopee continuam bloqueadas até orçamento durável configurado/validado. Sem quota autorizada, itens que precisem de nova consulta ficam pendentes; itens com aprovação e afiliado já persistidos podem prosseguir sem consulta extra.

## 10. Encerramento e recuperação

Parar o processo encerra o Coordinator e libera lock; nunca apagar ledgers para “destravar”. Na retomada, usar o mesmo ID e limite. Consultar status/eventos no SQLite somente em cópia para diagnóstico; não editar estados manualmente. `CONFIRMED` conserva recibo. `UNKNOWN` exige reconciliação do destino/tópico por leitura e evidência. `BLOCKED` de legenda só pode ser tentado novamente pelo mecanismo de autorização auditável. A recuperação não muda histórico, arquivos originais, checkpoints ou recibos antigos.

## 11. Validação e evidências

Evidências verificadas nesta campanha:

- Regressão operacional: **733 aprovados, 4 ignorados, zero falhas**; duração 91,93 s, após integrar o orçamento HTTP por tentativa.
- Testes focais do orçamento/transport na instalação operacional: **77 aprovados**.
- PR regression anterior à alteração de contagem por tentativa: **622 aprovados, 3 ignorados, zero falhas**; CI anterior teve sucesso nos workflows Windows/RVC asset migration e Speech isolated.
- Testes focais na alteração do orçamento integrada: **52 aprovados**. A regressão completa da PR passou após a alteração; os workflows deste novo commit devem concluir antes do fechamento do CI.
- Preflight Windows sem argumentos: runtime Python 3.11.9, runtime de áudio, composição dos sete módulos, configuração e três rotas passaram; não iniciou Coordinator nem enviou mensagens.
- Ollama local real: recuperação de legenda por Coordinator para três itens, Policy aprovada, candidatas/evidências persistidas, sem nova renderização; originais e renders conservaram seus hashes.
- Hub/Bot API local real: `getMe`/destino/permissões verificados; três envios autorizados e recibos CONFIRMED 1356–1358, um por tópico.
- SQLite operacional após certificação: `quick_check=ok`, `foreign_key_check` sem violações, 14.486 itens. Recibos históricos e checkpoints preservados; estados históricos ambíguos continuam bloqueados.
- Sem consulta comercial Shopee nesta campanha, sem reprocessamento dos 12 v3, sem ativação do Coordinator contínuo e sem publicação em massa.

A integração anterior de Coordinator com mídia real, ArmoredIA, Speech v5/RVC, Studio e Hub em dry-run foi validada em clones e consta na evidência histórica da campanha. O que ainda não foi exercitado nesta rodada é um CATCH-UP operacional integral até LIVE com a fila de produção inteira; não se inferiu essa transição dos três envios de certificação.

## 12. Pendências externas e decisão de prontidão

A configuração e o código estão preparados para bloquear consultas sem orçamento e publicação sem `--publish`. Para operação contínua com itens que ainda precisem de Vision, é necessário fornecer autorização/quota Shopee válida, ID, teto e validade no ledger local. Para envio normal contínuo, o proprietário deve liberar explicitamente a operação e a opção de publicação. A operação contínua fica desativada até essa liberação.

Portanto, a certificação técnica disponível é: **três rotas reais do Hub confirmadas e correção local de legendas validada; envio adicional, consulta comercial e operação contínua permanecem sob portões independentes.** Não classificar o projeto como “100% operacional contínuo” até validar orçamento Shopee ativo, concluir a liberação do operador e executar CATCH-UP/LIVE conforme o estado real dos checkpoints.
