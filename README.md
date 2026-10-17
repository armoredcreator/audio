# ArmoredCreator Audio Lab

Laboratório isolado para validar ArmoredSpeech e ArmoredStudio antes de qualquer integração no ArmoredCreator.

## Documento canônico

A arquitetura, o fluxo ponta a ponta, as responsabilidades, o contrato, as evidências e o plano de migração estão em [docs/ARMORED_SPEECH_STUDIO_FLOW.md](docs/ARMORED_SPEECH_STUDIO_FLOW.md).

## Regras rápidas

- Ordem do pipeline de produção: **ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub**.
- Speech precisa concluir e validar o lote aprovado antes de Studio; não intercalar fases item a item.
- Speech é dono de todo o áudio e entrega uma trilha final AAC/M4A por item.
- Studio é dono do visual: um crop canônico e um FFmpeg final por vídeo; recebe o AAC final e usa `-c:a copy`.
- Um único ambiente Python operacional: `.venv` na raiz para Speech, Studio e RVC Melody.
- Copiar/verificar localmente os ativos RVC e o efeito para Speech com `ArmoredSpeech/scripts/migrate_audio_assets.ps1`. Não apagar os originais em Studio até E2E v4 aprovado.
- Preservar originais, modelos/índices e assets. Não versionar vídeos, bancos locais, ambientes virtuais, credenciais nem saídas de `storage/`.
- `armoredcreator-testbase`, `armoredcreator-test` e o repositório operacional ficam intocados. Esta branch não está aprovada automaticamente para produção.
- O falso positivo conhecido do detector Gemini está fora do escopo da migração de áudio e deve ser investigado separadamente.

## Estado em 17/10/2026

- O fluxo antigo do item 2 foi renderizado e aprovado pelo usuário; isso não prova o novo contrato v4.
- A branch de laboratório mudou para contrato v4: Speech produz o AAC/M4A final; Studio não recebe faixas separadas nem grafo de áudio.
- CI mais recente confirmado: compilação e 79 testes automatizados passaram: https://github.com/armoredcreator/audio/actions/runs/39048256296.
- Pendente: executar o E2E real Windows com os ativos migrados para Speech, inspecionar o resultado e validar as rotas não-RVC antes de remover os ativos legados de Studio ou mesclar a PR.

## Comandos

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -c "import ArmoredSpeech; import ArmoredStudio.service; print('imports OK')"
```
