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

Write-Host "=== VALIDAR / MIGRAR ATIVOS LOCAIS PARA ARMORED SPEECH ===" -ForegroundColor Cyan
Write-Host "Origem legada opcional: $LegacyModels"
Write-Host "Destino canônico:       $SpeechModels"
Write-Host "Efeito legado opcional: $LegacyEffect"
Write-Host "Efeito canônico:        $SpeechEffect"
Write-Host "Esta operação nunca executa RVC no Studio e nunca apaga arquivos."

$LegacyModelsPresent = Test-Path -LiteralPath $LegacyModels -PathType Container
$SpeechModelsPresent = Test-Path -LiteralPath $SpeechModels -PathType Container
$LegacyEffectPresent = Test-Path -LiteralPath $LegacyEffect -PathType Leaf
$SpeechEffectPresent = Test-Path -LiteralPath $SpeechEffect -PathType Leaf

if ($LegacyModelsPresent) {
    $SpeechModelCount = Copy-VerifiedTreeWithoutOverwrite -Source $LegacyModels -Destination $SpeechModels
}
elseif ($SpeechModelsPresent) {
    $SpeechModelFiles = @(Get-ChildItem -LiteralPath $SpeechModels -File -Recurse -Force)
    if ($SpeechModelFiles.Count -eq 0) {
        throw "A pasta canônica de modelos do Speech está vazia: $SpeechModels"
    }
    $SpeechModelCount = $SpeechModelFiles.Count
    Write-Host "RVC_MODELS_ALREADY_CANONICAL_IN_SPEECH=True" -ForegroundColor Green
}
else {
    throw "Modelos ausentes nas duas localizações. Restaure modelos/índices de um backup confiável diretamente em $SpeechModels; nenhuma cópia ou exclusão foi tentada."
}

if ($LegacyEffectPresent) {
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
}
elseif (Test-Path -LiteralPath $SpeechEffect -PathType Leaf) {
    $SpeechEffectHash = (Get-FileHash -LiteralPath $SpeechEffect -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "EFEITO_JA_CANONICO_NO_SPEECH=True" -ForegroundColor Green
}
else {
    throw "Efeito sonoro ausente nas duas localizações. Restaure o arquivo confiável diretamente em $SpeechEffect; nenhuma exclusão foi tentada."
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
Write-Host "LEGACY_MODELS_SOURCE_PRESENT=$LegacyModelsPresent"
Write-Host "LEGACY_EFFECT_SOURCE_PRESENT=$LegacyEffectPresent"
Write-Host "STUDIO_AUDIO_RUNTIME_FALLBACK=False" -ForegroundColor Green
Write-Host "O runtime canônico do projeto é ArmoredSpeech; o caminho legado só serve como origem opcional de cópia verificada."
Write-Host "Não apague ainda ArmoredStudio\runtime\rvc nem o efeito antigo; só depois do E2E real com os novos caminhos."
