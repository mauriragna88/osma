#Requires -Version 5.1
<#
.SYNOPSIS
ARNES SCAN-PROJECTS - Auto-identificacion de proyectos con ARGOS/OSMA

.DESCRIPTION
Escanea una carpeta base (default: ~/Documents/GitHub) y detecta en cada
proyecto si tiene ARNES ARGOS + memoria OSMA. Esto responde la pregunta
"en todos los proyectos: tenemos argos y osma?" de forma automatica.

Criterios de deteccion (un proyecto es ARGOS/OSMA si cumple >=2):
  - .arnes/ existe
  - .arnes/arnes.db existe (memoria OSMA real)
  - .arnes/config.json con "player.name" == Atlas (harness configurado)
  - .arnes/quest-ledger.json existe (hay historial de quests)
  - opencode.json con agente "atlas" (integracion opencode)
  - AGENTS.md contiene "Atlas" y "OSMA" (integracion por instrucciones)

.EXAMPLE
.\osma-scan-projects.ps1                          # escanea ~/Documents/GitHub
.\osma-scan-projects.ps1 -BaseDir D:\repos        # escanea otra carpeta
.\osma-scan-projects.ps1 -Json                    # salida JSON para tooling
.\osma-scan-projects.ps1 -OnlyArnes               # solo proyectos ARGOS/OSMA
#>
[CmdletBinding()]
param(
    [string]$BaseDir = '',
    [switch]$Json,
    [switch]$OnlyArnes,
    [int]$MaxDepth = 2
)

$ErrorActionPreference = 'SilentlyContinue'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')

# === Resolver base: parametro > ~/Documents/GitHub > raiz del repo ===
if (-not $BaseDir) {
    $candidates = @(
        (Join-Path $env:USERPROFILE 'Documents\GitHub'),
        (Join-Path $env:USERPROFILE 'Documents\Proyectos'),
        (Join-Path $env:USERPROFILE 'Documents\projects'),
        (Split-Path $Root -Parent)   # la carpeta que contiene este repo
    )
    $BaseDir = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $BaseDir -or -not (Test-Path $BaseDir)) {
    Write-Host "[!] Carpeta base no existe: $BaseDir" -ForegroundColor Red
    Write-Host '    Usa: -BaseDir <ruta>' -ForegroundColor Yellow
    exit 1
}
$BaseDir = (Resolve-Path $BaseDir).Path

# === Criterio: es un proyecto? (tiene .git o archivos de proyecto, no es node_modules) ===
function Test-ProjectDir {
    param([string]$Path)
    if (Test-Path (Join-Path $Path '.git')) { return $true }
    foreach ($marker in @('package.json', 'opencode.json', 'AGENTS.md', '.arnes')) {
        if (Test-Path (Join-Path $Path $marker)) { return $true }
    }
    return $false
}

