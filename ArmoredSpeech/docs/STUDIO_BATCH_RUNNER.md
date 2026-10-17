# Speech batch → Studio visual renderer (contract v4)

O runner recebe um manifesto que declara o lote Speech completo e aprovado. Não descobre lote parcial pelo SQLite operacional e não abre banco de produção.

## Fluxo

1. Studio faz uma pré-análise somente visual: enquadramento seguro, barras, banner e detectores VEO/Gemini.
2. Speech lê as decisões READY da base isolada, valida original/WAV, classifica o áudio e resolve a rota segura.
3. Speech executa RVC Melody quando elegível, prepara o efeito com ganho aplicado uma vez, decide a timeline de áudio, faz trim/fades/mixagem/intro e executa FFmpeg para materializar **um único AAC/M4A final por item**.
4. O handoff v4 contém identidade, hash do original, arquivo final AAC/M4A, hash/duração e metadados da timeline/plano visual. Não contém WAVs separados de voz/efeito nem grafo de áudio.
5. O runner valida todos os artefatos finais e a integridade dos originais antes da primeira renderização.
6. Studio recalcula/valida o plano visual correspondente e faz um único crop na fonte. O FFmpeg final aplica somente o grafo visual e copia o AAC pronto com `-c:a copy` para o MP4.
7. O runner verifica saída e SHA-256 dos originais.

## Comandos

Na raiz do laboratório:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
```

O E2E real requer os modelos/índices em `ArmoredSpeech/runtime/rvc/models`, o efeito em `ArmoredSpeech/assets/efeitosonoro.wav`, o corpus local e um manifesto Speech aprovado. O script de migração copia e confere SHA-256 sem apagar os ativos antigos; só remova a origem após o E2E v4 real ser aprovado.

## Separação e segurança

- A rota RVC/EFFECT_ONLY, o silêncio, a mixagem, os ganhos, fades e o AAC final pertencem ao Speech.
- Studio só valida o artefato final, edita pixels e faz mux da trilha já codificada.
- Um crop canônico é aplicado uma vez no grafo visual.
- Há uma única invocação FFmpeg final por vídeo no Studio. Speech ainda usa suas próprias invocações FFmpeg para preparar o áudio.
- Nenhum teste desta branch publica no Hub.
