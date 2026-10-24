# Contrato de legendas ArmoredIA

## Caminho único

O fluxo obrigatório é **contexto Vision aprovado → geração ArmoredIA →
normalização determinística → Policy compartilhada → seleção → persistência
SQLite → validação Hub**. O normalizador corrige apenas apresentação; nunca cria
palavras, fatos, benefícios ou hashtags. A Policy valida a saída final e também
confere se os termos descritivos do texto principal e as hashtags têm suporte no
contexto persistido.

O coração laranja é centralizado pela normalização. Emojis excedentes,
duplicações de hashtag, espaços e quebras de linha são corrigidos quando isso não
muda o sentido. Hashtags sem relação demonstrável são removidas; se não sobrar
uma hashtag válida, a Policy rejeita a candidata e pede nova geração. A Policy
exige 2–3 palavras principais, exatamente um 🧡, 1–2 hashtags distintas,
contextuais e no fim, e bloqueia linguagem de venda, palavras proibidas, marcas,
modelos, medidas e expressões distintivas copiadas do título.

Para evitar inferências, pelo menos um termo principal precisa se apoiar no
contexto Vision. Termos que descrevem atributos precisam estar explicitamente
registrados. Um título curto sem contexto suficiente pode terminar em `BLOCKED`;
o pipeline preserva vídeo e render e não inventa uma legenda para liberar o Hub.

## Provedor local como padrão

Sem `ARMORED_IA_PROVIDER` explícito, o ArmoredIA usa Ollama local (`gemma3:1b`);
não troca silenciosamente para Gemini. Gemini só é escolhido quando a
configuração diz `ARMORED_IA_PROVIDER=gemini`. O perfil Ollama limita cada
requisição a uma candidata, uma tentativa de transporte e 256 tokens, com uma
rodada corretiva da Policy. Uma falha temporária pode receber uma única retomada
durável por item, conforme o ledger de recuperação.

Categoria, tipo, uso, estilo, aparência e descrição Vision são enviados ao
provedor junto ao nome aprovado. O JSON Schema local restringe a frase a 2–3
palavras do vocabulário seguro do contexto e reações subjetivas neutras; as
hashtags ficam restritas a termos comprovados pelo contexto. A Policy continua
sendo a validação final. Se o contexto não permitir uma frase natural válida, o
item fica `BLOCKED`, sem legenda inventada e sem envio.

## Rejeição, feedback e limites

As candidatas são normalizadas e avaliadas individualmente. Se nenhuma passar,
os motivos da Policy e orientações corretivas específicas são incluídos na
próxima solicitação ao provedor. O número de rodadas é limitado por
`ARMORED_IA_POLICY_RETRIES` (0–2; padrão 1); retentativas de transporte são
separadas e limitadas por `ARMORED_IA_MAX_ATTEMPTS`. Tentativas, provedor,
modelo, respostas brutas, correções, rejeições, seleção e status são persistidos
sem segredos ou URLs de produto.

Falhas transitórias de transporte (`TRANSIENT_UNAVAILABLE`, `RATE_LIMITED` ou
`RETRY_BUDGET_EXHAUSTED`) podem receber uma retomada automática durável,
controlada por `ARMORED_IA_AUTO_TRANSIENT_RETRIES` (0–2; padrão 1). A autorização
é consumida uma vez e registrada como `AUTOMATIC_RETRY_CLAIMED`. Erro de
autenticação, conteúdo rejeitado, contexto insuficiente e falha permanente não
recebem essa retomada. Recibo externo incerto também a bloqueia.

- Formatação corrigível: normaliza e valida, sem chamar o provedor novamente.
- Conteúdo rejeitado: gera nova rodada com os motivos das rejeições.
- Contexto insuficiente ou erro permanente: registra `BLOCKED`, preserva o item
  e interrompe novas tentativas automáticas.
- Indisponibilidade temporária: o provedor usa retentativas limitadas e backoff;
  se esgotadas, o item fica `BLOCKED` com a categoria e a evidência do erro.
- Publicação incerta ou recibo diferente de `ABSENT_CONFIRMED`: não altera a
  legenda e não libera nova tentativa.

O Coordinator pode seguir com itens independentes seguros do mesmo lote. O
checkpoint da fonte permanece antes do primeiro item não resolvido, e CATCH-UP
não conclui nem entra em LIVE enquanto esse cursor estiver bloqueado.

