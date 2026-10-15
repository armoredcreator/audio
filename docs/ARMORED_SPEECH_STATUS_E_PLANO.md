# ArmoredSpeech — relatório de validação e plano de integração

**Data do registro:** 15/10/2026  
**Repositório:** [armoredcreator/audio](https://github.com/armoredcreator/audio)  
**Branch documentada:** `refactor/ArmoredSpeech-directory-20261015`  
**Commit de referência informado e conferido no ambiente local:** `4aea33d6cee917d9c74523fef1cf1c502229a620` — `Fix package path for real MP4 acceptance validation`

## 1. Objetivo e limites

Este repositório é o laboratório isolado do ArmoredSpeech. O objetivo desta etapa é validar a classificação de áudio e definir, com evidências, o contrato que futuramente será consumido pelo ArmoredStudio.

**Ainda não é uma integração aprovada para produção.** Não foi concluída a ponte persistente ArmoredSpeech → ArmoredStudio, não foi validado o contrato contra o banco SQLite real do ArmoredCreator e não foi autorizado portar estas alterações para o repositório de produção.

Limites de segurança:
- Manter o repositório de laboratório separado do ArmoredCreator de produção.
- Não alterar o repositório de referência congelado `armoredcreator-testbase` nem o repositório de prova `armoredcreator-test`.
- Não executar processamento real do Studio/RVC, nova separação de áudio ou publicação como parte da validação desta etapa.
- Tratar os cinco MP4s de referência como somente leitura; preservar os originais e conferir hashes antes/depois.
- Não versionar vídeos, saídas, bancos locais, credenciais, ambientes virtuais ou conteúdo de `storage/`.
- A branch de organização/integração continua em revisão; o PR #2 não deve ser mesclado antes da conclusão dos critérios pendentes.

## 2. Modos de áudio e comportamento esperado

| Modo | Significado operacional | Política de RVC |
|---|---|---|
| `SPEECH` | Narração/fala em português identificada sem música relevante | Pode seguir para RVC somente após as validações exigidas |
| `SPEECH_PLUS_MUSIC` | Narração em português junto com música | Exige separação/validação da voz antes de RVC |
| `MUSIC_ONLY` | Música sem narração elegível | Nunca chamar RVC; seguir a rota de áudio/mute e efeito já definida pelo Studio |
| `NO_AUDIO` | Nenhum áudio efetivo, mesmo que o contêiner tenha uma faixa de áudio | Nunca chamar RVC; aplicar a política de silêncio/efeito do Studio |
| `NOISE` | Ruído sem fala elegível | Nunca chamar RVC; seguir rota segura sem narração |
| `SPEECH_UNVERIFIED` | Evidência insuficiente, contraditória ou inconclusiva | Falhar de forma segura; não presumir fala nem chamar RVC |

A classificação automática precisa ser persistida como decisão explícita. O Studio não deve tentar deduzi-la a partir do nome do arquivo, da existência de WAV ou de heurísticas paralelas.

## 3. Teste de aceitação com os cinco MP4s reais

O comando executado no ambiente virtual local foi:

```powershell
Set-Location 'C:\Users\Administrador\Downloads\ArmoredAudioLab'
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_real_audio.py
```

**Resultado observado: PASS (5/5).** O relatório do teste indicou que os hashes dos MP4s originais permaneceram inalterados.

| Arquivo de referência | Resultado esperado | Resultado observado | Evidência resumida |
|---|---|---|---|
| `291_6VKD4FrQVJ.mp4` | `NOISE` | `NOISE` | fala 0,019; música 0,009; ruído 0,077; VAD 0,0 |
| `1046_20uhmSz3VE.mp4` | `SPEECH` | `SPEECH` | fala 0,894; música 0,004; VAD 0,993; idioma `pt`, confiança 0,994 |
| `12008_1AyNNNzh3.mp4` | `MUSIC_ONLY` | `MUSIC_ONLY` | fala 0,008; música 0,881; ruído 0,003; VAD 0,041 |
| `12025_7ptrS9Gsg2.mp4` | `SPEECH_PLUS_MUSIC` | `SPEECH_PLUS_MUSIC` | fala 0,136; música 0,622; VAD 0,978; idioma `pt`, confiança 0,997 |
| `178482_7AeDXQWFF2.mp4` | `NO_AUDIO` | `NO_AUDIO` | heurística espectral; sinal decodificado sem áudio efetivo |

Métodos reportados: `silero-vad+audioset-multilabel+whisper-language-v2` para os quatro primeiros casos e `spectral-heuristic-v1` para `NO_AUDIO`.

Observações:
- O arquivo de ruído foi classificado como `NOISE`, e não como música ou fala.
- O exemplo de fala isolada foi classificado como `SPEECH` com idioma português.
- O exemplo misto foi reconhecido como `SPEECH_PLUS_MUSIC`.
- O exemplo de música sem fala foi classificado como `MUSIC_ONLY`.
- O arquivo silencioso foi classificado como `NO_AUDIO`, mesmo contendo uma faixa AAC no contêiner.
- O teste confirma os resultados nesses cinco arquivos específicos; não prova, sozinho, desempenho geral em toda a coleção histórica.

## 4. Testes automatizados e CI

### Testes locais

Comando executado:

```powershell
.\.venv\Scripts\python.exe -m pytest .\ArmoredSpeech\tests -q
```

**Resultado observado: `18 passed in 0.67s`.**

A validação inicial usando o Python global falhou porque aquele ambiente não tinha `silero_vad` instalado. A execução com o Python do `.venv` do laboratório encontrou as dependências e passou. Portanto, esse primeiro erro foi de ambiente/dependência, não evidência de falha da classificação.

Avisos de Torch/exportação e filtros mel foram observados durante a execução, mas não impediram o teste de aceitação de passar. Devem ser monitorados, sem confundi-los com falhas enquanto os testes continuam verdes.

### GitHub Actions

Resultados registrados anteriormente para esta branch:
- [Validação com áudio real — execução 38799346708](https://github.com/armoredcreator/audio/actions/runs/38799346708): relatório `REAL_AUDIO_VALIDATION=PASS (5/5)`, hashes inalterados.
- [Testes isolados — execução 38799346754](https://github.com/armoredcreator/audio/actions/runs/38799346754).

Os links são referências aos resultados registrados; antes de declarar uma nova versão validada, conferir o status atual dos workflows e o SHA exato ao qual cada execução pertence.

## 5. Estado do código e do Git

- Branch de trabalho: `refactor/ArmoredSpeech-directory-20261015`.
- Commit de referência: `4aea33d6cee917d9c74523fef1cf1c502229a620`.
- O estado local informado foi `git status --short` → `?? storage/`.
- `storage/` é dado local e deve continuar intocado e fora do Git. Não o adicionar, apagar ou limpar como parte da documentação.
- [PR #2 — organização do diretório ArmoredSpeech](https://github.com/armoredcreator/audio/pull/2) permanece em revisão/draft e **não deve ser mesclado por causa apenas dos testes de classificação**.
- [PR #1 — experimento isolado de separação e contrato em lote](https://github.com/armoredcreator/audio/pull/1) registra o contexto inicial do experimento. O manifesto experimental não deve ser confundido com a classificação automática já testada posteriormente.

## 6. O que já foi validado e o que ainda não foi

### Validado nesta etapa

- Organização canônica da ferramenta sob `ArmoredSpeech/` na branch de trabalho.
- Classificação dos cinco arquivos de aceitação para os cinco resultados esperados.
- Cobertura de testes locais reportada: 18 testes aprovados.
- Integridade dos cinco MP4s conferida por hash antes/depois.
- Separação entre laboratório e produção preservada no escopo do trabalho.
- O fluxo de classificação tem uma categoria explícita `NOISE`; essa categoria não deve ser tratada como fala elegível para RVC.

### Ainda não validado

- Ponte real e persistente entre ArmoredSpeech e ArmoredStudio.
- Contrato de dados final, incluindo esquema/versão, chave do item, caminho do original, modo de áudio, confiança/evidências, estado, timestamps e tratamento de falhas.
- Persistência no schema SQLite real do ArmoredCreator.
- Consumo da decisão persistida pelo Studio sem reclassificar o arquivo.
- Caminho de separação de voz e validação do stem para `SPEECH_PLUS_MUSIC`.
- Comportamento ponta a ponta para os seis modos, inclusive ausência de decisão, versão incompatível, falha de separação e decisão `SPEECH_UNVERIFIED`.
- Auditoria completa de dependências e módulos supostamente órfãos do ArmoredCreator e do ArmoredStudio.
- Validação com uma amostra representativa maior e com os arquivos do fluxo histórico.
- Aprovação para portar/mesclar no repositório de produção.

## 7. Plano futuro — ordem obrigatória

### Fase 1 — Definir e implementar a ponte isolada

Trabalhar primeiro no laboratório `armoredcreator/audio`. Inspecionar os contratos e os arquivos reais de ArmoredSpeech e ArmoredStudio antes de editar. A ponte deve registrar e ler uma decisão persistida e versionada, sem inferir a classificação pelo nome do arquivo nem pela mera existência de WAV.

Contrato a definir e documentar:
- Identidade estável do item e referência ao MP4 original.
- Modo de áudio, idioma detectado, confiança/evidências disponíveis e versão do classificador.
- Estado da decisão e data/hora.
- Caminhos de artefatos derivados, se houver, sem substituir o original.
- Elegibilidade para RVC e pré-condições necessárias.
- Erros, tentativas, ausência de decisão e incompatibilidade de versão.
- Política idempotente de repetição, sem duplicar artefatos ou apagar originais.

### Fase 2 — Auditar ArmoredCreator e ArmoredStudio

Criar inventário de módulos, pontos de entrada, imports, chamadas dinâmicas, configurações, persistência, consumidores e testes. Para cada módulo suspeito de estar órfão, reunir evidência de que não há uso direto, indireto, dinâmico ou por configuração antes de recomendar remoção.

**Não remover módulos durante a descoberta só por parecerem não usados.** Registrar cada achado como confirmado, provável ou inconclusivo, com evidência e risco.

### Fase 3 — Testar a integração ponta a ponta no ambiente isolado

Critérios de aceitação mínimos:
1. Studio lê a decisão persistida produzida pelo contrato do Speech.
2. A classificação não é recalculada silenciosamente pelo Studio.
3. `SPEECH` só chega a RVC depois das validações previstas.
4. `SPEECH_PLUS_MUSIC` só chega a RVC depois de a separação de voz ser validada.
5. `MUSIC_ONLY`, `NO_AUDIO` e `NOISE` nunca chamam RVC.
6. `SPEECH_UNVERIFIED`, decisão ausente, falha de separação e versão incompatível falham de modo seguro e observável.
7. Repetir a execução não corrompe o estado, não duplica artefatos e não modifica os MP4s originais.
8. Testes de contrato cobrem sucesso, erro, retomada e idempotência.
9. Os cinco MP4s de referência continuam com os mesmos hashes.
10. Não ocorre publicação nem execução em produção durante esses testes.

### Fase 4 — Revisar evidências e preparar o porte

Revisar diff, testes, contrato, relatório de auditoria e lista justificada de remoções. Só depois de aprovação explícita e evidências verdes planejar o porte para o repositório real. O porte deve ser separado da validação do laboratório, com backup e proteção dos originais e do banco.

### Fase 5 — Validar no repositório real sob controle

Antes de qualquer execução real: conferir branch/commit, backup, banco e armazenamento; aplicar a integração aprovada; executar primeiro testes sem publicação; verificar persistência e decisões; e só então planejar uma ativação controlada. Nenhuma etapa deste documento autoriza, por si só, executar RVC, reprocessar lote histórico ou publicar.

## 8. Critério de conclusão

A integração só será considerada pronta quando houver evidência reproduzível de que o Studio consome a decisão persistida do Speech, aplica a política correta nos seis modos, falha de modo seguro, mantém os originais intactos, passa nos testes de contrato/ponta a ponta e possui uma auditoria documentada dos módulos. Os cinco casos reais aprovados e os 18 testes unitários são uma base útil, mas não substituem esses critérios.

---
**Resumo executivo:** até aqui, a classificação passou nos cinco MP4s de aceitação, os 18 testes locais passaram e os hashes dos originais permaneceram iguais. O próximo trabalho é a ponte persistente ArmoredSpeech → ArmoredStudio no laboratório, seguida da auditoria de módulos e da validação ponta a ponta. Produção permanece fora do escopo até a aprovação dessas etapas.
