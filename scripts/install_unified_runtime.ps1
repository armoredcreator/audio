[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$TargetRoot,
    [Parameter(Mandatory=$true)][string]$Wheelhouse,
    [Parameter()][string]$PipCache,
    [Parameter()][string]$PythonExecutable
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$TargetRoot = [IO.Path]::GetFullPath($TargetRoot)
$Wheelhouse = (Resolve-Path -LiteralPath $Wheelhouse).Path
$EnvRoot = Join-Path $TargetRoot '.venv'
if (Test-Path -LiteralPath $EnvRoot) {
    throw 'Refusing to modify an existing environment. Use a new isolated TargetRoot.'
}
if ((Test-Path -LiteralPath $TargetRoot) -and (Get-ChildItem -LiteralPath $TargetRoot -Force | Select-Object -First 1)) {
    throw 'TargetRoot must be new or empty; existing files are preserved.'
}
$Expected = @{
    'fairseq-0.12.3.1-cp311-cp311-win_amd64.whl' = '515f6b9e9cfd2d14c16eb6c25f60df2c48335a01b6a51da78f1776b6317a528f'
    'rvc_python-0.1.5+armored311.1-py3-none-any.whl' = '88be78121a4bf64e74e8dad6c28c61fa0dacaad329199f5287bea458b8831765'
}
foreach ($Name in $Expected.Keys) {
    $Artifact = Join-Path $Wheelhouse $Name
    if (!(Test-Path -LiteralPath $Artifact -PathType Leaf)) { throw "Missing reviewed artifact: $Name" }
    $Actual = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected[$Name]) { throw "Artifact checksum mismatch: $Name" }
}
if ($PythonExecutable) {
    $PythonExecutable = (Resolve-Path -LiteralPath $PythonExecutable).Path
    $Version = (& $PythonExecutable -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
} else {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Python Launcher required or supply -PythonExecutable.' }
    $Version = (& py -3.11 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
}
if ($LASTEXITCODE -ne 0 -or $Version -ne '3.11.9') { throw "Python 3.11.9 required; found $Version" }
New-Item -ItemType Directory -Path $TargetRoot -Force | Out-Null
$SourceArchive = Join-Path $TargetRoot '.reviewed-source.zip'
& git -c "safe.directory=$RepoRoot" -C $RepoRoot archive --format=zip HEAD -o $SourceArchive
if ($LASTEXITCODE -ne 0) { throw 'Could not export the reviewed source commit.' }
Expand-Archive -LiteralPath $SourceArchive -DestinationPath $TargetRoot
if ($PythonExecutable) { & $PythonExecutable -m venv $EnvRoot } else { & py -3.11 -m venv $EnvRoot }
if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated environment.' }
$Python = Join-Path $EnvRoot 'Scripts\python.exe'
$env:PIP_CACHE_DIR = if ($PipCache) { [IO.Path]::GetFullPath($PipCache) } else { Join-Path $TargetRoot 'pip-cache' }
& $Python -m pip install --find-links $Wheelhouse -r (Join-Path $RepoRoot 'requirements-runtime311.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Pinned runtime installation failed; preserve logs and the isolated environment.' }
& $Python -m pip install --no-deps -e $TargetRoot
if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency failed.' }
& $Python -c "import sys,torch,fairseq,omegaconf,cv2,telegram; from rvc_python.infer import RVCInference; print(sys.version); print('Unified runtime imports: OK')"
if ($LASTEXITCODE -ne 0) { throw 'Runtime import validation failed.' }
Write-Host 'New isolated environment installed. Models are not downloaded or modified by this installer.'
