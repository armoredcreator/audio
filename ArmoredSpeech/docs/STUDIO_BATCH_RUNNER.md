# Speech batch → Studio visual renderer

O runner de laboratório só recebe um manifesto que declara o lote Speech completo e aprovado. Não descobre um lote parcial pelo SQLite operacional e não abre banco de produção.

## Fluxo

1. Studio executa uma pré-análise **somente visual** para encontrar barras, banner, caixas VEO/Gemini e preparar um plano de enquadramento.
2. Speech lê as decisões READY do seu SQLite, confere original e WAV, resolve a classe/rota e executa todo o RVC elegível.
3. Speech deriva o corte temporal de fala a partir do WAV quando existe banner e constrói o grafo de áudio: trim, fades, mixagem e concatenação do efeito/intro. O efeito é preparado com ganho uma única vez e compartilhado por lote.
4. O runner valida identidade, hashes, arquivos, modelo/index, grafo e duração de todos os itens antes de chamar o Studio.
5. O plano visual é atualizado com o início de vídeo derivado por Speech e seu hash deve coincidir com o hash no contrato.
6. Studio combina o grafo visual (crop único na fonte, split de intro/main, filtros de imagem) com o grafo de áudio gerado pelo Speech em uma única chamada FFmpeg final. Não há pré-mixagem de um WAV final nem chamada FFmpeg separada para renderizar as cenas e depois muxar.
7. O runner confirma caminhos de saída e SHA-256 dos originais.

## Comandos

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

O teste real exige os ativos migrados para `ArmoredSpeech/runtime/rvc/models` e `ArmoredSpeech/assets/efeitosonoro.wav`, além do corpus local. Não executar até o script `ArmoredSpeech/scripts/migrate_audio_assets.ps1` concluir e os dois caminhos existirem.

## Segurança

- Uma classificação desconhecida ou pacote ausente bloqueia a primeira renderização.
- O caminho `EFFECT_ONLY` usa silêncio gerado como entrada FFmpeg; nunca reusa o áudio original do vídeo.
- O Studio não executa RVC, não analisa fala/idioma/música e não aplica volume, fades, cortes ou mixagem.
- Nenhum teste desta branch publica no Hub.
