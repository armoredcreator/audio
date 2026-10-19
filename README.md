# ArmoredCreator Audio Lab

Laboratório isolado para validar a separação entre ArmoredSpeech (áudio) e ArmoredStudio (visual/render final) antes de qualquer integração operacional.

## Manual canônico

A arquitetura, o contrato ativo v5, a sequência de fases, configuração, testes, evidências E2E, segurança, revisão dos documentos anteriores e limpeza local estão consolidados em:

**[Manual canônico do ArmoredAudioLab](docs/ARMORED_AUDIO_LAB_OPERATIONS.md)**

Este manual é a fonte atual para operação e estado do laboratório. Os documentos técnicos anteriores foram convertidos em referências para evitar instruções repetidas ou estados obsoletos. O histórico de revisão auditiva permanece em [REAL_AUDIO_REVIEW_20261015.md](ArmoredSpeech/docs/REAL_AUDIO_REVIEW_20261015.md); as instruções para amostras ficam em [samples/README.md](ArmoredSpeech/samples/README.md).

## Estado em 19/10/2026

- PR #5 mesclada na branch de laboratório feature/speech-studio-batch-runner-20261016; merge commit 35896f1174dfc373fa8c5e6767dc6e54ebdfdd5e.
- CI pós-merge #751 bem-sucedido; 112 testes aprovados na validação local relatada.
- E2Es focais na ordem corrigida: NOISE → EFFECT_ONLY e PT_BR_NARRATION → RVC_MELODY, ambos NO_BANNER, com originais preservados e confirmação audiovisual do usuário.
- Nenhum dos 17 vídeos tem banner. BANNER_PRESENT é coberto por testes automatizados, não por E2E real de vídeo.
- Este merge não integrou nem alterou o repositório operacional.

Não usar banco operacional, não publicar no Hub e não executar limpeza genérica de storage, originais ou ativos locais.