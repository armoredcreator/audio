# Contrato de dados ArmoredSpeech → ArmoredStudio — v1 (laboratório)

## Objetivo e fronteira

ArmoredSpeech e ArmoredStudio continuam sendo ferramentas independentes. A integração é feita por um **payload JSON versionado**, não por importação de módulos internos nem por leitura do SQLite do Speech pelo Studio.

- **ArmoredSpeech** lê seu próprio registro e exporta uma decisão pronta para consumo.
- **Contrato v1** transporta identidade, caminhos de arquivos, classe de áudio, evidências disponíveis e restrições de segurança.
- **ArmoredStudio** deverá validar o payload e mapear `audio_class` para seus próprios modos internos. Não deve importar `ArmoredSpeech`, usar a tabela `armored_speech_results` ou depender do schema SQLite experimental.
- Esta PR ainda não implementa o consumidor dentro do repositório principal; portanto, **não é integração ponta a ponta**.

Implementação do produtor/contrato: `ArmoredSpeech/studio_bridge.py`.

## Campos do payload

| Campo | Significado |
|---|---|
| `contract_version` | Versão inteira do contrato; v1 = 1 |
| `source_id`, `item_id` | Identidade estável do item e da fonte |
| `original_path` | Caminho do original imutável |
| `audio_artifact_path` | Caminho do WAV aprovado pelo Speech |
| `audio_class` | Classe canônica do Speech, não um modo interno do Studio |
| `language`, `speech_detected`, `music_detected`, `confidence` | Evidência de classificação disponível |
| `rvc_eligible` | Elegibilidade conservadora para RVC; não substitui a validação do Studio |
| `requires_vocal_separation` | Indica que áudio misto requer separação vocal validada |
| `original_sha256_before`, `original_sha256_after` | Hashes quando persistidos; divergência bloqueia exportação |
| `classifier_version` | `null` enquanto o registro atual não persistir a versão |

Exemplo estrutural (valores ilustrativos):

```json
{
  "contract_version": 1,
  "source_id": "source-1",
  "item_id": "291",
  "original_path": "storage/Videos GRUPO_FONTE_1/291/291_finallinkoriginal.mp4",
  "audio_artifact_path": "storage/Videos GRUPO_FONTE_1/291/291_finallinkoriginal.wav",
  "audio_class": "PT_BR_NARRATION",
  "language": "pt",
  "speech_detected": true,
  "music_detected": false,
  "confidence": 0.99,
  "rvc_eligible": true,
  "requires_vocal_separation": false,
  "original_sha256_before": null,
  "original_sha256_after": null,
  "classifier_version": null
}
```

## Regras de segurança

- Só exporta registro com status `READY`; ausente, não pronto, schema incompatível ou classe desconhecida bloqueiam.
- O original e o WAV precisam existir no modo normal.
- Se os dois hashes do original existirem e forem diferentes, a exportação falha.
- RVC só é marcado elegível para `PT_BR_NARRATION`, idioma PT, fala detectada e flag persistida positiva.
- `PT_BR_NARRATION_WITH_MUSIC` continua inelegível no v1 e exige separação vocal validada. O filtro passa-alta não é prova de separação.
- `MUSIC_ONLY`, `NO_AUDIO`, `NOISE`, `NO_USABLE_NARRATION` e `FOREIGN_SPEECH` nunca ficam elegíveis para RVC pelo produtor v1.
- O payload é JSON-safe e não expõe a linha SQLite, nomes de colunas internas ou enums do Studio.

## Limitações antes de integração real

1. O schema experimental não persiste `classifier_version` nem um estado de validação de stem vocal; esses campos continuam nulos/bloqueados.
2. O payload contém caminhos locais, não transporta os bytes dos arquivos. O consumidor precisa validar caminhos, existência e identidade antes de usar.
3. O mecanismo de entrega (arquivo de handoff atômico ou IPC) ainda precisa ser decidido e testado; esta PR não escolhe implicitamente um.
4. O Studio consumidor ainda não foi implementado no repositório principal.
5. Não rodar RVC, renderização, publicação nem alterar originais durante os testes de contrato.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest .\ArmoredSpeech\tests\test_studio_bridge.py -q
```

Os testes cobrem payload neutro, serialização JSON, classe/modo de segurança, status não pronto, WAV ausente, classe desconhecida e divergência de hash. São testes de contrato do lado Speech, não uma prova de integração ponta a ponta.
