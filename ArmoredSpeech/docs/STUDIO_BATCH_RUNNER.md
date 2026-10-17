# Executor isolado ArmoredSpeech → ArmoredStudio

Este executor é **exclusivo do AudioLab**. Ele não inicia Sync, Vision, Stock, IA, Hub nem publicação. Recebe um manifesto explícito do lote aprovado e completo do Speech, lê as decisões READY somente pela ponte de contrato e chama a fronteira pública `ArmoredStudio.service.build(root).process(item, speech_payload=...)`.

## O que impede processamento prematuro

O executor só aceita um manifesto JSON com:
- `schema_version: 1`;
- `phase: "ARMORED_SPEECH"`;
- `phase_status: "COMPLETE"`;
- `approved: true`;
- `expected_item_count` igual ao tamanho exato da lista `items`;
- IDs únicos `source_id + telegram_message_id`;
- todos os itens no estado `IA`, que é a fronteira imediatamente anterior ao Studio;
- caminho de cada original dentro do workspace de item e hash SHA-256 igual ao declarado.

Antes da primeira chamada ao Studio, `process_items_with_speech(...)` faz preflight de todos os registros Speech e exige decisão `READY` e artefatos disponíveis. O validador do Studio verifica novamente o contrato, identidade, WAV legível, caminhos, hashes e elegibilidade RVC. Se qualquer item do lote não estiver pronto, nenhum item é enviado ao Studio.

**Importante:** o manifesto representa a lista integral aprovada pelo operador para esse lote; o executor não consegue adivinhar se uma lista arbitrária representa toda a coleta histórica. Gere/revise esse manifesto só depois de a fase Speech declarar conclusão.

## Manifesto de exemplo

Salve dentro do root do laboratório, por exemplo em `storage/validation/speech-phase-complete.json`. Os caminhos podem ser absolutos ou relativos ao root.

```json
{
  "schema_version": 1,
  "phase": "ARMORED_SPEECH",
  "phase_status": "COMPLETE",
  "approved": true,
  "allow_copy_only": true,
  "batch_id": "speech-lote-validado-001",
  "expected_item_count": 1,
  "items": [
    {
      "content_id": "source1_12345",
      "telegram_message_id": "12345",
      "source_id": "source1",
      "state": "IA",
      "workspace": "storage/Videos GRUPO_FONTE_1/12345",
      "original_path": "storage/Videos GRUPO_FONTE_1/12345/12345_finallinkoriginal.mp4",
      "original_sha256": "COLOQUE_AQUI_OS_64_CARACTERES_SHA256_REAIS",
      "affiliate_name": "produto",
      "affiliate_url": "https://shopee.com.br/product/123"
    }
  ]
}
```

O valor de `original_sha256` acima é deliberadamente um placeholder, não um hash utilizável. Calcule o hash real do arquivo e confirme a lista antes de executar.

O SQLite indicado deve ser o banco **experimental** do ArmoredSpeech dentro do mesmo root do laboratório. Não passe banco, credenciais, sessão nem configuração do ArmoredCreator de produção.

## Primeiro teste: somente handoff/contrato

O modo `--copy-only` chama o serviço real do Studio, valida o handoff e copia bytes do original para uma saída de teste. Ele **não** executa detecção visual, FFmpeg, composição, RVC nem prova qualidade do vídeo. Só pode ser usado quando o manifesto contém `"allow_copy_only": true`.

```powershell
Set-Location 'C:\Users\Administrador\Downloads\ArmoredAudioLab'
.\.venv\Scripts\python.exe -m ArmoredSpeech.studio_batch_runner `
  --root . `
  --manifest .\storage\validation\speech-phase-complete.json `
  --speech-database .\storage\database\armored_speech_experimental.db `
  --copy-only
```

Ajuste apenas o caminho do SQLite para o nome real do banco experimental. O relatório JSON é gravado em `storage/validation/` e informa modo, lote, itens, caminhos de saída e integridade dos originais. Em caso de falha, o executor grava status `FAILED` e propaga erro; não apaga WAVs de Speech.

## Execução real

A execução sem `--copy-only` tenta análise visual/áudio e processamento real. Antes dela, confirme a presença e a identidade dos assets oficiais `ArmoredStudio/assets/banner.png` e `ArmoredStudio/assets/efeitosonoro.wav`, FFmpeg e modelo/índice Melody. Use primeiro uma lista pequena, revisada e de cópias isoladas. Não execute a publicação; Hub permanece fora do escopo. Um `SUCCESS` neste runner comprova o lote processado e a integridade registrada dos originais, mas a precisão de VEO/Gemini/barras pretas e a qualidade auditiva ainda exigem avaliação por caso.

## Garantias e limitações

- Nunca infere conclusão olhando somente para o número de registros READY no SQLite.
- Não acessa o banco do Coordinator nem inicia integrações externas.
- Não aceita originais fora do root ou fora do workspace declarado.
- Recusa destinos Studio já existentes antes da primeira chamada, preservando resultados anteriores.
- Recalcula hash dos originais antes e depois; saída precisa existir, ser não vazia e permanecer no root.
- Preflight bloqueia o lote inteiro se faltar decisão READY/artefato em qualquer item.
- Não apaga fontes, WAVs Speech, modelo, índice nem assets.
- O executor não prova classificação correta do Speech nem aprova automaticamente o lote histórico: isso depende da fase Speech e da revisão do manifesto.
