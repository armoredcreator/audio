#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter()]
    [string]$Root = "C:\Users\Administrador\Downloads\ArmoredAudioLab",
    [Parameter()]
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath $Root).Path

# Only migrate the RVC model/index files and the canonical audio effect.
# Do NOT copy Studio's old Python 3.10 env, banner, SQLite, credentials, or videos.
$RvcSource = Join-Path $Root "ArmoredStudio\runtime\rvc\models"
$RvcTarget = Join-Path $Root "ArmoredSpeech\runtime\rvc\models"
$EffectSource = Join-Path $Root "ArmoredStudio\assets\efeitosonoro.wav"
$EffectTarget = Join-Path $Root "ArmoredSpeech\assets\efeitosonoro.wav"
$ValidationRoot = Join-Path $Root "storage\validation"
$Stamp = [DateTime]::UtcNow.ToString("yyyyMMdd'T'HHmmssfff'Z'")
$ReportDir = Join-Path $ValidationRoot "speech_rvc_asset_migration_$Stamp"
$ReportPath = Join-Path $ReportDir "asset-migration-report.json"

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-FileRecords([string]$BasePath) {
    $records = @()
    if (-not (Test-Path -LiteralPath $BasePath -PathType Container)) {
        return $records
    }
    $base = (Resolve-Path -LiteralPath $BasePath).Path.TrimEnd('\')
    foreach ($file in Get-ChildItem -LiteralPath $base -File -Recurse -Force) {
        $relative = $file.FullName.Substring($base.Length).TrimStart('\')
        $records += [pscustomobject]@{
            RelativePath = $relative
            FullName = $file.FullName
            Length = [int64]$file.Length
            Sha256 = Get-FileSha256 $file.FullName
        }
    }
    return $records
}

New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$Plan = @()
$Conflicts = @()
$Errors = @()

$SourceRvcFiles = @(Get-FileRecords $RvcSource)
$TargetRvcFiles = @(Get-FileRecords $RvcTarget)
$TargetByRelative = @{}
foreach ($row in $TargetRvcFiles) { $TargetByRelative[$row.RelativePath] = $row }

$SourceMelodyModels = @($SourceRvcFiles | Where-Object {
    $_.RelativePath -match '^(?i)melody\\[^\\]+\.pth$'
})
$TargetMelodyModels = @($TargetRvcFiles | Where-Object {
    $_.RelativePath -match '^(?i)melody\\[^\\]+\.pth$'
})
if ($SourceMelodyModels.Count -eq 0 -and $TargetMelodyModels.Count -eq 0) {
    $Errors += "Nenhum modelo .pth Melody encontrado na origem Studio nem no destino Speech."
}

foreach ($row in $SourceRvcFiles) {
    $targetFile = Join-Path $RvcTarget $row.RelativePath
    if ($TargetByRelative.ContainsKey($row.RelativePath)) {
        $existing = $TargetByRelative[$row.RelativePath]
        if ($existing.Sha256 -ne $row.Sha256) {
            $Conflicts += [pscustomobject]@{
                Kind = "RVC_MODEL_HASH_CONFLICT"
                RelativePath = $row.RelativePath
                SourceSha256 = $row.Sha256
                TargetSha256 = $existing.Sha256
            }
            $Plan += [pscustomobject]@{ Kind="RVC_MODEL"; RelativePath=$row.RelativePath; Action="CONFLICT" }
        } else {
            $Plan += [pscustomobject]@{ Kind="RVC_MODEL"; RelativePath=$row.RelativePath; Action="ALREADY_IDENTICAL" }
        }
    } else {
        $Plan += [pscustomobject]@{
            Kind = "RVC_MODEL"; RelativePath = $row.RelativePath
            Action = "COPY"; Source = $row.FullName; Target = $targetFile
            Sha256 = $row.Sha256; Length = $row.Length
        }
    }
}

$EffectSourceExists = Test-Path -LiteralPath $EffectSource -PathType Leaf
$EffectTargetExists = Test-Path -LiteralPath $EffectTarget -PathType Leaf
if ($EffectSourceExists -and $EffectTargetExists) {
    $sourceHash = Get-FileSha256 $EffectSource
    $targetHash = Get-FileSha256 $EffectTarget
    if ($sourceHash -ne $targetHash) {
        $Conflicts += [pscustomobject]@{
            Kind = "EFFECT_HASH_CONFLICT"
            SourceSha256 = $sourceHash
            TargetSha256 = $targetHash
        }
        $Plan += [pscustomobject]@{ Kind="EFFECT"; Action="CONFLICT"; SourceSha256=$sourceHash; TargetSha256=$targetHash }
    } else {
        $Plan += [pscustomobject]@{ Kind="EFFECT"; Action="ALREADY_IDENTICAL"; Sha256=$sourceHash }
    }
} elseif ($EffectSourceExists) {
    $effect = Get-Item -LiteralPath $EffectSource
    $Plan += [pscustomobject]@{
        Kind = "EFFECT"; Action = "COPY"; Source = $EffectSource; Target = $EffectTarget
        Sha256 = Get-FileSha256 $EffectSource; Length = [int64]$effect.Length
    }
} elseif ($EffectTargetExists) {
    $targetEffect = Get-Item -LiteralPath $EffectTarget
    if ($targetEffect.Length -le 0) { $Errors += "O efeito canônico do Speech existe, mas está vazio." }
    else { $Plan += [pscustomobject]@{ Kind="EFFECT"; Action="TARGET_PRESENT_SOURCE_MISSING"; Sha256=Get-FileSha256 $EffectTarget } }
} else {
    $Errors += "Efeito canônico ausente tanto em ArmoredStudio/assets quanto em ArmoredSpeech/assets."
}

$Report = [ordered]@{
    schema_version = 1
    created_at_utc = [DateTime]::UtcNow.ToString("o")
    root = $Root
    mode = $(if ($Apply) { "APPLY_COPY_NO_DELETE" } else { "PREVIEW_NO_COPY" })
    source_rvc_models = $RvcSource
    target_rvc_models = $RvcTarget
    effect_source = $EffectSource
    effect_target = $EffectTarget
    source_rvc_file_count = $SourceRvcFiles.Count
    target_rvc_file_count = $TargetRvcFiles.Count
    source_melody_pth_count = $SourceMelodyModels.Count
    target_melody_pth_count = $TargetMelodyModels.Count
    conflicts = $Conflicts
    errors = $Errors
    plan = $Plan
    copied = @()
    verification = @()
    source_files_deleted = $false
}
if ($Conflicts.Count -gt 0 -or $Errors.Count -gt 0) {
    $Report.status = "BLOCKED"
    $Report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-Output "ASSET_MIGRATION=BLOCKED"
    Write-Output "CONFLICTS=$($Conflicts.Count)"
    Write-Output "ERRORS=$($Errors.Count)"
    Write-Output "REPORT=$ReportPath"
    throw "Preflight bloqueado. Não copiei, sobrescrevi nem removi arquivos."
}

if (-not $Apply) {
    $Report.status = "PREVIEW"
    $Report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-Output "ASSET_MIGRATION=PREVIEW_NO_COPY"
    Write-Output "COPY_ACTIONS=$(@($Plan | Where-Object Action -eq 'COPY').Count)"
    Write-Output "SOURCE_RVC_FILES=$($SourceRvcFiles.Count)"
    Write-Output "TARGET_RVC_FILES=$($TargetRvcFiles.Count)"
    Write-Output "REPORT=$ReportPath"
    Write-Output "Use -Apply somente após revisar o plano e os hashes."
    return
}

try {
    foreach ($row in $Plan | Where-Object Action -eq "COPY") {
        $targetFile = [string]$row.Target
        $targetParent = Split-Path -Parent $targetFile
        New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        if (Test-Path -LiteralPath $targetFile) {
            throw "O destino surgiu depois do preflight; recuso sobrescrever: $targetFile"
        }
        Copy-Item -LiteralPath $row.Source -Destination $targetFile
        $actualHash = Get-FileSha256 $targetFile
        if ($actualHash -ne $row.Sha256) {
            throw "SHA-256 divergente após cópia: $targetFile"
        }
        $Report.copied += [pscustomobject]@{
            Kind = $row.Kind; Target = $targetFile; Length = [int64](Get-Item -LiteralPath $targetFile).Length
            Sha256 = $actualHash
        }
    }

    $verification = @()
    foreach ($row in $SourceRvcFiles) {
        $targetFile = Join-Path $RvcTarget $row.RelativePath
        if (-not (Test-Path -LiteralPath $targetFile -PathType Leaf)) {
            throw "Arquivo RVC ausente no destino após cópia: $targetFile"
        }
        $actualHash = Get-FileSha256 $targetFile
        if ($actualHash -ne $row.Sha256) {
            throw "Verificação RVC SHA-256 falhou: $targetFile"
        }
        $verification += [pscustomobject]@{ RelativePath=$row.RelativePath; Sha256=$actualHash; Status="VERIFIED" }
    }
    if (Test-Path -LiteralPath $EffectTarget -PathType Leaf) {
        if ((Get-Item -LiteralPath $EffectTarget).Length -le 0) { throw "Efeito final vazio." }
        if ($EffectSourceExists -and (Get-FileSha256 $EffectTarget) -ne (Get-FileSha256 $EffectSource)) {
            throw "Verificação SHA-256 do efeito falhou."
        }
    }
    $Report.verification = $verification
    $Report.status = "SUCCESS"
    $Report.finished_at_utc = [DateTime]::UtcNow.ToString("o")
    $Report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-Output "ASSET_MIGRATION=SUCCESS"
    Write-Output "COPIED=$($Report.copied.Count)"
    Write-Output "RVC_FILES_VERIFIED=$($verification.Count)"
    Write-Output "SOURCE_FILES_DELETED=False"
    Write-Output "REPORT=$ReportPath"
}
catch {
    $Report.status = "FAILED_PARTIAL_COPY_PRESERVED"
    $Report.error_type = $_.Exception.GetType().FullName
    $Report.error = $_.Exception.Message
    $Report.finished_at_utc = [DateTime]::UtcNow.ToString("o")
    $Report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-Output "ASSET_MIGRATION=FAILED_PARTIAL_COPY_PRESERVED"
    Write-Output "SOURCE_FILES_DELETED=False"
    Write-Output "REPORT=$ReportPath"
    throw
}
