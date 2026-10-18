[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$StudioRoot = Join-Path $RepoRoot "ArmoredStudio"
$SpeechRoot = Join-Path $RepoRoot "ArmoredSpeech"

function Get-PortableRelativePath {
    param([Parameter(Mandatory)][string]$Path)

    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $RootPrefix = $RepoRoot.TrimEnd([char[]]@([char]92, [char]47)) + [System.IO.Path]::DirectorySeparatorChar
    if ($FullPath.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $FullPath.Substring($RootPrefix.Length)
    }
    return $FullPath
}

Write-Output "AUDIO_OWNERSHIP_AUDIT=READ_ONLY"
Write-Output ("REPOSITORY_ROOT=" + $RepoRoot)

# Fail only for executable legacy ownership, not comments or the v5 handoff.
$LegacyExecutionPattern = '(?i)(\bRVCInference\b|\bconverter_voz\s*\(|\bexecutar_no_rvc\s*\(|from\s+(?:\.\s*)?(?:ArmoredStudio\.)?processing\.rvc\b|import\s+.*audio_intelligence|ArmoredStudio[\\/]+(?:runtime[\\/]+rvc|analysis[\\/]+audio_intelligence|processing[\\/]+rvc)|ArmoredStudio[\\/]+assets[\\/]+efeitosonoro\.wav)'
$StudioPython = @(
    Get-ChildItem -LiteralPath $StudioRoot -Filter "*.py" -File -Recurse |
    Where-Object {
        $_.FullName -notmatch '[\\/](?:__pycache__|\.venv|venv|tests?)[\\/]'
    }
)
$LegacyHits = @(
    foreach ($File in $StudioPython) {
        Select-String -LiteralPath $File.FullName -Pattern $LegacyExecutionPattern |
            ForEach-Object {
                [pscustomobject]@{
                    File = (Get-PortableRelativePath -Path $_.Path)
                    Line = $_.LineNumber
                    Match = $_.Line.Trim()
                }
            }
    }
)

# Audio DSP policy must stay in Speech. Studio's speech_handoff only validates
# metadata, and visual_finalizer only applies the supplied graph to the final render.
$AudioPolicyPattern = '(?i)(\bamix\s*=|\bloudnorm\b|\bafade\s*=|\badelay\s*=|\bapad\s*=|\bsilencedetect\b|\baudio-separator\b|\bRVCInference\b|\bconverter_voz\s*\()'
$UnexpectedPolicyHits = @(
    foreach ($File in $StudioPython) {
        $Relative = (Get-PortableRelativePath -Path $File.FullName).Substring($StudioRoot.Length - $RepoRoot.Length).TrimStart([char]92, [char]47).Replace('\', '/')
        if ($Relative -in @("speech_handoff.py", "processing/visual_finalizer.py")) {
            continue
        }
        Select-String -LiteralPath $File.FullName -Pattern $AudioPolicyPattern |
            ForEach-Object {
                [pscustomobject]@{
                    File = [System.IO.Path]::GetRelativePath($RepoRoot, $_.Path)
                    Line = $_.LineNumber
                    Match = $_.Line.Trim()
                }
            }
    }
)

Write-Output ("STUDIO_PYTHON_FILES=" + $StudioPython.Count)
Write-Output ("LEGACY_AUDIO_EXECUTION_REFERENCES=" + $LegacyHits.Count)
foreach ($Hit in $LegacyHits) {
    Write-Output ("LEGACY_REFERENCE={0}:{1}: {2}" -f $Hit.File, $Hit.Line, $Hit.Match)
}
Write-Output ("UNEXPECTED_STUDIO_AUDIO_POLICY_REFERENCES=" + $UnexpectedPolicyHits.Count)
foreach ($Hit in $UnexpectedPolicyHits) {
    Write-Output ("AUDIO_POLICY_REFERENCE={0}:{1}: {2}" -f $Hit.File, $Hit.Line, $Hit.Match)
}

$LegacyModules = @(
    "ArmoredStudio\analysis\audio_intelligence.py",
    "ArmoredStudio\processing\rvc.py",
    "ArmoredStudio\processing\finalizer.py"
)
foreach ($Relative in $LegacyModules) {
    $Exists = Test-Path -LiteralPath (Join-Path $RepoRoot $Relative) -PathType Leaf
    Write-Output ("LEGACY_MODULE_{0}={1}" -f ($Relative.Replace('\', '/')), $(if ($Exists) { "PRESENT" } else { "ABSENT" }))
}

$LegacyRvc = Join-Path $StudioRoot "runtime\rvc"
$SpeechModelsRoot = Join-Path $SpeechRoot "runtime\rvc\models"
$LegacyEffect = Join-Path $StudioRoot "assets\efeitosonoro.wav"
$SpeechEffect = Join-Path $SpeechRoot "assets\efeitosonoro.wav"
$Banner = Join-Path $StudioRoot "assets\banner.png"

if (Test-Path -LiteralPath $LegacyRvc -PathType Container) {
    $LegacyFiles = @(Get-ChildItem -LiteralPath $LegacyRvc -File -Recurse -Force)
    $LegacyBytes = [long](($LegacyFiles | Measure-Object -Property Length -Sum).Sum)
    Write-Output ("LEGACY_STUDIO_RVC_ASSET_FILES={0}; BYTES={1}; PRESERVED=True" -f $LegacyFiles.Count, $LegacyBytes)
} else {
    Write-Output "LEGACY_STUDIO_RVC_ASSET_FILES=0; PRESERVED=True"
}

$SpeechModelFiles = @()
if (Test-Path -LiteralPath $SpeechModelsRoot -PathType Container) {
    $SpeechModelFiles = @(
        Get-ChildItem -LiteralPath $SpeechModelsRoot -File -Recurse -Force |
        Where-Object { $_.Extension -in @(".pth", ".index") }
    )
}
$PthCount = @($SpeechModelFiles | Where-Object Extension -eq ".pth").Count
$IndexCount = @($SpeechModelFiles | Where-Object Extension -eq ".index").Count
Write-Output ("SPEECH_RVC_MODEL_FILES={0}; PTH={1}; INDEX={2}" -f $SpeechModelFiles.Count, $PthCount, $IndexCount)
Write-Output ("SPEECH_EFFECT_ASSET_PRESENT=" + (Test-Path -LiteralPath $SpeechEffect -PathType Leaf))
Write-Output ("LEGACY_STUDIO_EFFECT_ASSET_PRESENT=" + (Test-Path -LiteralPath $LegacyEffect -PathType Leaf))
Write-Output ("VISUAL_STUDIO_BANNER_PRESENT=" + (Test-Path -LiteralPath $Banner -PathType Leaf))

if ($LegacyHits.Count -gt 0 -or $UnexpectedPolicyHits.Count -gt 0) {
    Write-Output "STATIC_AUDIO_OWNERSHIP_AUDIT=FAIL"
    exit 3
}

Write-Output "STATIC_AUDIO_OWNERSHIP_AUDIT=PASS"
Write-Output "Read-only audit only. No modules, model/index files, assets, or generated results were changed or deleted."
