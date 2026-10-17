# ArmoredSpeech — proprietário do processamento de áudio

ArmoredSpeech é o único responsável por classificar e preparar o áudio no AudioLab. ArmoredStudio é responsável apenas por análise/edição visual e renderização final do vídeo.

## Contrato de responsabilidade

- **ArmoredSpeech:** detectar fala, idioma, ruído e música; decidir a rota segura; extrair/preparar áudio; executar RVC Melody quando elegível; preparar o efeito sonoro com ganho aplicado uma única vez; decidir corte temporal de áudio e início de fala do banner; construir e validar o grafo de áudio para a renderização final.
- **ArmoredStudio:** analisar imagem, detectar barras/banners visuais e marcas VEO/Gemini; consolidar as regiões seguras num plano visual; aplicar um único crop de enquadramento; renderizar a imagem e combinar o grafo de áudio assinado pelo Speech na única chamada FFmpeg final.
- **Fronteira:** contrato v3 em `ArmoredStudio/speech_handoff.py`; contém identidade do item, hashes, rota, faixas de voz/efeito, política de ganho e receita temporal de áudio. Studio não reclassifica, extrai áudio, escolhe Melody, gera silêncio, executa RVC, escolhe ganhos, fades, cortes ou mixagem.
- A preparação interna de áudio pode usar FFmpeg para converter WAVs necessários ao RVC e preparar o efeito. A **renderização final do MP4** é uma única invocação FFmpeg que combina os grafos visual e de áudio.
- O crop do plano de segurança e o enquadramento 9:16 são consolidados num único retângulo de origem e aplicados antes de dividir as cenas principal/intro.
- O original MP4 é somente leitura; SHA-256 antes/depois.
- Lotes completos são preparados e validados antes da primeira renderização do Studio.
- RVC usa somente a `.venv` na raiz. Os modelos passam a morar em `ArmoredSpeech/runtime/rvc/models`; o efeito em `ArmoredSpeech/assets/efeitosonoro.wav`.

## Preparar ambiente e ativos (Windows PowerShell)

Na raiz do repositório:

```powershell
.\ArmoredSpeech\setup_runtime.ps1
.\ArmoredSpeech\scripts\migrate_audio_assets.ps1
.\.venv\Scripts\python.exe -m pytest -q
```

A migração copia modelos e efeito da antiga localização do Studio, verifica SHA-256 e **não apaga nem sobrescreve** arquivos antigos. Só remova os ativos de origem depois de uma execução real passar com os novos caminhos.

## Diagnóstico e RVC

```powershell
.\.venv\Scripts\python.exe -m ArmoredSpeech.rvc_engine .\entrada.wav .\storage\validation\melody_output.wav melody
.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_real_audio.py
```

Use uma saída que não exista e não altere os originais. O validador real exige o corpus local.

## Limites

Este é o laboratório isolado do repositório `armoredcreator/audio`. Nada aqui publica no Hub nem altera o repositório operacional. A migração final para o projeto de produção só deve ocorrer depois dos testes reais das classes de áudio e da inspeção audiovisual.
