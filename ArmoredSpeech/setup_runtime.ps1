# Compatibility entry point. ArmoredSpeech uses the repository-root .venv.
[CmdletBinding()]
param(
    [Parameter()]
    [switch]$InstallSeparation
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$SharedEnv = Join-Path $RepoRoot ".venv"
$Python = Join-Path $SharedEnv "Scripts\python.exe"
$Requirements = Join-Path $PSScriptRoot "requirements-analysis.txt"

if (!(Test-Path -LiteralPath $Python -PathType Leaf)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Python Launcher (py) nao encontrado. Instale Python 3.11.9 e tente novamente."
    }
    $LauncherVersion = (& py -3.11 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
    if ($LASTEXITCODE -ne 0 -or $LauncherVersion -ne '3.11.9') {
        throw "O ambiente unificado exige Python 3.11.9. Launcher selecionou: $LauncherVersion"
    }
    Write-Host "[ArmoredSpeech] Criando o unico ambiente compartilhado: $SharedEnv"
    & py -3.11 -m venv $SharedEnv
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Falha ao criar o ambiente compartilhado .venv."
    }
}

$Version = (& $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ($LASTEXITCODE -ne 0 -or $Version -ne '3.11.9') {
    throw "O ambiente compartilhado precisa usar Python 3.11.9. Encontrado: $Version"
}

Write-Host "[ArmoredSpeech] Usando o ambiente compartilhado: $Python (Python $Version)"
& $Python -m pip install -e $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar o AudioLab no .venv compartilhado." }

& $Python -m pip install -r $Requirements
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar requirements-analysis.txt no .venv compartilhado." }

if ($InstallSeparation) {
    $SeparationRequirements = Join-Path $PSScriptRoot "requirements-separation.txt"
    & $Python -m pip install -r $SeparationRequirements
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar separacao de audio no .venv compartilhado." }
}

& $Python -c "import sys, numpy, torch, transformers, faster_whisper, silero_vad; print('Python:', sys.version.split()[0]); print('NumPy:', numpy.__version__); print('Torch:', torch.__version__); print('Transformers:', transformers.__version__); print('Speech runtime: OK')"
if ($LASTEXITCODE -ne 0) { throw "Smoke test do ambiente compartilhado falhou." }

$Legacy = Join-Path $PSScriptRoot "runtime\venv"
if (Test-Path -LiteralPath $Legacy) {
    Write-Host "[ArmoredSpeech] ATENCAO: runtime\venv legado ainda existe, mas este script nao o usa nem o apaga."
    Write-Host "[ArmoredSpeech] Remova-o somente apos confirmar que nenhum comando local depende dele."
}

Write-Host "[ArmoredSpeech] Ambiente compartilhado pronto."
Write-Host "Validacao do corpus:"
Write-Host '.\.venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_full_corpus.py'
