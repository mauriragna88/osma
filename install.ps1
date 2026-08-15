#Requires -Version 5.1
<#
.SYNOPSIS
OSMA INSTALL - instala el motor OSMA en ~/.config/arnes/osma (global, una vez por maquina)

.DESCRIPTION
Instala osma_brain.py + CLIs en ~/.config/arnes/osma para que cualquier harness
(ARGOS, opencode, pi, claude, codex, dsh) los resuelva sin depender del repo.

Funciona de DOS formas:
  1. Clon local:   .\install.ps1                    (copia los archivos del repo)
  2. Directa:      irm https://.../install.ps1 | iex (los descarga desde GitHub raw)

Uninstall: .\install.ps1 -Remove

Autor: Mauri Ragna (https://github.com/mauriragna88)
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [string]$Owner = 'mauriragna88',
    [string]$Repo = 'osma',
    [string]$Ref = 'main'
)

$ErrorActionPreference = 'Stop'
$Dst = Join-Path $env:USERPROFILE '.config\arnes\osma'
$Files = @(
    'osma_brain.py', 'osma-memory.ps1', 'osma-graph.ps1',
    'osma-backfill-experiences.ps1', 'osma-backfill-support.py',
    'osma-scan-projects.ps1', 'osma-scan-support.py'
)

if ($Remove) {
    if (Test-Path $Dst) { Remove-Item $Dst -Recurse -Force }
    Write-Output "  [OK] OSMA removido de $Dst"
    exit 0
}

New-Item -ItemType Directory -Path $Dst -Force | Out-Null

# Determinar origen: clone local (PSScriptRoot) o descarga desde GitHub raw
$local = $PSScriptRoot

function Get-OsmaFile {
    param([string]$Name)
    # si hay clone local con el archivo, copiarlo; si no, descargarlo
    if ($local -and (Test-Path (Join-Path $local $Name))) {
        Copy-Item (Join-Path $local $Name) (Join-Path $Dst $Name) -Force
        return "(local) $Name"
    }
    # descargar desde raw.githubusercontent.com
    $url = "https://raw.githubusercontent.com/$Owner/$Repo/$Ref/$Name"
    try {
        Invoke-WebRequest -Uri $url -OutFile (Join-Path $Dst $Name) -UseBasicParsing
    } catch {
        Write-Output "  [!] No se pudo obtener $Name desde $url" -ForegroundColor Yellow
        return $null
    }
    return "(github) $Name"
}

Write-Output "  [OK] Instalando OSMA en $Dst"
foreach ($f in $Files) {
    $r = Get-OsmaFile $f
    Write-Output ("   " + $r)
}

Write-Output "  [OK] OSMA instalado."
Write-Output "   La memoria se guarda por proyecto en: <proyecto>/.arnes/arnes.db"
Write-Output "   Resolucion del motor: env ARNES_OSMA_ROOT -> aqui -> ../osma -> fallback ./cli/"
Write-Output ""
Write-Output "   Siguiente: clona ARGOS (el harness) -> https://github.com/mauriragna88/argos"
