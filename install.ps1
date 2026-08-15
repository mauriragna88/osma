#Requires -Version 5.1
<#
.SYNOPSIS
OSMA INSTALL - instala el motor OSMA en ~/.config/arnes/osma (global, una vez por maquina)

.DESCRIPTION
Copia osma_brain.py + CLIs a ~/.config/arnes/osma para que cualquier harness
(ARGOS, opencode, pi, claude, codex, dsh) los resuelva sin depender del repo.
Uninstall: .\install.ps1 -Remove
#>
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$Src = $PSScriptRoot
$Dst = Join-Path $env:USERPROFILE '.config\arnes\osma'

if ($Remove) {
    if (Test-Path $Dst) { Remove-Item $Dst -Recurse -Force }
    Write-Output "  [OK] OSMA removido de $Dst"
    exit 0
}

New-Item -ItemType Directory -Path $Dst -Force | Out-Null
$files = @(
    'osma_brain.py', 'osma-memory.ps1', 'osma-graph.ps1',
    'osma-backfill-experiences.ps1', 'osma-backfill-support.py',
    'osma-scan-projects.ps1', 'osma-scan-support.py'
)
foreach ($f in $files) {
    Copy-Item (Join-Path $Src $f) (Join-Path $Dst $f) -Force
}
Write-Output "  [OK] OSMA instalado en $Dst"
Write-Output "   Resolucion: env ARNES_OSMA_ROOT -> aqui -> fallback .\cli\"
foreach ($f in $files) { Write-Output ("    " + $f) }