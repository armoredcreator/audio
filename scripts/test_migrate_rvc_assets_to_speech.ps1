#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ScriptPath = Join-Path $PSScriptRoot "migrate_rvc_assets_to_speech.ps1"
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("ArmoredAudioLab-RVC-migration-test-" + [guid]::NewGuid().ToString("N"))

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Get-Hash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

try {
    $SourceModels = Join-Path $TempRoot "ArmoredStudio\runtime\rvc\models"
    $TargetModels = Join-Path $TempRoot "ArmoredSpeech\runtime\rvc\models"
    $SourceAssets = Join-Path $TempRoot "ArmoredStudio\assets"
    $TargetAssets = Join-Path $TempRoot "ArmoredSpeech\assets"
    New-Item -ItemType Directory -Path (Join-Path $SourceModels "melody"), $SourceAssets, $TargetModels, $TargetAssets -Force | Out-Null

    $Model = Join-Path $SourceModels "melody\melody.pth"
    $Index = Join-Path $SourceModels "melody\melody_v2.index"
    $Cache = Join-Path $SourceModels "melody\cache.bin"
    $Notes = Join-Path $SourceModels "notes.txt"
    $Effect = Join-Path $SourceAssets "efeitosonoro.wav"
    [IO.File]::WriteAllBytes($Model, [byte[]](1, 2, 3, 4, 5))
    [IO.File]::WriteAllBytes($Index, [byte[]](6, 7, 8, 9))
    [IO.File]::WriteAllBytes($Cache, [byte[]](10, 11, 12))
    [IO.File]::WriteAllText($Notes, "must not migrate")
    [IO.File]::WriteAllBytes($Effect, [byte[]](13, 14, 15, 16))
    $ModelHash = Get-Hash $Model
    $IndexHash = Get-Hash $Index
    $EffectHash = Get-Hash $Effect

    & $ScriptPath -Root $TempRoot
    $PreviewDir = Get-ChildItem -LiteralPath (Join-Path $TempRoot "storage\validation") -Directory |
        Sort-Object Name -Descending | Select-Object -First 1
    $Preview = Get-Content -LiteralPath (Join-Path $PreviewDir.FullName "asset-migration-report.json") -Raw | ConvertFrom-Json
    Assert-True ($Preview.status -eq "PREVIEW") "preview must not copy"
    $PlannedCopies = @($Preview.plan | Where-Object { $_.Action -eq "COPY" })
    Assert-True ($PlannedCopies.Count -eq 3) "preview should plan only model, index and effect"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $TargetModels "melody\melody.pth"))) "preview copied a model"
    Assert-True ((Get-Hash $Model) -eq $ModelHash) "preview changed source model"

    & $ScriptPath -Root $TempRoot -Apply
    $ApplyDir = Get-ChildItem -LiteralPath (Join-Path $TempRoot "storage\validation") -Directory |
        Sort-Object Name -Descending | Select-Object -First 1
    $Applied = Get-Content -LiteralPath (Join-Path $ApplyDir.FullName "asset-migration-report.json") -Raw | ConvertFrom-Json
    Assert-True ($Applied.status -eq "SUCCESS") "apply should report SUCCESS"
    Assert-True ((Get-Hash (Join-Path $TargetModels "melody\melody.pth")) -eq $ModelHash) "model hash mismatch"
    Assert-True ((Get-Hash (Join-Path $TargetModels "melody\melody_v2.index")) -eq $IndexHash) "index hash mismatch"
    Assert-True ((Get-Hash (Join-Path $TargetAssets "efeitosonoro.wav")) -eq $EffectHash) "effect hash mismatch"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $TargetModels "melody\cache.bin"))) "unrelated cache file was copied"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $TargetModels "notes.txt"))) "unrelated note was copied"
    Assert-True ((Get-Hash $Model) -eq $ModelHash) "source model changed"
    Assert-True ((Get-Hash $Index) -eq $IndexHash) "source index changed"
    Assert-True ((Get-Hash $Effect) -eq $EffectHash) "source effect changed"

    Write-Output "RVC_ASSET_MIGRATION_SCRIPT_TEST=PASS"
}
finally {
    if (Test-Path -LiteralPath $TempRoot -PathType Container) {
        $TempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        $ResolvedTempRoot = [IO.Path]::GetFullPath($TempRoot)
        $ExpectedNamePrefix = 'ArmoredAudioLab-RVC-migration-test-'
        if (
            -not $ResolvedTempRoot.StartsWith($TempBase, [StringComparison]::OrdinalIgnoreCase) -or
            -not ([IO.Path]::GetFileName($ResolvedTempRoot)).StartsWith(
                $ExpectedNamePrefix, [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Refusing recursive cleanup outside the generated migration-test temp directory: $ResolvedTempRoot"
        }
        Remove-Item -LiteralPath $ResolvedTempRoot -Recurse -Force
    }
}
