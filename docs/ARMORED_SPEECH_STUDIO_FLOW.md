# Fluxo ArmoredSpeech → ArmoredStudio

Este documento é um índice do [Manual canônico do ArmoredAudioLab](ARMORED_AUDIO_LAB_OPERATIONS.md), que contém a arquitetura, os módulos, o contrato v5, as rotas, os comandos seguros e o registro de evidências E2E.

## Sequência vigente

Speech completo → validação do lote aprovado e das duas variantes de cada item → análise visual somente de leitura por Studio → seleção da variante temporal existente → validação final → render audiovisual com uma chamada FFmpeg por vídeo → inspeção de streams, decodificação e integridade dos originais.

Speech é proprietário de toda decisão e preparação de áudio. Studio é proprietário do visual, da análise de banner/barras/marcas e do render final. Não intercalar Speech e Studio por item, não recalcular áudio no Studio e não usar o SQLite operacional neste laboratório.

## Evidência E2E atual

- **NO_BANNER:** o corpus consolidado de 17 vídeos passou 17/17 na pós-validação. Nove itens usaram RVC_MELODY e oito EFFECT_ONLY; todos os arquivos finais têm H.264 1080×1920, AAC 44.100 Hz estéreo, decodificação completa aprovada e originais íntegros.
- **BANNER_PRESENT:** E2E real com storage/original_018.mp4. Detector confirmou banner_detectado=true; Studio selecionou BANNER_PRESENT e manteve a rota RVC_MELODY. O arquivo final passou ffprobe e decodificação; o hash do original permaneceu íntegro e o usuário aprovou visualmente a saída.

BANNER_PRESENT não está mais limitado a cobertura automatizada: há uma execução real aprovada. A amostra testada não garante cobertura de todas as famílias de banner.

## Limite operacional

Esta validação continua restrita ao repositório e ao armazenamento do laboratório. Não modifica o ArmoredCreator operacional, não toca no banco operacional, não publica no Hub e não autoriza uma cópia direta do código para produção.