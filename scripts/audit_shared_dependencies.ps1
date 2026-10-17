[CmdletBinding()]
param([string]$ReportPath = (Join-Path $env:TEMP "ArmoredAudioLab-unified-dependency-audit.csv"))

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (!(Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Ambiente unificado nao encontrado: $Python"
}

$Version = (& $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel identificar o Python unificado." }
Write-Output ("AMBIENTE_UNIFICADO: Python={0}; caminho={1}" -f $Version, $Python)

$Lines = @(& $Python -m pip freeze)
if ($LASTEXITCODE -ne 0) { throw "pip freeze falhou no ambiente unificado." }

$Rows = @()
foreach ($Line in $Lines) {
    if ($Line -match '^([^=<>!~]+)==(.+)$') {
        $Rows += [pscustomobject]@{
            Package = $Matches[1]
            Version = $Matches[2].Trim()
        }
    }
}

Write-Output ("PACOTES_REGISTRADOS=" + $Rows.Count)
$Rows | Sort-Object Package | Format-Table -AutoSize

Write-Output "`n===== TESTE DE IMPORTS SPEECH + RVC ====="
& $Python -c "import numpy, torch, torchaudio, transformers, faster_whisper, silero_vad, fairseq, cloudpickle, lxml.etree, librosa, numba, faiss, parselmouth, pyworld, soundfile, soxr, torchcrepe; from rvc_python.infer import RVCInference; print('Python imports Speech + RVC: OK'); print('NumPy:', numpy.__version__); print('Torch:', torch.__version__); print('TorchAudio:', torchaudio.__version__); print('Fairseq:', __import__('importlib.metadata', fromlist=['version']).version('fairseq')); print('RVCInference: OK')"
if ($LASTEXITCODE -ne 0) { throw "O teste de imports do ambiente unificado falhou." }

if ($ReportPath) {
    $Parent = Split-Path -Parent $ReportPath
    if ($Parent -and !(Test-Path -LiteralPath $Parent)) {
        New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    }
    $Rows | Export-Csv -LiteralPath $ReportPath -NoTypeInformation -Encoding UTF8
    Write-Output ("RELATORIO_CSV=" + $ReportPath)
}

Write-Output "`nAUDITORIA_DO_AMBIENTE_UNIFICADO=PASSOU"
Write-Output "Somente leitura: nenhum pacote foi instalado, atualizado ou removido."
