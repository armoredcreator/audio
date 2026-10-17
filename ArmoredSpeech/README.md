# ArmoredSpeech — módulo de áudio do AudioLab

ArmoredSpeech é o módulo responsável por analisar e preparar áudio no laboratório isolado. A arquitetura completa, a ordem do pipeline, as regras por classe de áudio, o contrato Speech → Studio, as evidências e o plano para transferir RVC/mixagem para Speech estão centralizados em:

**[Documentação canônica ArmoredSpeech → ArmoredStudio](../../docs/ARMORED_SPEECH_STUDIO_FLOW.md)**

## Regras essenciais

- Fluxo maior: **ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub**.
- Speech conclui todo o lote aprovado antes de Studio começar.
- O original MP4 é somente leitura; validar SHA-256 antes/depois.
- Decisão desconhecida, artefato inválido ou falha bloqueia RVC.
- A implementação atual de integração ainda é parcial: RVC, parte da decisão de áudio e mixagem continuam no Studio. A migração está planejada, não concluída.
- Usar apenas a `.venv` da raiz. Não criar outro ambiente RVC.
- Este laboratório não autoriza publicar nem modificar o ArmoredCreator operacional.

## Comandos básicos

Na raiz do repositório, no PowerShell:

```powershell
.\ArmoredSpeech\setup_runtime.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_real_audio.py
```

Execute a validação de áudio real somente quando o corpus local estiver presente. Nunca versionar vídeos, credenciais, banco local, ambientes virtuais ou saídas de `storage/`.
