# Auditoria estática ponta a ponta — base ArmoredStudio

**Data:** 2026-10-15  
**Base auditada:** `armoredcreator/armoredcreator`, branch `main`, árvore Git `c9eeefe2449b5de26b3534c94090032313c9c8e8`.  
**Destino do relatório:** `armoredcreator/audio`, branch `test/armoredstudio-speech-integration-20261015`.  
**Tipo de auditoria:** leitura estática do código e dos contratos remotos. Nenhum vídeo foi processado e nenhum teste foi executado nesta sessão.

## Resultado executivo

A base do ArmoredStudio está localizada e seus módulos foram inventariados. A arquitetura tem uma fronteira pública relativamente clara em `ArmoredStudio/service.py` (`build(root)` → `process(item)`), uma etapa de análise visual/áudio em `unified.py`, planejamento/validação de crop, execução FFmpeg, finalização e conversão RVC.

**Status para integrar Speech → Studio: BLOQUEADO até corrigir os pontos abaixo.** Não basta copiar a pasta: o Studio atual não consome o contrato versionado do ArmoredSpeech e executa uma segunda classificação de áudio própria. Também há risco de apagar uma saída anterior e de gravar intermediários de áudio ao lado do vídeo de entrada.

## Inventário da base oficial

### Entrada e orquestração do Studio
- `ArmoredStudio/service.py` — fronteira pública, valida disponibilidade do FFmpeg e constrói `StudioResult`.
- `ArmoredStudio/unified.py` — análise obrigatória, decisão RVC/silêncio, execução de finalização e limpeza de WAVs temporários.

### Análise
- `analysis/audio_intelligence.py` — extração/inspeção de áudio e classificação de modo/idioma; pode inicializar modelos de reconhecimento de fala.
- `analysis/video.py` — metadados do vídeo.
- `analysis/blackbar.py` — detecção de barras pretas.
- `analysis/banner.py` e `analysis/banner_analyzer.py` — detecção/corte de banner.
- `analysis/veo_detector.py` — detecção visual VEO por templates OpenCV embutidos no código.
- `analysis/gemini_detector.py` — detecção visual baseada em processamento de imagem/OpenCV no módulo auditado.
- `analysis/export_planner.py` — combina evidências num plano de crop.
- `analysis/export_plan_validator.py` — valida campos, ações e limites básicos do crop.
- `analysis/export_executor.py` — prepara/executa FFmpeg e valida o arquivo produzido.
- `analysis/logger.py` — logging.

### Processamento e assets
- `processing/finalizer.py` — composição final, normalização para formato vertical, áudio, banner e FFmpeg.
- `processing/rvc.py` — execução da conversão de voz num ambiente Python/RVC separado.
- `processing/tool_paths.py` — resolução de executáveis externos.
- `assets/banner.png` e `assets/efeitosonoro.wav` — recursos versionados que devem ser copiados sem alteração de bytes; não substituir por arquivos arbitrários.

## Achados — ordem de prioridade

### P1 — O Studio não consome o contrato ArmoredSpeech

**Evidência:** `ArmoredStudio/service.py` recebe apenas `Item`. `ArmoredStudio/unified.py` instancia `AnalysisEngine` e executa `AudioIntelligence.analyze(source)` de novo. A ponte existente em `ArmoredSpeech/studio_bridge.py` exporta um `SpeechAudioDirective`, mas não é chamada por `ArmoredStudio` e não existe argumento/adapter no serviço que receba essa diretiva.

**Impacto:** Speech e Studio não estão integrados; a decisão de áudio pode ser calculada duas vezes e divergir. O WAV exportado pelo Speech não é usado pelo Studio como entrada de áudio. A ponte é atualmente um exportador de contrato, não um handoff funcional.

**Correção exigida:** introduzir um adapter de integração versionado no laboratório, com identidade `source_id + item_id`; validar versão, classe, idioma, elegibilidade RVC, caminho e integridade do original e artefato de áudio; encaminhar a decisão explicitamente para o Studio. Preservar compatibilidade do caminho antigo durante o teste, sem alterar produção.

### P1 — A saída anterior é apagada antes de o processamento ser validado

**Evidência:** `UnifiedStudio.process` resolve `output = storage.result(...)` e executa `output.unlink()` se o arquivo já existir, antes de concluir a análise, verificar assets e renderizar a nova saída.

**Impacto:** uma reexecução pode perder um resultado válido se a análise, RVC ou FFmpeg falhar depois da exclusão.

**Correção exigida:** gerar a saída num arquivo temporário exclusivo no workspace isolado, validar existência/tamanho/metadata e só então substituir atomicamente o destino. Em falha, manter a saída anterior e os dados necessários para recuperação.

### P1 — WAVs intermediários são gravados ao lado do vídeo de entrada

**Evidência:** `unified.py` cria `audio_original` e `audio_rvc` com `source.with_name(...)`; o `finally` remove esses caminhos.

**Impacto:** se a entrada escolhida for o original imutável, o Studio ainda cria/remove arquivos na pasta dos originais. Isso mistura temporários com o contrato de armazenamento e dificulta provar isolamento/recovery.

**Correção exigida:** criar uma área temporária por item dentro do workspace de teste, garantir que os dois WAVs estejam dentro dela e limpar apenas artefatos que o próprio job criou. O original deve ser somente leitura.

### P1 — Integridade do original não é verificada pelo Studio

**Evidência:** o serviço confirma que `original_path` existe, mas o fluxo de processamento não calcula SHA-256 antes/depois. A ponte Speech compara apenas os dois hashes persistidos, quando presentes; não recalcula o hash do arquivo atual.

