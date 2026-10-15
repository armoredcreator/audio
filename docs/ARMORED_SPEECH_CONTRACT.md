# ArmoredSpeech — contrato experimental

## Posição no fluxo histórico

`ArmoredSync` completo → `ArmoredVision` completo → `ArmoredStock` completo → **ArmoredSpeech completo** → ArmoredIA → ArmoredStudio → ArmoredHub.

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
| `PT_BR_NARRATION_WITH_MUSIC` | Sim | separar e manter o stem vocal no WAV | RVC somente na voz separada |
| `MUSIC_ONLY` | Não | WAV de referência | silenciar áudio original e aplicar o efeito sonoro existente |
| `NO_AUDIO` | Não | WAV silencioso determinístico | aplicar efeito sonoro sem RVC |
| `NO_USABLE_NARRATION` | Não | WAV de referência, se houver | silenciar áudio original e aplicar efeito sonoro |
| `FOREIGN_SPEECH` | Não | WAV de referência | não enviar para RVC PT-BR; silenciar e aplicar efeito conforme regra do projeto |
| `UNKNOWN` | **Bloqueado** | não liberar WAV como aprovado | pendência/erro; nunca presumir elegibilidade |

A classificação `FOREIGN_SPEECH` está incluída como proteção inicial. A rota final de fala estrangeira precisa ser confirmada antes de integração com Studio.

## SQLite / contrato com Studio

A tabela experimental `armored_speech_results` guarda:
- `source_id`, `item_id`: identidade e origem do item;
- `video_path`, `wav_path`: caminhos dos arquivos;
- `audio_class`, `language`, `speech_detected`, `music_detected`, `confidence`;
- `rvc_eligible`: decisão explícita e fail-closed;
- `status`, hashes do original antes/depois e erro.

Studio deve consultar esse registro e consumir o WAV pelo caminho; não precisa receber ou mover o arquivo entre módulos. ArmoredSpeech não escolhe destino do Hub: o roteamento continua ligado ao `source_id` e à configuração de destino já existente.

## Segurança e limpeza

- Não modificar, renomear ou mover o MP4 original.
- Confirmar SHA-256 antes/depois do processamento.
- Erro de classificação, separação ou WAV ausente não libera RVC.
- Não excluir WAV ao terminar ArmoredSpeech. Preservar até o Studio confirmar que concluiu e não precisa mais dele.
- Se Studio falhar, preservar o WAV para retry.
- Não gerar vídeo final nem publicar no Telegram nesta fase.

## Estado real desta branch

O runner, o registro SQLite e o contrato de dados são experimentais. O classificador automático usa Whisper para idioma, Silero VAD para atividade de fala e AudioSet/AST para evidência de música; existe também um modo opcional de manifesto revisado. A classificação ainda precisa de benchmark e avaliação auditiva reproduzíveis em exemplos rotulados antes de declarar o ArmoredSpeech pronto ou portar o código para produção. A tabela SQLite desta branch é experimental e não é o schema real do ArmoredCreator.
