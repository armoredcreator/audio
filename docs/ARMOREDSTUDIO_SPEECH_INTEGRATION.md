# Integração isolada ArmoredSpeech → ArmoredStudio

**Estado: preparação/auditoria; integração ainda não certificada.** Este documento foi criado na branch `test/armoredstudio-speech-integration-20261015`. Ele não altera a branch `main`, o repositório operacional, `armoredcreator-test` nem `armoredcreator-testbase`.

## Objetivo imediato

Validar no laboratório o contrato entre o resultado de áudio do ArmoredSpeech e o processamento de vídeo do ArmoredStudio, sem executar publicação, sem tocar nos originais e sem reutilizar o banco/credenciais/storage de produção.

## Evidência observada no repositório

- O README da branch descreve o ArmoredSpeech como protótipo isolado e declara que a classificação automática ainda precisa de benchmark reproduzível.
- O contrato publicado pelo pacote ArmoredSpeech usa `AudioClass`, `SpeechDecision` e `SpeechStatus`; apenas narração confirmada em PT-BR é elegível para RVC.
- O adaptador canônico do Studio na branch `main` do repositório operacional é `ArmoredStudio/service.py`: `build(root)` instancia o serviço, e `process(item)` retorna `StudioResult` com caminho de trabalho opcional e caminho de saída. Essa evidência foi lida somente; nenhum arquivo do repositório operacional foi alterado.
- O contrato Speech atual diz que o WAV sidecar fica junto do MP4, com mesmo nome-base e extensão `.wav`, e deve ser preservado até o Studio confirmar que terminou de utilizá-lo.

## Contrato de handoff obrigatório

1. Identidade estável: `source_id + item_id`; não depender apenas do nome do arquivo.
2. Entrada de vídeo: caminho absoluto dentro do workspace temporário de teste, arquivo existente, não vazio e extensão/formato validáveis.
3. Original: calcular SHA-256 antes de começar e novamente depois; qualquer mudança bloqueia o teste.
4. Resultado Speech: decisão tipada, classe, idioma, confiança, estado e caminho WAV. Resultado ausente, inválido ou `UNKNOWN` falha fechado.
5. RVC: autorizado somente se `SpeechDecision.rvc_eligible` for verdadeiro. Música sem narração PT-BR, fala estrangeira, ausência de áudio, ruído ou decisão incerta não podem entrar em RVC.
6. WAV: precisa existir, ser não vazio, ter nome-base correspondente ao item e ser validável antes do handoff. WAV ausente/corrompido bloqueia a integração; não fabricar um resultado de sucesso.
7. Studio: receber a entrada pelo adaptador real `build(root)`/ `process(item)` e pelo modelo `Item` do próprio projeto. Não inventar campos nem alterar o contrato de produção antes de inspecionar os tipos reais.
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
- Colocar o Studio sob esta branch de laboratório, mantendo as dependências internas necessárias sem copiar credenciais ou bancos.
- Definir diretórios de teste separados para entrada, WAV, saída, logs e SQLite experimental.
- Bloquear por configuração qualquer publicação e qualquer conexão externa não necessária ao teste.
- Garantir que caminhos de saída não apontem para `storage/` operacional e que o original seja somente leitura.

### C. Testes automatizados mínimos
- Speech válido PT-BR + WAV válido → handoff permitido.
- `MUSIC_ONLY`, `FOREIGN_SPEECH`, `NO_AUDIO`, `NOISE`, `UNKNOWN` → RVC bloqueado.
- WAV ausente, vazio, nome-base incompatível ou ilegível → falha fechada.
- Studio lança exceção → resultado não marcado como sucesso e WAV preservado.
- Saída inexistente/vazia/fora do workspace → falha.
- Hash do original idêntico antes/depois.
- Nenhum acesso a banco, credenciais, diretórios ou destinos operacionais.
- Contrato e imports validados por testes sem renderização; renderização sintética só depois dos testes unitários.

### D. Critério para teste de vídeo real
Não executar ainda. Só liberar após: imports/dependências auditados, suíte automatizada verde, workspace isolado comprovado, integridade do original verificada e autorização explícita do usuário. Nenhuma publicação real faz parte desta validação.

## Resultado da auditoria da base oficial

O inventário completo da pasta oficial foi obtido e os bloqueios foram registrados em [`ARMOREDSTUDIO_BASE_AUDIT.md`](./ARMOREDSTUDIO_BASE_AUDIT.md). Os principais impedimentos confirmados são: Speech não é consumido pelo Studio (o Studio reclassifica o áudio), o resultado existente é apagado antes da nova renderização, os WAVs temporários são criados ao lado do vídeo de entrada e a integridade do original não é recalculada antes/depois. A ponte Speech atual também não valida suficientemente o artefato WAV.

A base foi auditada estaticamente; nenhum teste foi executado e nenhum vídeo foi processado nesta auditoria. Portanto, o Studio ainda não foi migrado nem a integração foi certificada. A implementação deve corrigir esses bloqueios na branch de laboratório e só então executar a suíte automatizada.

## Critério de conclusão

A integração só será considerada pronta quando houver um commit nesta branch, inventário dos arquivos portados, testes executados com resultados registrados, comprovação de hash do original preservado, e demonstração de que o teste não alcança DB/credenciais/storage/publicação operacional. Não fazer merge nem promoção automática.
