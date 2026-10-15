# Plano de teste UVR5 / MDX-Net

## Fase 0 — segurança e ambiente

- Criar um ambiente virtual próprio; não instalar dependências no Python global nem no ambiente RVC.
- Confirmar FFmpeg e espaço livre em disco.
- Escolher um único clipe curto primeiro.
- Calcular SHA-256 antes e depois; o original deve permanecer idêntico.
- Guardar todas as saídas numa pasta de laboratório, nunca na pasta de produção.

## Fase 1 — benchmark CPU

Executar somente um clipe e registrar:
- modelo exato e versão do pacote;
- duração do vídeo/áudio;
- tempo de separação;
- memória usada, se disponível;
- tamanho dos arquivos produzidos;
- se o processo terminou sem erro.

Interromper o experimento se houver pressão excessiva de memória, congelamento do sistema ou falta de espaço.

## Fase 2 — validação auditiva

Para o clipe com narração PT-BR e música:
1. Ouvir o original.
2. Ouvir a faixa vocal isolada.
3. Ouvir a faixa instrumental/residual.
4. Conferir se consoantes, finais de palavra e frases inteiras foram preservados.
5. Conferir se a música realmente diminuiu na faixa vocal e se não há artefatos fortes.
6. Não enviar o resultado ao RVC nesta fase.

## Ordem de amostras

1. Um vídeo curto com narração PT-BR + música (ex.: ID 1046, se disponível localmente).
2. Música sem fala (ID 291): teste negativo para falso positivo de voz.
3. Fala estrangeira (ID 12008): não pode ser rotulada como PT-BR.
4. Sem áudio (ID 178482): deve permanecer NO_AUDIO.

## Critério de decisão

Não basta gerar um arquivo vocal. A separação só será candidata à integração se preservar inteligibilidade da fala e reduzir a música de forma consistente em mais de um clipe, sem uso de nuvem paga e com tempo/RAM aceitáveis no hardware alvo. Resultado incerto = reprovação para automação; manter o áudio bloqueado para RVC.
