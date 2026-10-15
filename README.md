# ArmoredCreator Audio Lab

Laboratório isolado para avaliar detecção de fala e separação de narração em português com música de fundo.

> Este repositório não integra nem altera o ArmoredCreator de produção. Os originais são somente leitura; todos os resultados devem ser gravados em uma pasta de experimento separada.

## Objetivo

Avaliar se UVR5/MDX-Net consegue produzir uma faixa de narração em português suficientemente limpa para o pipeline RVC, mesmo quando o vídeo original contém música de fundo.

**Não presumir que o modelo funciona para narração falada.** Muitos modelos vocais foram treinados principalmente para separar canto. A aprovação depende de ouvir e medir exemplos reais, inclusive artefatos e palavras perdidas.

## Ambiente de teste

- Windows e Python em ambiente virtual dedicado.
- Começar com inferência em CPU, sem exigir GPU.
- Usar cópias de poucos vídeos de teste, nunca a pasta de originais da produção.
- Registrar duração, tempo de processamento, pico aproximado de RAM, modelo/versão e caminhos de saída.
- Não executar renderização, RVC ou publicação durante esta fase.

## Critérios de aprovação

1. Os arquivos originais permanecem byte a byte intactos.
2. A saída vocal preserva as palavras e a inteligibilidade da narração PT-BR.
3. A música residual é suficientemente baixa para o objetivo definido.
4. Não há cortes importantes, voz robótica ou artefatos que piorem o áudio.
5. Tempo de execução e memória são aceitáveis para o notebook alvo.
6. Falhas, modelo ausente ou resultados ambíguos não liberam o áudio automaticamente para RVC.

## Plano

1. Registrar hashes e metadados dos clipes de referência.
2. Medir primeiro a detecção de fala e música sem separar.
3. Rodar um modelo MDX-Net em CPU sobre cópias isoladas.
4. Comparar original, faixa vocal e instrumental por escuta e métricas.
5. Documentar os limites e decidir se a integração é segura.

Só depois de resultados repetíveis e aprovação explícita o código poderá ser portado para o ArmoredCreator.

## Estrutura planejada

- `samples/README.md`: como preparar amostras locais sem commitar vídeos.
- `experiments/`: relatórios de testes, sem mídia original.
- `scripts/`: ferramentas de benchmark e separação.
- `docs/DECISION.md`: decisão de aprovação/reprovação e limites conhecidos.
