# ArmoredSpeech → ArmoredStudio — fluxo canônico do laboratório

**Repositório:** armoredcreator/audio  
**Branch:** design/speech-studio-audio-timeline-20261017  
**PR:** #5, draft e não mesclada.  
**Escopo:** laboratório isolado; sem publicação no Hub nem alteração no banco operacional.

## 1. Objetivo e separação real de responsabilidades

A regra é **ArmoredSpeech possui o áudio; ArmoredStudio possui a edição visual e a saída de vídeo**.

### ArmoredSpeech é dono de todo o trabalho de áudio
- Extrair/preparar o áudio e classificar fala, idioma, música, ruído e silêncio.
- Persistir/validar a decisão na base isolada do Speech.
- Decidir a elegibilidade de RVC e executar Melody somente para narração PT-BR limpa.
- Produzir a voz convertida ou declarar explicitamente a entrada de silêncio para rotas não RVC.
- Preparar o efeito do projeto, aplicando normalização/ganho uma única vez.
- Construir o grafo completo de áudio: corte temporal da voz, mixagem, fades/intro e concatenação.
- Emitir o contrato v3 com identidade, hashes, metadados, inputs permitidos e grafo de áudio.

### ArmoredStudio é dono da edição visual e do vídeo final
- Analisar imagem/vídeo; detectar barras, VEO/Gemini e banner.
- Criar/validar o plano visual e realizar o crop/framing.
- Validar estruturalmente o contrato v3, sem tomar decisões de áudio.
- Unir o grafo visual ao grafo de áudio entregue pelo Speech e executar **uma única chamada final do FFmpeg**.
- Validar a saída e comprovar que o original permanece intacto.

O preflight visual é somente leitura; não autoriza o Studio a extrair, classificar ou processar áudio. Todos os pacotes do lote precisam estar prontos e validados antes do primeiro render.

## 2. Simplificação: um crop e um render

- O finalizador visual calcula um único crop da fonte para a região segura e formato 9:16.
- Esse crop é aplicado uma vez antes de dividir os frames entre corpo e introdução.
- O FFmpeg final recebe vídeo, banner visual quando necessário e os inputs de áudio descritos por Speech.
- O grafo de áudio chega definido pelo Speech; Studio não inventa rota nem aplica ATRIM, AMIX, AFADE, loudnorm, volume, silêncio ou RVC por decisão própria.
- A renderização combina áudio e imagem no mesmo filter_complex, em uma chamada final.

Isso não elimina a extração/transcodificação de WAV nem a execução de RVC dentro do Speech. São passos de preparação de áudio e pertencem ao Speech. A simplificação elimina o segundo render final e crops repetidos da fonte.

## 3. Contrato v3 e rotas

O pacote v3 inclui identidade, decisão Speech, hashes do original/voz/efeito, rota, inputs permitidos, metadados técnicos e grafo de áudio com hash. Studio valida o contrato, os arquivos e os hashes antes do render.

- RVC_MELODY: apenas para PT_BR_NARRATION, idioma pt/pt-br, fala detectada, music_detected=false, elegibilidade explícita e Melody identificado.
- EFFECT_ONLY: Speech declara a entrada de silêncio e o efeito. Não roda conversão RVC.
- Música + fala, música isolada, ruído, ausência de áudio, fala estrangeira e narração não utilizável não podem forçar Melody.
- UNKNOWN, decisão incompleta, pacote inconsistente, hash incorreto ou caminho externo bloqueiam o lote.
- O Studio não recupera o original, não executa RVC nem inventa uma nova rota quando o áudio está inelegível.

## 4. Propriedade dos módulos e ativos

