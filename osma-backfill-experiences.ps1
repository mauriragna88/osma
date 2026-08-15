#Requires -Version 5.1
<#
.SYNOPSIS
ARNES BACKFILL - Convierte observaciones de "quest-outcome" en experiencias OSMA V5
validadas y deduplicadas (una por quest), sin poblar ruido de smoke-test.

Repara el gap detectado: la capa V5 (experiences/cues/patterns) estaba vacia porque
el CLI harness solo escribia observaciones (V1-V4) y el hook PI (argos-learning.ts)
nunca corria en este proyecto. Este script hace el backfill controlado desde los
outcomes reales ya presentes en arnes.db.

Uso:  .\osma-backfill-experiences.ps1 [-DryRun] [-Force]
      -DryRun: solo reporta lo que haria (no escribe)
      -Force : registra aunque el quest ya tenga experiencia (por defecto respeta dedup)
#>
[CmdletBinding()]
param([switch]$DryRun, [switch]$Force)

$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$ArnesDir = Join-Path $Root '.arnes'
$DbPath = Join-Path $ArnesDir 'arnes.db'
$MemCli = Join-Path $PSScriptRoot 'osma-memory.ps1'

if (-not (Test-Path $DbPath)) { Write-Host "[!] No hay arnes.db en $ArnesDir" -ForegroundColor Yellow; exit 1 }

# ---- 1. Leer observaciones de quest-outcome + quests con experiencia (python helper) ----
$SupportPy = Join-Path $PSScriptRoot 'osma-backfill-support.py'
$out = (& python $SupportPy $DbPath obs 2>$null | Out-String).Trim()
$obs = if ($out) { @($out | ConvertFrom-Json) } else { @() }
$out2 = (& python $SupportPy $DbPath existing 2>$null | Out-String).Trim()
$existing = if ($out2) { @($out2 | ConvertFrom-Json) } else { @() }

Write-Host ''
Write-Host '  ARNES - BACKFILL EXPERIENCIAS OSMA V5' -ForegroundColor Cyan
Write-Host ("  Observaciones quest-outcome encontradas: {0} (dedup -> {1})" -f ($obs | Measure-Object).Count, ($obs | Measure-Object).Count) -ForegroundColor White
Write-Host ("  Quests con experiencia existente: {0}" -f ($existing | Measure-Object).Count) -ForegroundColor White
Write-Host ''

$created = 0; $skipped = 0
foreach ($o in $obs) {
    if ($existing -contains $o.quest -and -not $Force) {
        Write-Host ("  [--] {0} ya tiene experiencia (usa -Force para re-registrar)" -f $o.quest) -ForegroundColor DarkGray
        $skipped++; continue
    }
    $reward = if ($o.verdict -eq 'PASS') { 0.9 } elseif ($o.verdict -eq 'FAIL_PARTIAL') { 0.3 } else { -0.8 }
    Write-Host ("  [>>] {0} ({1}) reward={2}" -f $o.quest, $o.agent, $reward) -ForegroundColor Yellow
    if ($DryRun) { $created++; continue }
    $summary = $o.content; if ($summary.Length -gt 240) { $summary = $summary.Substring(0,240) + '...' }
    $null = & $MemCli experience -ExperienceAction record `
        -Situation ("Quest: {0}" -f $o.quest) `
        -Reasoning ("Agente: {0}. Quest procesado por el harness ARNES." -f $o.agent) `
        -Conclusion ("Veredicto: {0}" -f $o.verdict) `
        -Action ("Quest {0} completado." -f $o.quest) `
        -Outcome $summary `
        -Reward $reward `
        -Agent $o.agent -Project (Split-Path $Root -Leaf) -QuestId $o.quest -Quiet 2>$null
    $created++
}

Write-Host ''
Write-Host ("  [OK] Backfill - creadas: {0}, omitidas (dedup): {1}" -f $created, $skipped) -ForegroundColor Green
if ($DryRun) { Write-Host '  (modo DryRun - no se escribio nada)' -ForegroundColor DarkGray }
