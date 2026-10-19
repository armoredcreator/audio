# ArmoredSpeech — contrato experimental

## Posição no fluxo histórico

`ArmoredSync` completo → `ArmoredVision` completo → `ArmoredStock` completo → `ArmoredIA` → **ArmoredSpeech completo** → ArmoredStudio → ArmoredHub.

ArmoredSpeech é um lote histórico por fonte. Não faz processamento item a item intercalado com IA/Studio/Hub e não publica nada no Telegram. Esta branch é um protótipo isolado; ainda não integra o repositório de produção.

## Arquivos

O original nunca é alterado. O WAV derivado fica junto do item, com o mesmo nome-base e extensão diferente:

```text
storage/
└── Videos GRUPO_FONTE_1/
    └── 291/
        ├── 291_finallinkoriginal.mp4
        └── 291_finallinkoriginal.wav
```

O caminho é sempre registrado no SQLite junto de `source_id` e `item_id`. Não separar em pastas de “narrados” e “mutados”; a rota é lógica e vem da classificação persistida.

## Rotas de áudio para o Studio

| Classe | RVC | Preparação | Comportamento final esperado no Studio |
|---|---:|---|---|
| `PT_BR_NARRATION` | Sim | WAV extraído | RVC na narração PT-BR |
| `PT_BR_NARRATION_WITH_MUSIC` | Só após validação do stem | Speech separa voz e música, verifica duração/energia e grava recibo com hashes da origem, stem e modelo | RVC Melody recebe somente o stem vocal validado; se a separação falhar, o lote falha explicitamente em vez de silenciar a voz |
| `MUSIC_ONLY` | Não | WAV de referência | silenciar áudio original e aplicar o efeito sonoro existente |
| `NOISE` | Não | WAV de referência | silenciar áudio original e aplicar o efeito sonoro existente |
| `NO_AUDIO` | Não | WAV silencioso determinístico | aplicar efeito sonoro sem RVC |
| `NO_USABLE_NARRATION` | Não | WAV de referência, se houver | silenciar áudio original e aplicar efeito sonoro |
| `FOREIGN_SPEECH` | Não | WAV de referência | não enviar para RVC PT-BR; silenciar e aplicar efeito conforme regra do projeto |
| `UNKNOWN` | **Bloqueado** | não liberar WAV como aprovado | pendência/erro; nunca presumir elegibilidade |

A classificação `FOREIGN_SPEECH` está incluída como proteção inicial. A rota final de fala estrangeira precisa ser confirmada antes de integração com Studio.

Para classe mista, `rvc_eligible` no payload continua falso até Speech validar um recibo de separação vocal. `rvc_candidate_eligible` registra apenas a possibilidade inicial baseada na decisão persistida. O render usa o stem vocal separado, não o WAV misturado; ausência de recibo válido dispara erro explícito antes do primeiro RVC.

## SQLite / contrato com Studio

A tabela experimental `armored_speech_results` é propriedade exclusiva do ArmoredSpeech. Ela guarda:
- `source_id`, `item_id`: identidade e origem do item;
- `video_path`, `wav_path`: caminhos dos arquivos;
- `audio_class`, `language`, `speech_detected`, `music_detected`, `confidence`;
- `rvc_eligible`: decisão explícita e fail-closed;
- `status`, hashes do original antes/depois e erro.

**ArmoredStudio não deve consultar diretamente essa tabela nem importar módulos internos do Speech.** Nesta branch, o produtor do contrato está em `ArmoredSpeech/studio_bridge.py`, a entrega por item/lote em `ArmoredSpeech/studio_integration.py`, e a validação/consumo pertencem ao Studio (`ArmoredStudio/speech_handoff.py` e `ArmoredStudio/unified.py`). O pré-check do lote bloqueia a primeira chamada ao Studio se algum item não estiver pronto. Isso comprova a ligação de código e os testes de contrato, mas não certifica a conversão real RVC/Melody, a renderização completa no ambiente local nem uma integração com o repositório de produção. ArmoredSpeech não escolhe destino do Hub: o roteamento continua ligado ao `source_id` e à configuração de destino já existente.

## Segurança e limpeza

- Não modificar, renomear ou mover o MP4 original.
- Confirmar SHA-256 antes/depois do processamento.
- Erro de classificação, separação ou WAV ausente não libera RVC.
- Não excluir WAV ao terminar ArmoredSpeech. Preservar até o Studio confirmar que concluiu e não precisa mais dele.
- Se Studio falhar, preservar o WAV para retry.
- Não gerar vídeo final nem publicar no Telegram nesta fase.

## Estado real desta branch e E2E v5

O runner, o registro SQLite e o contrato de dados continuam experimentais; a tabela SQLite desta branch não é o schema operacional do ArmoredCreator. O classificador automático usa Whisper para idioma, Silero VAD para atividade de fala e AudioSet/AST para evidência de música; existe também um modo opcional de manifesto revisado.

Em 17/10/2026, o E2E real no Windows aprovou o contrato v5 para o item `source_teste/5`: `PT_BR_NARRATION` → `RVC_MELODY`, `STUDIO_SPEECH_BATCH=SUCCESS`, `ORIGINALS_INTACT=True` e renderização H.264/AAC. O hash do original antes/depois coincidiu com o manifesto (`c489fcaae803f36407c82dde3b93ff68077001ef5607909081a2c6555bee15a1`). O relatório fica em `storage/validation/speech-studio-v5-item5-20261017T185708Z.json`.

O resultado comprova somente esse caminho de narração PT-BR. É necessário avaliar as classes sem RVC e fazer benchmark/classificação com exemplos rotulados antes de declarar o ArmoredSpeech pronto para produção. Não levar esta base nem o runner experimental para o repositório operacional por causa de um único E2E bem-sucedido.
