# ArmoredCreator Audio Lab

Laboratório isolado para validar ArmoredSpeech e ArmoredStudio antes de qualquer integração no ArmoredCreator.

## Documento canônico

**Toda a arquitetura, o fluxo ponta a ponta, as responsabilidades, o contrato-alvo, as evidências, os riscos e o plano de migração de áudio estão em [docs/ARMORED_SPEECH_STUDIO_FLOW.md](docs/ARMORED_SPEECH_STUDIO_FLOW.md).** Consulte esse documento antes de alterar a integração.

## Regras rápidas

- Ordem do pipeline: **ArmoredSync completo → ArmoredVision completo → ArmoredStock completo → ArmoredIA → ArmoredSpeech completo → ArmoredStudio → ArmoredHub**.
- Speech deve concluir o lote aprovado antes de Studio; não intercalar as fases item a item.
- Um único ambiente operacional: `.venv` na raiz para ArmoredSpeech, ArmoredStudio e RVC Melody.
- O RVC deve usar o Python compartilhado; não recriar nem depender de `ArmoredStudio/runtime/rvc/env311`.
- Preservar originais, modelo/índice Melody, assets e credenciais. Não versionar vídeos, bancos locais, ambientes virtuais, credenciais nem saídas de `storage/`.
- `armoredcreator-testbase` e `armoredcreator-test` permanecem intocados. Nenhuma mudança deste laboratório está automaticamente aprovada para produção.
- O falso positivo conhecido do detector Gemini está documentado no documento canônico e deve ser investigado separadamente; não misturar essa correção com a migração de áudio.

## Validação registrada em 16/10/2026

- Conversão real RVC Melody executada pelo Python da raiz e resultado escutado/aprovado pelo usuário.
- Teste real Speech → Studio de um vídeo reportou `STUDIO_E2E=SUCCESS` e `VALIDACAO_E2E=PASSOU`; o usuário confirmou o resultado correto.
- O CI registrado nesta branch passou em compilação e 68 testes. Isso não equivale a validar todas as rotas de áudio, o corpus inteiro ou a publicação.
- A migração de RVC/mixagem para Speech está planejada, não concluída. O Studio ainda contém parte da decisão e do processamento de áudio.

## Comandos básicos

Na raiz do repositório, em PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -c "import ArmoredSpeech; import ArmoredStudio.service; print('imports OK')"
```

Vídeos, ambientes virtuais, bancos locais, credenciais, assets privados e resultados gerados não devem ser enviados ao Git sem aprovação explícita.