## Recuperação explícita de legenda `BLOCKED`

Uma revisão pode autorizar uma única tentativa nova, com ator e motivo
obrigatórios, sem apagar eventos anteriores. O método recusa qualquer recibo
confirmado, `UNKNOWN`, `SENT_UNVERIFIED`, mensagem existente ou resultado sem
ausência comprovada.

Depois de revisar o contexto Vision persistido e confirmar que não existe
resultado Telegram incerto, o operador pode executar no ambiente escolhido:

```powershell
python scripts/reopen_caption_recovery.py `
  --database "<caminho-do-sqlite>" `
  --item-id "<content-id>" `
  --actor "<identificação-do-operador>" `
  --reason "<motivo verificável da revisão>"
```

Isso **não inicia o Coordinator nem publica**. Apenas grava `MANUAL_REOPENED` e
autoriza uma tentativa de IA. A autorização é consumida ao reivindicar a
tentativa. Se a Policy voltar a rejeitar, o item fica `BLOCKED` novamente e exige
outra revisão explícita. Nunca use o procedimento em um item com publicação
incerta.

## Itens históricos com render v5

Para itens como 295–297, o Pipeline reutiliza primeiro uma candidata selecionada
e válida já persistida. Caso não exista, usa ArmoredIA real com os campos Vision
já salvos e persiste candidatas e legenda aprovada antes do Hub. Se o contexto
não sustentar uma legenda, registra `BLOCKED`; não executa Shopee e não repete
Speech, RVC ou Studio. Com uma legenda válida e render íntegro, Hub dry-run
mantém o item em `PUBLISHING` como preparado, sem criar recibo de envio. `BLOCKED`
nunca conta como preparado ou publicado.

## Evidência de provedores reais

Os resultados das chamadas Gemini/Ollama, inclusive candidatas inválidas,
normalizações, feedback, erros transitórios e checagens de persistência após
reabrir o banco isolado ficam em
[`docs/evidence/caption-provider-validation-20261023-v5.json`](evidence/caption-provider-validation-20261023-v5.json).
Na amostra final, Gemini gerou e persistiu para o item 295 a legenda
`Visual moderno 🧡 casual\n#tenis`; candidatas reais do Ollama foram reavaliadas
e uma legenda foi persistida para o item 297: `Estilo 🧡 verão\n#verão`. Para o
item 296, duas rodadas reais do Gemini foram rejeitadas
por falta de suporte contextual/hashtag segura e por especificação técnica; ele
permaneceu `BLOCKED` na cópia. As evidências incluem cada candidata e cada
motivo, sem converter uma rejeição em aprovação.
O SQLite operacional foi apenas leitura para obter o contexto; todas as escritas
foram em uma cópia temporária. Nenhuma consulta Shopee, processamento de mídia,
mensagem Telegram ou publicação foi executada.

Depois do ajuste do perfil local, o Coordinator e o Hub reais foram executados
em clone consistente para o item 296. Ollama `gemma3:1b` gerou e persistiu
`Acidificante 🧡 elegante\n#Acidificante #Equilíbrio`; a Policy aprovou a
legenda, Hub dry-run manteve o item em `PUBLISHING`, a retomada não repetiu IA,
e os hashes do original e do render v5 permaneceram iguais. Speech, Studio,
Vision e Shopee não foram chamados e nenhum recibo Telegram foi criado. O
registro detalhado está em
[`ollama-caption-recovery-clone-20261023-v8.json`](evidence/ollama-caption-recovery-clone-20261023-v8.json).

Chamadas reais do Ollama no fluxo ArmoredIA, com produto de calçados, geraram
`Chinelos 🧡 charmosos\n#chinelos #calçados` e, na validação adicional após
ajustar a recuperação de substantivos de categoria, `Chinelo 🧡 charmoso\n#Chinelo #detalhe`. Ambas foram aprovadas pela mesma Policy; veja
[`ollama-caption-provider-real-20261023-v1.json`](evidence/ollama-caption-provider-real-20261023-v1.json)
e [`ollama-caption-provider-real-20261023-v2.json`](evidence/ollama-caption-provider-real-20261023-v2.json).

## Regressão

Regressão completa após as mudanças de normalização, persistência e retomada:
**617 aprovados, 3 ignorados, 0 falhas**.
