# ArmoredSpeech: create and prepare its private Windows runtime.
$ErrorActionPreference = "Stop"
$SpeechRoot = $PSScriptRoot
$RepoRoot = Split-Path -Parent $SpeechRoot
$RuntimeRoot = Join-Path $SpeechRoot "runtime"
$Python = Join-Path $RuntimeRoot "venv\Scripts\python.exe"
$Requirements = Join-Path $SpeechRoot "requirements-analysis.txt"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher (py) not found. Install Python 3.11 and retry."
}
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
if (-not (Test-Path $Python)) {
    Write-Host "[ArmoredSpeech] Creating private Python 3.11 environment."
    py -3.11 -m venv (Join-Path $RuntimeRoot "venv")
    if ($LASTEXITCODE -ne 0) { throw "Failed to create ArmoredSpeech virtual environment." }
}
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip in ArmoredSpeech runtime." }
& $Python -m pip install -e $RepoRoot
if ($LASTEXITCODE -ne 0) { throw "Failed to install the lab package into ArmoredSpeech runtime." }
& $Python -m pip install -r $Requirements
if ($LASTEXITCODE -ne 0) { throw "Failed to install ArmoredSpeech analysis dependencies." }
Write-Host "[ArmoredSpeech] Runtime ready."
& $Python --version
& $Python -c "import torch, transformers, silero_vad; print('torch:', torch.__version__); print('transformers:', transformers.__version__); print('silero_vad: OK')"
if ($LASTEXITCODE -ne 0) { throw "Runtime smoke test failed." }
Write-Host "Next command:"
Write-Host ".\ArmoredSpeech\runtime\venv\Scripts\python.exe .\ArmoredSpeech\tests\validate_full_corpus.py"
