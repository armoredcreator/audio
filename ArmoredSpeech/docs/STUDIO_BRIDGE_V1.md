# Ponte ArmoredSpeech → ArmoredStudio (laboratório isolado)

## Escopo desta primeira entrega

Esta ponte é um adaptador de leitura no repositório isolado `armoredcreator/audio`. Ela lê uma decisão READY da tabela experimental `armored_speech_results` e produz uma diretiva tipada para um futuro consumidor do Studio. Não modifica o repositório `armoredcreator/armoredcreator`, não executa FFmpeg/RVC, não altera vídeos e não publica.

Implementação: `ArmoredSpeech/studio_bridge.py`.

## Contrato v1

- Chave: `source_id + item_id`.
- Fonte da verdade: registro SQLite persistido; não deduzir modo pelo nome do arquivo.
- Só aceita status `READY`; ausente, `PROCESSING`, `BLOCKED`, `FAILED` ou schema incompatível interrompem a rota.
- Original e WAV precisam existir para uso normal.
- A decisão inclui classe original, modo compatível com os nomes atuais do Studio, idioma, confiança, elegibilidade para RVC e requisito de separação vocal.
- A elegibilidade para RVC é recalculada de forma conservadora: apenas `PT_BR_NARRATION`, idioma PT, fala detectada e flag persistida verdadeira. A flag do banco sozinha nunca é suficiente.
- `PT_BR_NARRATION_WITH_MUSIC` exige separação vocal validada; a ponte mantém RVC bloqueado até existir um artefato/resultado de separação explicitamente validado.
- `NOISE` e `NO_USABLE_NARRATION` são preservados em `source_audio_class`, mas mapeados para `MUSIC_ONLY` no campo de compatibilidade porque o Studio atual usa a rota não-RVC/efeito para modos sem narração elegível.
- A ponte é somente leitura. Ela não cria tabela, não atualiza estado e não apaga WAVs.

## Limitações conhecidas (não esconder)

O schema experimental atual ainda não armazena `classifier_version`, versão de schema persistida nem um status explícito de validação do stem vocal. Por isso, `classifier_version` é `None` e fala+musica permanece inelegível para RVC. Essas lacunas devem ser resolvidas antes de integrar o adaptador diretamente no Studio.

O Studio atual em `armoredcreator/armoredcreator` ainda classifica o áudio dentro de `AnalysisEngine` usando `AudioIntelligence`. Esta entrega não altera esse comportamento; logo, é um contrato/adaptador testável, não integração ponta a ponta. A próxima etapa é ampliar o registro versionado, adicionar evidência de separação vocal e então criar testes de integração com um consumidor Studio isolado.

## Testes de contrato

Executar no ambiente virtual do laboratório:

```powershell
.\.venv\Scripts\python.exe -m pytest .\ArmoredSpeech\tests -q
```

Os testes cobrem modos, idioma, status, ausência de decisão, WAV ausente, classe desconhecida e bloqueio de RVC para áudio misto. Não executar RVC nem publicar durante estes testes.