# === Detectar ARGOS/OSMA en un proyecto ===
function Get-ArnesStatus {
    param([string]$Path)
    $arnesDir = Join-Path $Path '.arnes'
    $markers = [ordered]@{}

    $hasArnesDir = Test-Path $arnesDir
    $markers.arnes_dir = $hasArnesDir

    $dbPath = Join-Path $arnesDir 'arnes.db'
    $hasDb = Test-Path $dbPath
    $markers.osma_db = $hasDb
    $schema = 0
    $obs = 0; $exps = 0; $cues = 0
    if ($hasDb) {
        try {
            $SupportPy = Join-Path $PSScriptRoot 'osma-scan-support.py'
            $res = (& python $SupportPy $dbPath 2>$null | Out-String).Trim() | ConvertFrom-Json
            if ($res) {
                $schema = [int]$res.schema; $obs = [int]$res.obs
                $exps = [int]$res.exps; $cues = [int]$res.cues
            }
        } catch {}
    }

    $cfgPath = Join-Path $arnesDir 'config.json'
    $hasAtlasCfg = $false
    if (Test-Path $cfgPath) {
        try {
            $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
            if ($cfg.player -and $cfg.player.name -eq 'Atlas') { $hasAtlasCfg = $true }
        } catch {}
    }
    $markers.atlas_config = $hasAtlasCfg

    $ledgerPath = Join-Path $arnesDir 'quest-ledger.json'
    $hasLedger = Test-Path $ledgerPath
    $markers.quest_ledger = $hasLedger
    $quests = 0
    if ($hasLedger) {
        try {
            $ledger = Get-Content $ledgerPath -Raw | ConvertFrom-Json
            $quests = @($ledger.quests).Count
        } catch {}
    }

    $ocPath = Join-Path $Path 'opencode.json'
    $hasOcAtlas = $false
    if (Test-Path $ocPath) {
        try {
            $oc = Get-Content $ocPath -Raw | ConvertFrom-Json
            if ($oc.agent.atlas) { $hasOcAtlas = $true }
        } catch {}
    }
    $markers.opencode_atlas = $hasOcAtlas

    $agentsPath = Join-Path $Path 'AGENTS.md'
    $hasAgentsOsma = $false
    if (Test-Path $agentsPath) {
        try {
            $txt = Get-Content $agentsPath -Raw
            if ($txt -match 'Atlas' -and $txt -match 'OSMA') { $hasAgentsOsma = $true }
        } catch {}
    }
    $markers.agents_osma = $hasAgentsOsma

    $score = 0
    foreach ($v in $markers.Values) { if ($v) { $score++ } }
    $isArnes = $score -ge 2

    return [ordered]@{
        path       = $Path
        name       = Split-Path $Path -Leaf
        is_arnes   = $isArnes
        score      = $score
        schema     = $schema
        obs        = $obs
        exps       = $exps
        cues       = $cues
        quests     = $quests
        markers    = $markers
    }
}

# === Escanear ===
$results = @()
$roots = @(Get-ChildItem $BaseDir -Directory -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -notmatch '^(node_modules|\.git|\.venv|bin|obj|dist|build)$' })
foreach ($d in $roots) {
    if (Test-ProjectDir $d.FullName) {
        $results += Get-ArnesStatus $d.FullName
    }
}

# === Salida ===
if ($Json) {
    $payload = [ordered]@{
        base_dir   = $BaseDir
        scanned_at = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')
        total      = $results.Count
        arnes      = @($results | Where-Object { $_.is_arnes })
        no_arnes   = @($results | Where-Object { -not $_.is_arnes })
    }
    Write-Output ($payload | ConvertTo-Json -Depth 8 -Compress)
    exit 0
}

Write-Host ''
Write-Host "  ARNES - SCAN DE PROYECTOS ($BaseDir)" -ForegroundColor Cyan
Write-Host '  =============================================' -ForegroundColor Cyan

$arnes = @($results | Where-Object { $_.is_arnes })
$noArnes = @($results | Where-Object { -not $_.is_arnes })

if ($OnlyArnes) { $results = $arnes }

foreach ($r in $results) {
    if ($r.is_arnes) {
        Write-Host ("  [ARGOS] {0}" -f $r.name) -ForegroundColor Green
        Write-Host ("          OSMA v{0} | obs={1} exp={2} cues={3} quests={4} | path: {5}" -f $r.schema, $r.obs, $r.exps, $r.cues, $r.quests, $r.path) -ForegroundColor White
    } else {
        Write-Host ("  [  --  ] {0}  (sin ARGOS/OSMA)" -f $r.name) -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host ("  Total proyectos: {0} | con ARNES ARGOS/OSMA: {1} | sin: {2}" -f $results.Count, $arnes.Count, $noArnes.Count) -ForegroundColor Yellow
if (-not $OnlyArnes) {
    Write-Host '  Tip: -OnlyArnes para ver solo los que tienen ARGOS/OSMA; -Json para tooling.' -ForegroundColor DarkGray
}
