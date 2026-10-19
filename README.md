# ArmoredCreator Audio Lab

Laboratório isolado para validar a separação entre ArmoredSpeech (áudio) e ArmoredStudio (visual/render final) antes de qualquer integração operacional.

## Manual canônico

A arquitetura, o contrato ativo v5, a sequência de fases, configuração, testes, evidências E2E, segurança, auditoria de duplicações, decisão sobre módulos órfãos e limpeza local estão consolidados em:

**[Manual canônico do ArmoredAudioLab](docs/ARMORED_AUDIO_LAB_OPERATIONS.md)**

Este manual é a fonte operacional detalhada. Os documentos técnicos anteriores são referências auxiliares/históricas para evitar instruções repetidas ou estados antigos.

## Estado em 19/10/2026

- O fluxo de lote Speech → Studio está integrado na branch de laboratório test/armoredstudio-speech-integration-20261015; contrato ativo v5.
- A preparação WAV e o SHA-256 de arquivos foram unificados em helpers compartilhados.
- Nesta rodada, os duplicados comprovados de materialização de assets oficiais, digest canônico do plano visual, contenção de caminhos e opções de subprocesso Windows foram centralizados em armored_core, mantendo os wrappers usados por outros módulos.
- O workflow de testes agora dispara em push e pull request quando mudam ArmoredSpeech, ArmoredStudio, armored_core ou scripts.
- No merge atual da PR #7 (`3e6969eeb4c42d747989fc3d865f1edfd72a92b6`), o CI passou `compileall` e **127 testes** ([testes isolados](https://github.com/armoredcreator/audio/actions/runs/39336661372)); o [workflow de diagnósticos de áudio](https://github.com/armoredcreator/audio/actions/runs/39336661228) também passou.
- Os dois E2Es focais pós-correção continuam sendo a evidência real atual: EFFECT_ONLY e RVC_MELODY, ambos NO_BANNER, preservando os originais.
- A revisão não encontrou módulo que pudesse ser removido com segurança apenas por parecer antigo; as ferramentas de manifesto, modelo, lote/corpus, separação individual e diagnóstico mantêm usos distintos.

Este trabalho continua restrito ao AudioLab. Não migra código para armoredcreator/armoredcreator, não usa banco operacional, não publica no Hub e não apaga storage, originais ou assets locais.