**Impacto:** hashes persistidos iguais não provam, por si só, que o arquivo atual permanece intacto.

**Correção exigida:** calcular hash do original imediatamente antes e depois do teste e comparar com o hash persistido/esperado. Qualquer diferença bloqueia o resultado e deixa o item em falha recuperável.

### P1 — O contrato da ponte não valida suficientemente o WAV

**Evidência:** `export_audio_directive` verifica a existência do arquivo de áudio quando `require_files=True`, mas não valida que seja WAV legível/não vazio, que o nome-base corresponda ao item, que esteja dentro do workspace permitido ou que o conteúdo corresponda ao artefato esperado. `UNKNOWN` é rejeitado como classe não suportada, o que é o comportamento seguro.

**Impacto:** um caminho existente mas inválido ou fora do workspace pode ser aceito pelo produtor da diretiva.

**Correção exigida:** validação fail-closed de tamanho, cabeçalho/decodificação WAV, identidade do item, diretório permitido e hash; nenhuma classe desconhecida pode habilitar RVC.

### P1 — O cache de modelos de áudio depende de caminho relativo ao diretório corrente

**Evidência:** `AudioIntelligence` usa por padrão um caminho relativo como `storage/models/audio` para cache de modelos, enquanto o serviço recebe `root` explicitamente. O caminho relativo pode ser resolvido a partir do diretório de execução, que não é necessariamente o root passado ao Studio.

**Impacto:** iniciar o laboratório a partir de outro checkout pode escrever/ler cache no diretório errado.

**Correção exigida:** definir explicitamente um cache exclusivo do laboratório e/ou derivar o caminho do root de forma inequívoca. Não importar/copiar o `project.env` operacional para o teste.

### P2 — Dependências e recursos externos precisam ser declarados para o ambiente de laboratório

**Evidência:** os módulos dependem de OpenCV, NumPy, FFmpeg, modelos de reconhecimento de fala e, para renderização de voz, um ambiente RVC separado com modelo/índice. `rvc.py` resolve `ARMORED_RVC_ROOT` ou usa `ArmoredStudio/runtime/rvc`.

**Impacto:** importar os módulos pode funcionar enquanto a execução real falha por falta de executável, modelo, asset ou ambiente Python compatível.

**Correção exigida:** inventário explícito de dependências e preflight sem rede/publicação; primeiro executar testes unitários com mocks/arquivos sintéticos, depois renderização sintética isolada. A validação de áudio real só ocorre após autorização.

### P2 — Validação do plano de crop é estrutural, não prova qualidade visual

**Evidência:** `export_plan_validator.py` valida campos, ações permitidas e limites do crop. Isso não demonstra que os detectores identificam corretamente todas as marcas ou que o crop preserva conteúdo útil em vídeos reais.

**Impacto:** um plano formalmente válido pode ainda remover área visual importante ou deixar watermark.

**Correção exigida:** testes determinísticos para limites e casos de detecção; depois benchmark visual controlado, com resultados e falsos positivos/negativos registrados. Não declarar o detector aprovado apenas porque o plano passa na validação.

### P2 — Falta prova de integração automatizada específica Speech → Studio

**Evidência:** foram encontrados testes de áudio, RVC e detectores no projeto principal e testes do contrato/ponte no laboratório, mas esta auditoria estática não encontrou execução de uma suíte que prove o handoff completo, integridade do original, isolamento de paths e preservação da saída em falha.

**Correção exigida:** criar testes específicos para: narração PT-BR válida; música/sem áudio/ruído/fala estrangeira/UNKNOWN; WAV ausente ou inválido; erro no Studio; saída vazia; caminho externo ao workspace; hash do original; preservação de resultado anterior; nenhuma publicação/rede/DB operacional.

## Fluxo de integração que deve ser validado

1. Ler uma decisão Speech `READY` pelo contrato versionado, identificada por `source_id + item_id`.
2. Validar versão, status, classe, idioma, confiança, elegibilidade RVC, WAV e hash do original.
3. Preparar workspace temporário isolado; original somente leitura.
4. Passar a diretiva validada para um adapter de Studio; não reclassificar silenciosamente a mesma entrada para tomar uma decisão conflitante.
5. Renderizar para um destino temporário; validar o artefato.
6. Recalcular SHA-256 do original e garantir igualdade.
7. Promover o resultado dentro do workspace de laboratório somente após todas as verificações.
8. Em falha, preservar o resultado anterior e os artefatos necessários à recuperação; nunca publicar.

## Restrições do teste

- Não acessar Telegram, Shopee, Hub, publicação ou limpeza operacional.
- Não usar banco, sessão, tokens, `project.env` ou cache de produção.
- Não executar vídeos reais até que a suíte sintética passe e haja autorização explícita.
- Os MP4s já presentes na raiz do repositório de laboratório não foram usados nem inspecionados como mídia; sua origem/adequação como cópias de teste precisa ser confirmada antes de qualquer execução.
- Não alterar `armoredcreator/armoredcreator`, `armoredcreator-test` ou `armoredcreator-testbase`.

## Conclusão

A base oficial foi inventariada e a revisão estática identificou bloqueios concretos de integração e segurança operacional. O próximo trabalho não é simplesmente copiar arquivos: é portar a implementação e os assets para a branch de laboratório, corrigir o handoff Speech → Studio e o isolamento de arquivos/cache, e provar o contrato com testes automatizados. **Até esses critérios serem satisfeitos, a integração não está pronta para renderização real nem para promoção.**
