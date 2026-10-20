# Runner Speech → Studio — operação e evidências

A especificação canônica do runner, contrato v5, ownership, preflight, variantes temporais e comandos está no [Manual canônico do ArmoredAudioLab](../../docs/ARMORED_AUDIO_LAB_OPERATIONS.md). Este documento registra o estado específico do runner e não substitui esse manual.

## Garantias obrigatórias do runner

1. Aceita somente manifesto explícito, aprovado, completo e consistente com o número de itens.
2. Confere identidade, estado, caminhos dentro da raiz, existência dos originais e SHA-256 antes de iniciar.
3. Lê a base SQLite experimental explicitamente fornecida; não descobre nem abre o banco operacional.
4. Speech constrói e valida o pacote de áudio de todos os itens e as variantes NO_BANNER e BANNER_PRESENT antes de qualquer análise do Studio.
5. Só após o preflight completo, Studio analisa o vídeo e escolhe uma das variantes temporais já criadas por Speech.
6. O resultado é gravado num destino novo. Destinos existentes, pacote inválido, hash divergente ou falha de integridade bloqueiam o lote.
7. O relatório registra rota e timeline separadamente. BANNER_PRESENT não implica RVC; NO_BANNER não implica EFFECT_ONLY.

## E2E real confirmado

### Corpus consolidado de 17 vídeos

- Relatório: storage/validation/post_consolidation_corpus17_20261019T234723122Z/studio-report.json.
- Resultado: SUCCESS, 17 itens processados, contrato v5, originais intactos.
- Pós-validação de todos os 17 arquivos: H.264 1080×1920, AAC 44.100 Hz estéreo, decode estrito aprovado e SHA-256 original preservado.
- Rotas: 9 RVC_MELODY (002, 004, 005, 008, 010, 011, 012, 013, 014) e 8 EFFECT_ONLY (001, 003, 006, 007, 009, 015, 016, 017).
- Os 17 itens usaram NO_BANNER; esse corpus não contém banner.

### Banner real

- Entrada: storage/original_018.mp4.
- Análise visual: banner_detectado=true, confiança retornada 90, mudança inicial 0.1 e estabilidade 0.0; hash da entrada preservado.
- Relatório do Studio: storage/validation/corpus1_banner_present_20261020T004009599Z/studio-report.json.
- Resultado: BANNER_PRESENT + RVC_MELODY, mídia H.264 1080×1920/AAC estéreo 44.100 Hz, decodificação integral OK, cópia original íntegra; BANNER_E2E=PASS.
- O usuário assistiu à saída final e aprovou o resultado visual.

Os diretórios dos relatórios têm timestamps UTC. Veja o manual canônico para o caminho de saída final, registros de CI, comandos de pós-validação e critérios pendentes.

## Política de repetição

Não repetir esses E2Es para reconstruir a evidência. Um novo caso exige uma nova entrada/cópia, batch-id único, manifesto revisado, SQLite experimental e caminhos de relatório/saída inéditos. Nunca apagar um resultado anterior para liberar o destino. Não utilizar --copy-only como prova de áudio ou render real.