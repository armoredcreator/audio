# Assets visuais do ArmoredStudio

Esta pasta mantém os assets visuais locais consumidos pelo ArmoredStudio.

- `banner.png`: banner visual usado na composição/intro do vídeo. Preserve o asset validado; não o substitua por uma versão arbitrária.
- O efeito sonoro `efeitosonoro.wav` não é mais um asset de runtime do Studio. A propriedade canônica é do ArmoredSpeech, em `ArmoredSpeech/assets/efeitosonoro.wav`.
- Os modelos e índices RVC também pertencem ao ArmoredSpeech e ficam em `ArmoredSpeech/runtime/rvc/models/`. O Studio não deve executar RVC nem usar assets de áudio legados como fallback.

Os binários de runtime são locais e não são versionados. Esta nota descreve a fronteira vigente de propriedade dos assets; consulte o [manual canônico do AudioLab](../../docs/ARMORED_AUDIO_LAB_OPERATIONS.md) para setup, validação e preservação dos arquivos.
