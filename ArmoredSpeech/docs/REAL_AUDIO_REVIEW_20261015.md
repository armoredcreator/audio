# ArmoredSpeech — diagnóstico preliminar do corpus local (15/10/2026)

Este relatório registra a saída de \`validate_full_corpus.py\`; não substitui confirmação auditiva humana. Os cinco casos de referência passaram segundo os rótulos atualmente codificados no teste; isso não prova validação independente dos rótulos.

| # | Arquivo | Classificação automática | Evidência reportada | Rótulo confirmado / correção |
|---:|---|---|---|---|
| 1 | \`1046_20uhmSz3VE.mp4\` | SPEECH | fala 0.894; música 0.004; VAD 0.993; idioma pt (0.994) | **Fala em português — confirmado** |
| 2 | \`12008_1AyNNNzh3.mp4\` | MUSIC_ONLY | fala 0.008; música 0.881; VAD 0.041 | **Só música — confirmado** |
| 3 | \`12025_7ptrS9Gsg2.mp4\` | SPEECH_PLUS_MUSIC | fala 0.136; música 0.622; VAD 0.978; idioma pt (0.997) | **Fala + música — confirmado** |
| 4 | \`13.mp4\` | SPEECH_PLUS_MUSIC | fala 0.508; música 0.427; VAD 0.835; idioma pt (0.997) | **Fala em português**; início contém banner a remover e efeito sonoro antes da narração |
| 5 | \`178482_7AeDXQWFF2.mp4\` | NO_AUDIO | heurística espectral; sem probabilidades de fala/música | **Sem áudio — confirmado** |
| 6 | \`20260827_095121.mp4\` | SPEECH_PLUS_MUSIC | fala 0.861; música 0.518; VAD 0.935; idioma pt (0.998) | **Fala em português — confirmado** |
| 7 | \`291_6VKD4FrQVJ.mp4\` | NOISE | fala 0.019; música 0.009; ruído 0.077; VAD 0.000 | **Ruído / som ambiente — confirmado** |
| 8 | \`55214.mp4\` | SPEECH | fala 0.871; música 0.007; VAD 0.983; idioma pt (0.991) | **Fala em português — confirmado** |
| 9 | \`73.mp4\` | SPEECH_PLUS_MUSIC | fala 0.464; música 0.425; VAD 0.824; idioma pt (0.991) | **Fala em português**; início contém banner a remover e efeito sonoro antes da narração |
| 10 | \`Flow_delpmaspu_ (3).mp4\` | NOISE | fala 0.005; música 0.023; ruído 0.069; VAD 0.000 | **Ruído / som ambiente — confirmado** |
| 11 | \`Mulher_falando_sobre_produto_202607141616.mp4\` | SPEECH | fala 0.909; música 0.004; VAD 0.953; idioma pt (0.999) | **Fala em português — confirmado** |
| 12 | \`Sneaker_demonstration_on_sofa_delpmaspu_.mp4\` | NOISE | fala 0.011; música 0.019; ruído 0.116; VAD 0.000 | **Ruído / som ambiente — confirmado** |
| 13 | \`teste.mp4\` | SPEECH_PLUS_MUSIC | fala 0.444; música 0.373; VAD 0.832; idioma pt (0.971) | **Fala em português**; início contém banner a remover e efeito sonoro antes da narração |
| 14 | \`video4980984971737958607.mp4\` | SPEECH | fala 0.748; música 0.105; VAD 0.992; idioma pt (0.999) | **Fala em português — confirmado** |
| 15 | \`video4980984971737958608.mp4\` | SPEECH | fala 0.820; música 0.165; VAD 0.938; idioma pt (0.996) | **Fala em português — confirmado** |

## Estado da evidência

- Corpus analisado: 15/15; hashes dos originais inalterados na execução reportada.
- Cinco casos com rótulo de referência no script passaram segundo os rótulos codificados.
- Os 15 arquivos receberam confirmação auditiva humana nesta revisão; consulte a coluna “Rótulo confirmado / correção”.
- Em `13.mp4`, `73.mp4` e `teste.mp4`, o rótulo humano é fala em português. O efeito sonoro inicial está associado ao banner que o ArmoredStudio deve remover; não deve transformar a classificação do conteúdo principal em fala + música.
- Em `20260827_095121.mp4`, embora o classificador tenha retornado SPEECH_PLUS_MUSIC, a confirmação humana é fala em português.
- A classificação de ruído/som ambiente foi confirmada para `291_6VKD4FrQVJ.mp4`, `Flow_delpmaspu_ (3).mp4` e `Sneaker_demonstration_on_sofa_delpmaspu_.mp4`.
- O idioma inferido não substitui a escuta.
- Avisos de TorchScript e filtros mel não foram falhas fatais.
- Este teste cobre classificação de áudio; não testa separação vocal/instrumental, RVC, legendas, detecção visual de marca d'água ou handoff do Studio.

## Como confirmar

Responda com os números e correções, por exemplo: \`1 fala PT; 2 só música; 3 fala + música; 4 só fala...\`. Ouça cada arquivo e informe o rótulo real; não precisa reenviar os vídeos.
