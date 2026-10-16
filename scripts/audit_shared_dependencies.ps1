[CmdletBinding()]
param([string]$ReportPath = (Join-Path $env:TEMP "ArmoredAudioLab-dependency-audit.csv"))
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$definitions = @(
    [pscustomobject]@{ Name = "root"; Relative = ".venv" },
    [pscustomobject]@{ Name = "speech"; Relative = "ArmoredSpeech\runtime\venv" },
    [pscustomobject]@{ Name = "rvc"; Relative = "ArmoredStudio\runtime\rvc\env311" }
)
function Normalize-Name([string]$Name) {
    return (($Name.ToLowerInvariant() -replace '[-_.]+', '-').Trim('-'))
}
function Normalize-Version([string]$Name, [string]$Version) {
    $normalizedName = Normalize-Name $Name
    if ($normalizedName -in @("torch", "torchaudio")) {
        return ($Version -replace '\+cpu$', '')
    }
    return $Version
}
$environments = @()
foreach ($definition in $definitions) {
    $environmentRoot = Join-Path $RepoRoot $definition.Relative
    $candidates = @(
        (Join-Path $environmentRoot "Scripts\python.exe"),
        (Join-Path $environmentRoot "bin\python"),
        (Join-Path $environmentRoot "bin\python3")
    )
    $python = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (!$python) {
        Write-Output ("AMBIENTE_AUSENTE: {0} ({1})" -f $definition.Name, $environmentRoot)
        continue
    }
    $pythonVersion = (& $python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Nao consegui identificar o Python em $python" }
    $freeze = @(& $python -m pip freeze)
    if ($LASTEXITCODE -ne 0) { throw "pip freeze falhou em $($definition.Name)." }
    $packages = @{}
    foreach ($line in $freeze) {
        if ($line -match '^([^=<>!~]+)==(.+)$') {
            $originalName = $Matches[1]
            $packageName = Normalize-Name $originalName
            $packageVersion = $Matches[2].Trim()
            if ($packageName) {
                $packages[$packageName] = [pscustomobject]@{
                    Name = $originalName
                    Version = $packageVersion
                    ComparisonVersion = Normalize-Version $originalName $packageVersion
                }
            }
        }
    }
    $environments += [pscustomobject]@{
        Name = $definition.Name
        Python = $python
        PythonVersion = $pythonVersion
        Packages = $packages
    }
    Write-Output ("AMBIENTE_OK: {0}; Python={1}; pacotes pip={2}" -f $definition.Name, $pythonVersion, $packages.Count)
}
if ($environments.Count -lt 2) {
    throw "Preciso de pelo menos dois ambientes existentes para comparar. Nenhum ambiente foi alterado."
}
$allNames = @($environments | ForEach-Object { $_.Packages.Keys } | Sort-Object -Unique)
$rows = @()
foreach ($name in $allNames) {
    $installed = @()
    foreach ($environment in $environments) {
        if ($environment.Packages.ContainsKey($name)) {
            $package = $environment.Packages[$name]
            $installed += [pscustomobject]@{
                Environment = $environment.Name
                Version = $package.Version
                ComparisonVersion = $package.ComparisonVersion
            }
        }
    }
    if ($installed.Count -lt 2) { continue }
    $versions = @($installed.ComparisonVersion | Sort-Object -Unique)
    $status = if ($versions.Count -eq 1) { "SAME_VERSION_CANDIDATE" } else { "VERSION_CONFLICT" }
    $row = [ordered]@{ Package = $name; Status = $status }
    foreach ($environment in $environments) {
        $match = $installed | Where-Object Environment -eq $environment.Name | Select-Object -First 1
        $row[$environment.Name] = if ($match) { $match.Version } else { "" }
    }
    $rows += [pscustomobject]$row
}
if ($rows.Count -gt 0) {
    Write-Output ([Environment]::NewLine + "===== PACOTES PRESENTES EM MAIS DE UM AMBIENTE =====")
    $rows | Sort-Object Status, Package | Format-Table -AutoSize
} else {
    Write-Output ([Environment]::NewLine + "Nenhum pacote duplicado encontrado entre os ambientes existentes.")
}
if ($ReportPath) {
    $parent = Split-Path -Parent $ReportPath
    if ($parent -and !(Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $rows | Export-Csv -LiteralPath $ReportPath -NoTypeInformation -Encoding UTF8
    Write-Output ("RELATORIO_CSV=" + $ReportPath)
}
Write-Output ([Environment]::NewLine + "Auditoria somente leitura concluida. Nenhum pacote ou ambiente foi alterado.")
