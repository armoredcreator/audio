# Primeiro teste isolado no Windows

O teste usa CPU por padrão e roda em um ambiente virtual próprio. Não usa o Python global, o ambiente RVC ou os arquivos de saída do ArmoredCreator.

## 1. Clonar e criar o ambiente

No PowerShell:

~~~powershell
git clone https://github.com/armoredcreator/audio.git ArmoredAudioLab
Set-Location .\ArmoredAudioLab
git fetch origin
git switch refactor/ArmoredSpeech-directory-20261015
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install pytest numpy
~~~

Se o comando de ativação for bloqueado pela política do PowerShell, não altere a política global do computador. Use sempre o Python do ambiente diretamente, por exemplo .\.venv\Scripts\python.exe.

A instalação e o primeiro download do modelo podem levar tempo e ocupar espaço. Não prossiga se o disco estiver quase cheio.

## 2. Verificar o CLI e os modelos disponíveis

~~~powershell
audio-separator --env_info
audio-separator --list_models | Select-String -Pattern 'Voc_FT|MDX'
~~~

Confirme que o modelo indicado existe na lista. Se o nome UVR-MDX-NET-Voc_FT.onnx não aparecer, escolha um nome exato mostrado pelo comando e passe-o com --model. Não faça um lote para descobrir o modelo.

## 3. Separar um clipe curto

Escolha uma cópia de teste ou um vídeo original como entrada, mas direcione os resultados para uma pasta totalmente separada. Exemplo (ajuste o caminho do vídeo):

~~~powershell
.\.venv\Scripts\python.exe .\ArmoredSpeech\scripts\separate_one.py "C:\CAMINHO\VIDEO_TESTE.mp4" --output-root "C:\ArmoredAudioLab-outputs"
~~~

Para escolher um modelo explícito:

~~~powershell
.\.venv\Scripts\python.exe .\ArmoredSpeech\scripts\separate_one.py "C:\CAMINHO\VIDEO_TESTE.mp4" --output-root "C:\ArmoredAudioLab-outputs" --model "UVR-MDX-NET-Voc_FT.onnx"
~~~

O script calcula o SHA-256 da entrada antes e depois, cria uma pasta de execução exclusiva e lista as saídas. Ele não deve ser apontado para uma pasta de saída que contenha ou seja ancestral da entrada.

## 4. O que medir

- Tempo total e se a CPU permanece utilizável.
- RAM usada no Gerenciador de Tarefas.
- Se foram geradas as faixas vocal e instrumental.
- Se a faixa vocal preserva palavras completas em português.
- Se a música realmente diminui sem deixar voz metálica ou palavras cortadas.
- Se a faixa instrumental contém a narração residual.

**Não executar RVC, renderização ou publicação.** Uma saída criada sem erro não significa que o teste passou; é obrigatória a avaliação auditiva.

## Nota sobre desempenho

O modelo i3-2370M e os gráficos Intel HD 3000 não têm aceleração moderna para estes modelos. O teste pode funcionar em CPU, mas ser lento. Comece com um clipe de poucos segundos e pare se houver pressão excessiva de RAM, travamento ou falta de espaço. Não processar a coleção inteira até conhecer o custo real.

## 5. Executar o lote experimental ArmoredSpeech

Instale as dependências opcionais de classificação no mesmo venv isolado:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r .\ArmoredSpeech\requirements-analysis.txt
.\.venv\Scripts\python.exe -m pip install -r .\ArmoredSpeech\requirements-separation.txt
~~~

Use cópias de teste fora da pasta de produção e ajuste ArmoredSpeech/examples/armoredspeech-items.example.json. O source_order define a ordem histórica das fontes (1, 2, 3); source_id guarda a identidade da fonte; item_id guarda o ID do item.

~~~powershell
.\.venv\Scripts\python.exe .\ArmoredSpeech\scripts\run_armoredspeech.py --items .\ArmoredSpeech\examples\armoredspeech-items.example.json --database "C:\ArmoredAudioLab\state\armoredspeech.sqlite3"
~~~

Sem --decisions, o runner usa o classificador CPU (Whisper + Silero VAD + AudioSet/AST). O primeiro uso pode baixar modelos e demorar. Se faltar evidência confiável, o item não é aprovado para RVC.

Para testar o fluxo com rótulos revisados, use --decisions .\ArmoredSpeech\examples\armoredspeech-decisions.example.json. Os rótulos do exemplo são ilustrativos e precisam ser conferidos.

**Atenção:** o WAV é escrito ao lado do vídeo de entrada com o mesmo nome-base. Nunca aponte o lote para a pasta de produção nesta fase. Não executar RVC, renderização ou publicação. A integração com o schema SQLite real ainda não foi feita.
