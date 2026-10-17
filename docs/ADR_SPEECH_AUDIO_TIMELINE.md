# Decisão técnica — áudio Speech → Studio e alinhamento temporal

**Status:** decisão de arquitetura para guiar a implementação isolada. A extração estrutural do motor RVC para `armored_core.audio.rvc` já foi feita nesta branch; a chamada por Speech e o pacote v2 continuam pendentes. Não é validação do runtime RVC v2.  
**Branch:** `design/speech-studio-audio-timeline-20261017`  
**Contexto:** a fase ArmoredSpeech deve concluir o lote aprovado antes da fase de processamento do ArmoredStudio. O Studio descobre `video_start` e `audio_start` com o plano visual.

## 1. Decisão principal

**Não enviar um único WAV já mixado como saída v2 de Speech.** O plano visual que pode exigir o corte temporal é conhecido por Studio, e Speech deve terminar o lote antes do início do processamento Studio. Logo, no fluxo atual Speech não dispõe de `audio_start` quando prepara o áudio. Um único WAV pré-mixado faria com que um corte do começo da voz também cortasse o começo do efeito — comportamento diferente do atual.

Em vez disso, Speech passa a produzir um **pacote de faixas de áudio preparadas**, com rota e metadados explícitos. A etapa final de renderização do Studio fica limitada a aplicar o plano temporal e combinar as faixas uma única vez no mesmo grafo FFmpeg que compõe o vídeo. Studio não reclassifica, não chama RVC, não escolhe a rota e não recalcula normalização/ganhos de áudio.

Este limite é importante: com a ordem rígida `ArmoredSpeech completo → ArmoredStudio`, o Studio precisa aplicar o alinhamento temporal descoberto na análise visual. A composição final sincronizada ocorre no renderizador; a preparação, a política de áudio e os ganhos das faixas pertencem ao Speech. Transferir também a execução da mixagem final para Speech exigiria conhecer antes o plano visual, o que mudaria a ordem de fases e não será feito nesta etapa.

## 2. Comportamento temporal que precisa ser preservado

O código atual em `ArmoredStudio/processing/finalizer.py` faz o seguinte na faixa principal:

- aplica `plan.audio_start` **somente à faixa de voz** antes de misturá-la;
- inicia a faixa do efeito desde o começo da parte principal, sem cortar o efeito por `audio_start`;
- usa a mesma política de ganho/normalização de efeito também para a introdução, com fades próprios;
- gera uma única composição de áudio por renderização, tanto com introdução quanto sem introdução.

O novo contrato não deve transformar `audio_start` em um corte de uma faixa pré-mixada. A faixa de voz deve manter a linha temporal de origem e ser aparada só pelo Studio ao aplicar o plano; o efeito preparado deve continuar começando no início da parte principal. Fade/trim necessários à introdução continuam sendo operações de alinhamento da renderização, não uma nova decisão de áudio.

## 3. Fronteira de responsabilidades

### ArmoredSpeech prepara e decide

- classifica o áudio e escolhe a rota de processamento;
- chama Melody no máximo uma vez, apenas em narração PT-BR explicitamente elegível;
- prepara a faixa de voz convertida ou uma faixa silenciosa determinística para rotas sem RVC;
- prepara o efeito sonoro do projeto com normalização e ganho acordados, sem depender do plano visual;
- valida e descreve as faixas: hash, duração, sample rate, canais, formato e estado;
- entrega um payload v2 que identifique cada artefato e a política de áudio usada.

### ArmoredStudio sincroniza e renderiza

- calcula e valida o plano visual, incluindo `video_start` e `audio_start`;
- rejeita o payload v2 se houver identidade, caminho, hash ou metadados inválidos;
- não executa `AudioIntelligence` para sobrepor a decisão já aprovada pelo Speech;
- não invoca `converter_voz`, não gera a faixa de voz silenciosa e não aplica novamente loudness/ganhos;
- aplica o corte temporal somente à faixa de voz, preserva o efeito desde o ponto temporal atual, prepara intro/fades e combina as faixas uma única vez durante o render;
- mantém intactos os detectores visuais, o crop, banner, composição do vídeo e validação do arquivo final.

## 4. Estrutura proposta do payload v2

Os nomes finais podem ser ajustados na implementação, mas a semântica não deve mudar:

