# Integração isolada ArmoredSpeech → ArmoredStudio

**Estado: preparação/auditoria; integração ainda não certificada.** Este documento pertence à branch `test/armoredstudio-speech-integration-20261015`. Não altera `main`, o repositório operacional, `armoredcreator-test` nem `armoredcreator-testbase`.

## Ordem oficial do pipeline

**ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub.**

O escopo atual é somente validar Speech → Studio de ponta a ponta no laboratório. Não executar Hub, Telegram, Shopee ou publicação nesta fase.

## Objetivo imediato

Validar no laboratório o contrato entre o resultado de áudio do ArmoredSpeech e o processamento de vídeo do ArmoredStudio, usando o corpus local informado pelo usuário, sem executar publicação, sem tocar nos originais e sem reutilizar o banco/credenciais/storage de produção.

## Corpus de teste

Diretório local: `C:\Users\Administrador\Downloads\ArmoredAudioLab\videos`.

O corpus contém:
- vídeos de teste para ArmoredSpeech;
- casos visuais de ArmoredStudio com VEO 1 e VEO 2;
- casos Gemini;
- barras pretas de diferentes tamanhos.

Esses arquivos não são parte desta branch nem devem ser enviados ao Git. Antes do processamento, inventariar os nomes, durações, streams de áudio/vídeo, tamanhos e SHA-256; executar os testes em cópias dentro de um workspace isolado e verificar novamente os hashes dos originais depois.

## Recursos que serão transferidos separadamente

O usuário transferirá os assets oficiais do Studio e o ambiente/modelo/índice RVC. Até confirmar os caminhos, dependências e hashes/identidade desses recursos, não declarar renderização real de ponta a ponta aprovada.

## Contrato de handoff obrigatório

1. Identidade estável: `source_id + item_id`; não depender apenas do nome do arquivo.
2. Entrada de vídeo: caminho absoluto dentro do workspace temporário de teste, arquivo existente, não vazio e extensão/formato validáveis.
3. Original: calcular SHA-256 antes de começar e novamente depois; qualquer mudança bloqueia o teste.
4. Resultado Speech: decisão tipada, classe, idioma, confiança, estado e caminho WAV. Resultado ausente, inválido ou `UNKNOWN` falha fechado.
5. RVC: autorizado somente se `SpeechDecision.rvc_eligible` for verdadeiro. Música sem narração PT-BR, fala estrangeira, ausência de áudio, ruído ou decisão incerta não podem entrar em RVC.
6. WAV: precisa existir, ser não vazio, ter nome-base correspondente ao item e ser validável antes do handoff. WAV ausente/corrompido bloqueia a integração; não fabricar um resultado de sucesso.
7. Studio: receber a entrada pelo adaptador real `build(root)`/`process(item)` e pelo modelo `Item` do próprio projeto. Não inventar campos nem alterar o contrato de produção antes de inspecionar os tipos reais.
8. Saída: deve existir, ser não vazia e permanecer dentro do diretório de saída isolado. Caminho fora do workspace, sobrescrita do original ou saída vazia falha.
9. Retenção: não remover WAV nem intermediários antes de o Studio confirmar sucesso; falha no Studio mantém o WAV para recovery.
10. Escopo: testes usam arquivos sintéticos ou cópias descartáveis em diretório temporário. Nenhum teste chama Telegram, Shopee, Hub, publicação ou cleanup de produção.

## Plano de trabalho executável

### A. Auditoria antes de portar
- Inventariar o conteúdo completo de ArmoredStudio na branch de referência que contém as correções mais recentes.
- Identificar imports absolutos/relativos, dependências externas, FFmpeg, modelos, assets, variáveis de ambiente, paths globais, SQLite, logs e operações de escrita/cleanup.
- Confirmar os tipos `Item`, `StudioResult`, `Storage` e a API de `UnifiedStudio` no mesmo commit de referência.
- Comparar o comportamento de detectores de áudio, Veo/Gemini e barras pretas com o histórico congelado, sem editar as bases de referência.

### B. Isolamento
- Manter Studio e Speech sob esta branch de laboratório, sem copiar credenciais ou bancos de produção.
- Separar diretórios de entrada, WAV, saída, logs e SQLite experimental.
- Bloquear por configuração qualquer publicação e conexão externa não necessária ao teste.
- Garantir que caminhos de saída não apontem para storage operacional e que o original seja somente leitura.

### C. Testes automatizados mínimos
- Speech válido PT-BR + WAV válido → handoff permitido.
- `MUSIC_ONLY`, `FOREIGN_SPEECH`, `NO_AUDIO`, `NOISE`, `UNKNOWN` → RVC bloqueado.
- WAV ausente, vazio, nome-base incompatível ou ilegível → falha fechada.
- Studio lança exceção → resultado não marcado como sucesso e WAV preservado.
- Saída inexistente/vazia/fora do workspace → falha.
- Hash do original idêntico antes/depois.
- Nenhum acesso a banco, credenciais, diretórios ou destinos operacionais.
- Contrato e imports validados por testes sem renderização; renderização sintética só depois dos testes unitários.

### D. Validação com o corpus local
1. Gerar inventário e SHA-256 dos originais sem modificá-los.
2. Classificar os casos de Speech e conferir resultados por amostra.
3. Executar os casos visuais VEO 1, VEO 2, Gemini e barras pretas em cópias isoladas.
4. Combinar Speech → Studio e registrar áudio, detecção visual, saída e integridade por caso.
5. Repetir o cálculo de SHA-256 dos originais e comparar com o inventário inicial.

Não executar renderização real até as dependências/asset serem validadas, os testes automatizados passarem e o usuário autorizar o ensaio controlado. Nenhuma publicação real faz parte desta validação.

## Estado atual

O adapter e os testes sintéticos de handoff existem. A ligação do chamador/orquestrador real do laboratório, os assets binários oficiais e o runtime/modelo/índice RVC ainda precisam ser confirmados antes de declarar o teste real ponta a ponta concluído.