- ArmoredSpeech/audio_intelligence.py, model_processor.py, media.py: análise e preparação de áudio.
- ArmoredSpeech/rvc_engine.py: único ponto de execução da conversão RVC.
- ArmoredSpeech/audio_package.py: preparação e validação das faixas.
- ArmoredSpeech/timeline_mixer.py: política e grafo de filtros de áudio; não executa FFmpeg.
- ArmoredSpeech/studio_integration.py e studio_batch_runner.py: preparação Speech e handoff do lote.
- ArmoredStudio/speech_handoff.py: validação estrutural do contrato, sem classificador/conversor.
- ArmoredStudio/unified.py: análise visual e orquestração da renderização.
- ArmoredStudio/processing/visual_finalizer.py: crop único e render de vídeo + áudio numa chamada final FFmpeg.
- ArmoredStudio/assets/fetch_official_assets.py: somente banner visual.

Os módulos legados ArmoredStudio/analysis/audio_intelligence.py, ArmoredStudio/processing/rvc.py e ArmoredStudio/processing/finalizer.py não fazem parte do caminho ativo desta branch e não devem ser recriados como fallback.

O caminho canônico dos modelos/índices RVC é ArmoredSpeech/runtime/rvc/models/<voz>/; o efeito é ArmoredSpeech/assets/efeitosonoro.wav. O engine não faz fallback para Studio.

Após atualizar a branch local, execute na raiz do laboratório:

```powershell
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
```

O script copia e confere SHA-256 dos modelos/índices e do efeito a partir dos ativos locais antigos no Studio; não apaga a origem. Destinos com conteúdo divergente bloqueiam sem sobrescrita. Só remova os ativos antigos em Studio após o E2E v3 real passar. O banner continua sendo visual de Studio.

## 5. Estado da validação

### E2E antigo aprovado pelo usuário

Em 17/10/2026, a versão anterior produziu 2_finaldomeulinknovo.mp4 a partir do item 2 com Melody. O usuário assistiu e aprovou áudio/imagem; o hash SHA-256 do original foi preservado. Essa execução usava o contrato antigo e não prova a nova arquitetura v3.

### Fixture atual para o próximo E2E v3

Entrada: storage/Videos GRUPO_FONTE_teste/5/5_narração_pt_gemini1.mp4.

- AUDIO_CLASS=PT_BR_NARRATION
- LANGUAGE=pt-br
- CONFIDENCE=0.9828158873485948
- RVC_ELIGIBLE=true
- MUSIC_DETECTED=false
- TEST_ITEM_ID=5
- SHA-256 do MP4 de referência: c489fcaae803f36407c82dde3b93ff68077001ef5607909081a2c6555bee15a1

Manifesto isolado: storage/validation/speech-v2-real-smoke-20261017T151136454023Z-62a58b/manifest.json

SQLite isolado: storage/database/armored_speech_lab_20261017T151136454023Z_62a58b.sqlite3

A preparação foi executada antes de sincronizar todas as mudanças mais recentes. **Não execute o runner com a cópia local antiga**: atualize a branch, migre/verifique os ativos para Speech e só então use o manifesto/SQLite acima no runner atualizado. O runner deve bloquear, sem sobrescrever, se houver destino canônico preexistente.

Após renderizar, confirmar relatório SUCCESS, MODE=REAL_STUDIO, contrato v3, rota, saída canônica, áudio/imagem/crop/intro, sincronização/duração e hash do original antes/depois.

## 6. Próxima ordem de trabalho

1. Atualizar localmente a branch por fast-forward seguro.
2. Copiar/verificar os ativos RVC e efeito para ArmoredSpeech.
3. Executar o E2E v3 com o fixture do item 5 e assistir ao MP4 final.
4. Validar rotas não-RVC usando itens 1, 3, 4 e 6, sem reutilizar artefatos canônicos de outros itens.
5. Auditar referências legadas e confirmar CI verde.
6. Só depois remover os ativos locais legados de RVC/efeito em Studio; nunca apagá-los antes do E2E v3.
7. Limpar pacotes/relatórios/WAVs temporários de maneira seletiva, preservando corpus, ativos em Speech e resultados aprovados até finalizar a validação.

## 7. Segurança

- Nenhuma execução publica no Hub.
- Preparador/runner usa SQLite isolado e não abre o banco operacional.
- Originais do corpus, modelos e índices não podem ser alterados durante o teste.
- A PR permanece draft/não mesclada até o E2E v3 real do Windows e as rotas seguras passarem.