# Recuperação durável de legenda no Hub

## Falha corrigida

O item `-1003788989075_295` tinha aprovação Vision, afiliado, original íntegro e
render Speech v5 íntegro, mas `publication_caption` era nulo e `ia_context_json`
estava vazio. A retomada confiava no render v5 e saltava diretamente para
`PUBLISHING`; por isso o Hub recusava o payload vazio. O tratamento genérico
mandava o item a `RECOVERY`, que o recolocava em `PUBLISHING` e repetia a mesma
falha.

## Contrato atual

- Antes do Hub, o Coordinator valida a legenda pela Policy compartilhada do
  ArmoredIA.
- Reutiliza primeiro uma candidata válida já selecionada e persistida.
- Se não houver candidata, gera uma única recuperação durável por item usando
  ArmoredIA e apenas os dados de produto aprovados que já estão no SQLite.
  Não consulta Vision/Shopee e não altera áudio, vídeo, original ou render.
- A geração e a seleção são registradas em `caption_candidates`,
  `ia_generation_attempts` e `caption_recovery`. A legenda é persistida antes
  de qualquer chamada ao Hub.
- Provedores IA mantêm suas retentativas limitadas e backoff configurados. Se a
  geração ou a Policy falhar, o item fica com recuperação `BLOCKED`. Conteúdo,
  contexto insuficiente, autenticação e falhas permanentes não repetem
  automaticamente. Falhas transitórias de transporte podem receber uma
  retomada durável, limitada por `ARMORED_IA_AUTO_TRANSIENT_RETRIES` (0–2;
  padrão 1), somente sem recibo externo incerto. A autorização e seu consumo
  ficam no ledger. Falhas permanentes exigem revisão explícita.
- Recibos `SENT_UNVERIFIED`, `UNKNOWN` e outros sem ausência comprovada são
  preservados e colocados em quarentena; não alteramos conteúdo nem tentamos
  novo envio. Uma ausência verificada é registrada como `ABSENT_CONFIRMED`, sem
  apagar o recibo anterior.
- Hub em dry-run valida legenda, link, integridade e destino e mantém o item em
  `PUBLISHING`, sem inserir recibo de envio.

## Ajuste da Policy e evidência recente

A geração agora segue criação IA, normalização apenas de forma, validação
estrita da Policy compartilhada, seleção e persistência. O normalizador pode
centralizar/inserir 🧡, remover emojis excedentes, normalizar espaços e deduplicar
ou remover hashtags sem suporte; ele não cria palavras, características ou
hashtags. Uma nova rodada da IA recebe os motivos específicos e as candidatas
rejeitadas, com limite configurável de 0–2 rodadas. O Hub aplica a mesma Policy.

As evidências históricas abaixo registram Gemini quando ele estava selecionado;
isso não o torna requisito nem fallback. O padrão atual é Ollama local, e Gemini
só é usado quando `ARMORED_IA_PROVIDER=gemini` for configurado explicitamente.
Para o item 295, uma execução anterior com Gemini produziu
`Visual moderno 🧡 casual\n#tenis`, aprovada pela Policy e persistida após
reabrir o banco. Para o item 297, candidatas reais do Ollama foram normalizadas;
`Estilo 🧡 verão\n#verão` foi validada e persistida na cópia. Para o item 296,
duas rodadas reais do Gemini produziram opções, mas nenhuma passou: os motivos
registrados foram termo técnico `pH`, atributos sem suporte no contexto Vision
persistido e ausência de hashtag contextual válida. O item ficou `BLOCKED`,
preservado; não foi inventada uma legenda. Confira as candidatas completas e
evidências após reabertura em
[`caption-provider-validation-20261023-v5.json`](evidence/caption-provider-validation-20261023-v5.json).

Uma verificação adicional usou o provedor selecionado pela configuração padrão
(`OllamaProvider`, `gemma3:1b`) através do `CaptionGenerator` real. Com contexto
de calçado aprovado, a inferência local retornou
`Chinelo 🧡 charmoso\n#Chinelo #detalhe`; a Policy aprovou em uma chamada do
provedor. A evidência registra HTTP 200, uma requisição, nenhuma chamada Shopee
ou Telegram e nenhuma escrita no banco operacional:
[`ollama-caption-provider-real-20261023-v2.json`](evidence/ollama-caption-provider-real-20261023-v2.json).

## Evidência integrada do item 295

A reprodução foi feita em clone consistente do SQLite, dos ledgers de orçamento
e quarentena, do original e do render v5. O Coordinator e o ArmoredHub usados
foram os reais; a geração foi executada pelo ArmoredIA com o provedor Gemini
configurado. O Hub ficou em dry-run.

Resultado: uma tentativa IA, Policy aprovada, legenda persistida, estado final
`PUBLISHING`, nenhum recibo Telegram criado, orçamento inalterado e retomada após
reabrir o banco sem repetir IA, Speech ou Studio. SHA-256 do original:
`ef42a6d9988e8c0afbd98b20a4cb26d1cbb31fe8de7a4b34185b915b8766db7a`.
SHA-256 do render v5:
`7fc70ef2331b7cba8e7d1ab415382d4c01282e8cb3fa6c8f9311eee1227c5fe9`.

Evidência detalhada do clone: [`docs/evidence/item295-caption-recovery-20261023.json`](evidence/item295-caption-recovery-20261023.json).

## Regressão

Regressão completa desta atualização: **617 aprovados, 3 ignorados, 0 falhas**.
Os fixtures incompatíveis foram atualizados para fornecer contexto Vision e
legendas válidas pela mesma Policy compartilhada; nenhum teste foi removido ou
ignorado para obter aprovação.

## Retomada operacional

Não reinicie o lote enquanto o processo antigo ainda estiver ativo. Depois de
pará-lo de forma controlada e confirmar que nenhum Coordinator mantém o lock,
retome o mesmo orçamento, sem `--publish` e sem alterar o SQLite:

```bat
START_ALL.bat --run --catch-up-run-id primeira-ativacao-20261023 --max-catch-up-items 3
```

O lote permanece limitado às três atribuições já persistidas. Itens com
recuperação de legenda `BLOCKED` ficam em espera; uma legenda aprovada não
autoriza por si só publicação real.
