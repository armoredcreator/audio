[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$LegacyRvcRoot = Join-Path $RepoRoot "ArmoredStudio\runtime\rvc"
$SpeechRvcRoot = Join-Path $RepoRoot "ArmoredSpeech\runtime\rvc"
$LegacyModels = Join-Path $LegacyRvcRoot "models"
$SpeechModels = Join-Path $SpeechRvcRoot "models"
$LegacyEffect = Join-Path $RepoRoot "ArmoredStudio\assets\efeitosonoro.wav"
$SpeechAssets = Join-Path $RepoRoot "ArmoredSpeech\assets"
$SpeechEffect = Join-Path $SpeechAssets "efeitosonoro.wav"

function Get-FileHashMap {
    param([Parameter(Mandatory)][string]$Root)

    $Result = [System.Collections.Generic.SortedDictionary[string,string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    Get-ChildItem -LiteralPath $Root -File -Recurse -Force |
        ForEach-Object {
            $Relative = $_.FullName.Substring($Root.Length).TrimStart('\','/')
            $Result[$Relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    return $Result
}

function Copy-VerifiedTreeWithoutOverwrite {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Pasta de origem não encontrada: $Source"
    }

    $SourceFiles = @(Get-ChildItem -LiteralPath $Source -File -Recurse -Force)
    if ($SourceFiles.Count -eq 0) {
        throw "A pasta de origem não contém arquivos: $Source"
    }

    if (Test-Path -LiteralPath $Destination -PathType Container) {
        $SourceMap = Get-FileHashMap -Root $Source
        $DestinationMap = Get-FileHashMap -Root $Destination
        $Same = $SourceMap.Count -eq $DestinationMap.Count
        if ($Same) {
            foreach ($Key in $SourceMap.Keys) {
                if (-not $DestinationMap.ContainsKey($Key) -or $DestinationMap[$Key] -ne $SourceMap[$Key]) {
                    $Same = $false
                    break
                }
            }
        }
        if (-not $Same) {
            throw "Destino já existe com conteúdo diferente; nada será sobrescrito: $Destination"
        }
        Write-Host "RVC_MODELS_JA_MIGRADOS=True" -ForegroundColor Green
        return $SourceFiles.Count
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
    $SourceMapAfter = Get-FileHashMap -Root $Source
    $DestinationMapAfter = Get-FileHashMap -Root $Destination
    if ($SourceMapAfter.Count -ne $DestinationMapAfter.Count) {
        throw "Quantidade de arquivos diverge após copiar RVC: $Destination"
    }
    foreach ($Key in $SourceMapAfter.Keys) {
        if (-not $DestinationMapAfter.ContainsKey($Key) -or $DestinationMapAfter[$Key] -ne $SourceMapAfter[$Key]) {
            throw "SHA-256 não confere após copiar RVC: $Key"
        }
    }
    Write-Host "RVC_MODELS_COPIADOS_E_VERIFICADOS=True" -ForegroundColor Green
    return $SourceFiles.Count
}

if ($env:ARMORED_RVC_ROOT) {
    $ConfiguredRoot = [System.IO.Path]::GetFullPath($env:ARMORED_RVC_ROOT)
    $ExpectedLegacyRoot = [System.IO.Path]::GetFullPath($LegacyRvcRoot)
    $ExpectedSpeechRoot = [System.IO.Path]::GetFullPath($SpeechRvcRoot)

    # ARMORED_RVC_ROOT describes the runtime location, not the source folder.
    # Both known canonical roots are safe during the copy-only migration:
    # legacy Studio root is the explicit source; Speech root is the final owner.
    # Any third/custom path remains blocked so the script cannot guess assets.
    $IsCanonicalRoot = (
        $ConfiguredRoot.Equals($ExpectedLegacyRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $ConfiguredRoot.Equals($ExpectedSpeechRoot, [System.StringComparison]::OrdinalIgnoreCase)
    )
    if (-not $IsCanonicalRoot) {
        throw "ARMORED_RVC_ROOT não corresponde a nenhuma raiz canônica. Migração bloqueada: $ConfiguredRoot. Permitidos: $ExpectedLegacyRoot ou $ExpectedSpeechRoot"
    }
}

Write-Host "=== MIGRAÇÃO LOCAL DOS ATIVOS PARA ARMORED SPEECH ===" -ForegroundColor Cyan
Write-Host "Origem dos modelos: $LegacyModels"
Write-Host "Destino dos modelos: $SpeechModels"
Write-Host "Origem do efeito:   $LegacyEffect"
Write-Host "Destino do efeito:  $SpeechEffect"
Write-Host "Esta operação copia e verifica SHA-256. Não apaga nem altera a origem." -ForegroundColor Yellow

if (-not (Test-Path -LiteralPath $LegacyEffect -PathType Leaf)) {
    throw "Efeito sonoro da origem não encontrado: $LegacyEffect"
}
if (-not (Test-Path -LiteralPath $LegacyModels -PathType Container)) {
    throw "Modelos RVC da origem não encontrados: $LegacyModels"
}

$SpeechModelCount = Copy-VerifiedTreeWithoutOverwrite -Source $LegacyModels -Destination $SpeechModels

$LegacyEffectHash = (Get-FileHash -LiteralPath $LegacyEffect -Algorithm SHA256).Hash.ToLowerInvariant()
if (Test-Path -LiteralPath $SpeechEffect -PathType Leaf) {
    $SpeechEffectHash = (Get-FileHash -LiteralPath $SpeechEffect -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($SpeechEffectHash -ne $LegacyEffectHash) {
        throw "O efeito já existe no Speech com SHA-256 diferente. Nenhum arquivo foi sobrescrito."
    }
    Write-Host "EFEITO_JA_MIGRADO=True" -ForegroundColor Green
}
else {
    New-Item -ItemType Directory -Path $SpeechAssets -Force | Out-Null
    Copy-Item -LiteralPath $LegacyEffect -Destination $SpeechEffect
    $SpeechEffectHash = (Get-FileHash -LiteralPath $SpeechEffect -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($SpeechEffectHash -ne $LegacyEffectHash) {
        throw "SHA-256 do efeito não confere após a cópia."
    }
    Write-Host "EFEITO_COPIADO_E_VERIFICADO=True" -ForegroundColor Green
}

$MelodyModels = @(Get-ChildItem -LiteralPath (Join-Path $SpeechModels "melody") -Filter "*.pth" -File -ErrorAction SilentlyContinue)
if ($MelodyModels.Count -eq 0) {
    throw "Não encontrei modelo .pth em $SpeechModels\melody"
}
$MelodyIndexes = @(Get-ChildItem -LiteralPath (Join-Path $SpeechModels "melody") -Filter "*.index" -File -ErrorAction SilentlyContinue)

Write-Host ""
Write-Host "MIGRACAO_SPEECH_ASSETS=SUCCESS" -ForegroundColor Green
Write-Host "RVC_MODEL_FILES=$SpeechModelCount"
Write-Host "MELODY_MODELS=$($MelodyModels.Count)"
Write-Host "MELODY_INDEXES=$($MelodyIndexes.Count)"
Write-Host "EFFECT_SHA256=$SpeechEffectHash"
Write-Host "LEGACY_ASSETS_PRESERVED=True"
Write-Host "Não apague ainda ArmoredStudio\runtime\rvc nem o efeito antigo; só depois do E2E real com os novos caminhos."