- `contract_version: 2`;
- `source_id`, `item_id`, `original_path`, `original_sha256_before`, `original_sha256_after`;
- `audio_class`, `language`, `confidence`, `speech_detected`, `music_detected`;
- `route`: valor fechado, por exemplo `RVC_MELODY` ou `EFFECT_ONLY`;
- `voice_track`: caminho da faixa vocal convertida ou de silêncio, hash, formato, sample rate, canais e duração;
- `effect_track`: caminho da faixa de efeito preparada, hash, formato, sample rate, canais e duração;
- `effect_gain_policy`/versão da preparação: metadado auditável, sem pedir ao Studio para aplicar o mesmo ganho outra vez;
- `rvc_model` e versão/configuração quando aplicável;
- `status: READY` e um identificador de execução para evitar consumir um resultado de outro item;
- política de retenção: manter ambas as faixas até o Studio confirmar sucesso.

O v2 deve rejeitar caminhos fora do workspace, arquivos vazios/ilegíveis, hashes divergentes, rota não suportada, metadados incompatíveis e áudio fora da duração/faixa esperada. Não é permitido inferir elegibilidade RVC pela existência de um arquivo.

## 5. Regras de rota

- `PT_BR_NARRATION` validada e sem música relevante: pode produzir `RVC_MELODY`.
- `PT_BR_NARRATION_WITH_MUSIC`: continua inelegível no v2 inicial até aprovação verificável do stem vocal; não reabrir esta rota só porque há arquivo de separação.
- Música, silêncio, ruído, fala estrangeira, narração não utilizável, `UNKNOWN`, erro ou baixa confiança: não executam Melody. Produzem a faixa segura definida pela política do projeto (normalmente silêncio) e a faixa de efeito.
- Falha na conversão RVC não deve silenciosamente cair para voz original. Bloquear o item e deixar um erro explícito.
- Todas as faixas mantêm formato/duração canônicos verificados; o original MP4 é somente leitura.

## 6. Ordem de implementação

1. Criar testes de regressão do comportamento temporal atual no finalizer, especialmente `audio_start=0` e `audio_start>0`, com/sem intro.
2. **Feito parcialmente:** extrair a implementação de baixo nível para `armored_core.audio.rvc` e manter `ArmoredStudio/processing/rvc.py` como facade de compatibilidade. **Pendente:** implementar o produtor de pacote v2 em Speech e a invocação RVC no lote Speech. Não reescrever o backend nem alterar arquivos de modelo/índice.
3. Ampliar o validador Studio para v2 mantendo v1 durante a transição.
4. Tornar v1 e v2 mutuamente exclusivos por execução/item; um item v2 jamais entra no caminho de classificação/RVC/mixagem antiga.
5. Alterar o finalizer para receber faixas preparadas e só efetuar corte/alinhamento/composição temporal, mantendo o efeito no início conforme a semântica atual.
6. Adicionar testes que provem que, no modo v2, Studio não chama classificador de áudio, conversor RVC nem filtros duplicados de normalização/volume.
7. Rodar a suíte automatizada e, somente com artefatos reais da cópia isolada, repetir E2E e comparação auditiva/visual; verificar SHA-256 do original antes/depois.
8. Aposentar v1 somente após os testes e a aprovação manual do E2E.

## 7. Critérios de aceitação

- A curva/ordem temporal de voz e efeito permanece equivalente ao caminho anterior para `audio_start=0` e `audio_start>0`.
- RVC ocorre no máximo uma vez e apenas para a rota aprovada.
- Efeito/volume/normalização são preparados uma vez; o Studio não os reprocessa.
- Nenhum item começa em Studio antes de o lote Speech completo e aprovado passar no preflight.
- Nenhum original, asset, modelo ou índice é alterado; saídas são novas e isoladas.
- O E2E real com o mesmo clipe de referência continua correto e é aprovado por escuta humana.
- Nenhuma alteração é portada ao `armoredcreator/armoredcreator` nesta etapa.

## 8. Limitações e evidência

Esta decisão resulta da leitura estática de `unified.py`, `speech_handoff.py`, `processing/rvc.py`, `processing/finalizer.py`, `studio_bridge.py`, `media.py` e dos testes de contrato/lote. Após a extração estrutural do motor, o CI da branch passou em compilação e 73 testes automatizados; não houve renderização nem E2E real com o novo caminho Speech-owned nesta etapa. O E2E histórico aprovado prova apenas a rota anterior testada, não a arquitetura v2.
