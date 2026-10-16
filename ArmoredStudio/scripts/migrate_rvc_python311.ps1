[CmdletBinding()]
param(
    [Parameter()]
    [string]$SourceStudioRoot = "C:\Users\Administrador\Downloads\armoredcreator-rvc\ArmoredStudio"
)

$ErrorActionPreference = "Stop"

$SourceStudioRoot = (Resolve-Path -LiteralPath $SourceStudioRoot).Path
$sourcePython = Join-Path $SourceStudioRoot "runtime\rvc\env311-audit\Scripts\python.exe"
$sourceConfig = Join-Path $SourceStudioRoot "runtime\rvc\env311-audit\pyvenv.cfg"

$studioRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path | Split-Path -Parent
$rvcRoot = Join-Path $studioRoot "runtime\rvc"
$targetEnv = Join-Path $rvcRoot "env311"
$targetPython = Join-Path $targetEnv "Scripts\python.exe"

if (!(Test-Path -LiteralPath $sourcePython -PathType Leaf)) {
    throw "Python RVC de origem nao encontrado: $sourcePython"
}
if (!(Test-Path -LiteralPath $sourceConfig -PathType Leaf)) {
    throw "pyvenv.cfg da origem nao encontrado: $sourceConfig"
}

$sourceVersion = (& $sourcePython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ($LASTEXITCODE -ne 0 -or $sourceVersion -ne "3.11.9") {
    throw "A origem precisa ser Python 3.11.9; encontrado: $sourceVersion"
}

# Find the base interpreter used to create the validated venv. Do not copy
# the venv directory: Windows venv launchers/configuration are path-sensitive.
$configLines = Get-Content -LiteralPath $sourceConfig
$executableLine = $configLines | Where-Object { $_ -match '^\s*executable\s*=' } | Select-Object -First 1
$homeLine = $configLines | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1

$basePython = $null
if ($executableLine) {
    $candidate = ($executableLine -split '=', 2)[1].Trim()
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $basePython = $candidate
    }
}
if (!$basePython -and $homeLine) {
    $home = ($homeLine -split '=', 2)[1].Trim()
    $candidate = Join-Path $home "python.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $basePython = $candidate
    }
}
if (!$basePython) {
    throw "Nao consegui localizar o Python-base em pyvenv.cfg. Nenhum ambiente foi alterado."
}

$baseVersion = (& $basePython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ($LASTEXITCODE -ne 0 -or $baseVersion -ne "3.11.9") {
    throw "O Python-base precisa ser 3.11.9; encontrado: $baseVersion ($basePython)"
}

Write-Output "Origem validada: Python $sourceVersion"
Write-Output "Python-base: $basePython"
Write-Output "Destino novo: $targetEnv"

if (Test-Path -LiteralPath $targetEnv) {
    if (!(Test-Path -LiteralPath $targetPython -PathType Leaf)) {
        throw "O destino env311 existe, mas nao tem python.exe. Foi preservado; nao vou apaga-lo automaticamente: $targetEnv"
    }
    $existingVersion = (& $targetPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
    if ($LASTEXITCODE -ne 0 -or $existingVersion -ne "3.11.9") {
        throw "O destino env311 existente e de outra versao ($existingVersion). Foi preservado, sem sobrescrever."
    }
    Write-Output "Ambiente env311 existente em Python 3.11.9: vou reparar/completar dependencias nele."
} else {
    New-Item -ItemType Directory -Force -Path $rvcRoot | Out-Null
    & $basePython -m venv $targetEnv
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $targetPython -PathType Leaf)) {
        throw "Falha ao criar o venv env311. O ambiente antigo permanece intacto."
    }
}

$targetVersion = (& $targetPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ($LASTEXITCODE -ne 0 -or $targetVersion -ne "3.11.9") {
    throw "O novo ambiente nao confirmou Python 3.11.9: $targetVersion"
}

$requirementsPath = Join-Path $env:TEMP ("armored-rvc311-" + [guid]::NewGuid().ToString("N") + ".txt")
$sourcePackages = @(& $sourcePython -m pip freeze)
if ($LASTEXITCODE -ne 0 -or $sourcePackages.Count -eq 0) {
    throw "Nao foi possivel exportar as dependencias do RVC validado."
}
$sourcePackages | Set-Content -LiteralPath $requirementsPath -Encoding ASCII

try {
    Write-Output ""
    Write-Output "=== ATUALIZANDO PIP NO NOVO AMBIENTE ==="
    & $targetPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar pip no env311." }

    Write-Output ""
    Write-Output "=== INSTALANDO AS VERSOES DO RVC VALIDADO (CPU) ==="
    # Reproduce the already-tested environment exactly instead of dependency-resolving it as a fresh installation.
    # The validated source intentionally has a local fairseq 0.12.3.1 wheel, while
    # upstream rvc-python metadata requests fairseq 0.12.2. A normal resolver rejects
    # this known-working source combination, so install the pinned freeze without
    # re-resolving transitive dependencies; the source freeze contains those packages.
    & $targetPython -m pip install --no-deps --prefer-binary --extra-index-url "https://download.pytorch.org/whl/cpu" -r $requirementsPath
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependencias. O ambiente antigo foi preservado." }

    Write-Output ""
    Write-Output "=== TESTE DE IMPORTACAO ==="
    & $targetPython -c "import sys, torch, scipy; from rvc_python.infer import RVCInference; print('Python:', sys.version.split()[0]); print('Torch:', torch.__version__); print('SciPy:', scipy.__version__); print('RVCInference: OK'); print('CUDA:', torch.cuda.is_available())"
    if ($LASTEXITCODE -ne 0) { throw "As dependencias foram instaladas, mas o teste de importacao falhou." }

    $targetPackages = @(& $targetPython -m pip freeze)
    if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel verificar as versoes instaladas." }

    # Some Windows CPU wheels expose +cpu in runtime version strings while
    # the source environment's pip metadata omits it. Normalize only that
    # suffix for torch/torchaudio comparison; import test checks CPU build.
    $sourceComparable = @($sourcePackages | ForEach-Object { [regex]::Replace($_, '^(torch|torchaudio)==(.+)[+]cpu$', '$1==$2') } | Sort-Object)
    $targetComparable = @($targetPackages | ForEach-Object { [regex]::Replace($_, '^(torch|torchaudio)==(.+)[+]cpu$', '$1==$2') } | Sort-Object)
    $packageDiff = @(Compare-Object -ReferenceObject $sourceComparable -DifferenceObject $targetComparable)
    if ($packageDiff.Count -eq 0) {
        Write-Output ""
        Write-Output "DEPENDENCIAS: correspondem ao ambiente RVC validado (sufixo +cpu normalizado para comparacao)."
    } else {
        Write-Warning "O ambiente abre RVC, mas ha diferencas de pacotes. Primeiras diferencas:"
        $packageDiff | Select-Object -First 20 | Format-Table -AutoSize
        Write-Output "Manifesto temporario mantido em: $requirementsPath"
        exit 2
    }

    Write-Output ""
    Write-Output "MIGRACAO DO AMBIENTE PYTHON RVC CONCLUIDA."
    Write-Output "Python ativo para Studio: $targetPython"
    Write-Output "O ambiente Python 3.10.11 antigo nao foi removido nem alterado."
    Remove-Item -LiteralPath $requirementsPath -Force
} catch {
    Write-Error $_
    Write-Output "O ambiente antigo foi preservado."
    Write-Output "Manifesto de dependencias preservado em: $requirementsPath"
    throw
}
