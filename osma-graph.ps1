#Requires -Version 5.1
<#
.SYNOPSIS
ARNES GRAPH - CLI del grafo de relaciones (arnes.db edges)

.DESCRIPTION
El NEOCORTEX del harness: guarda RELACIONES entre nodos (componentes, librerias,
agentes, modulos). Responde "quien toco X", "que depende de Y", "hay camino entre A y B".

.EXAMPLE
.\osma-graph.ps1 add -NodeA Login.tsx -NodeB zod -Relation uses -Agent vivi
.\osma-graph.ps1 query -Node Login.tsx
.\osma-graph.ps1 neighbors -Node Login.tsx -Depth 2
.\osma-graph.ps1 path -Start Login.tsx -End ansem
.\osma-graph.ps1 stats
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('add', 'query', 'neighbors', 'path', 'stats')]
    [string]$Command,

    [string]$NodeA,
    [string]$NodeB,
    [string]$Relation = 'related',
    [string]$Node,
    [string]$Start,
    [string]$End,
    [string]$Agent,
    [string]$QuestId,
    [int]$Depth = 1,
    [int]$MaxDepth = 6,
    [string]$Json
)

$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$ArnesDir = Join-Path (Get-Location) '.arnes'
$DbPath = Join-Path $ArnesDir 'arnes.db'
$BrainScript = Join-Path $PSScriptRoot 'osma_brain.py'

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host '[ERROR] Python no encontrado. El arnes necesita Python 3.14+.' -ForegroundColor Red
    exit 1
}

switch ($Command) {
    'add' {
        if (-not $NodeA -or -not $NodeB) { throw 'Falta -NodeA y -NodeB' }
        $data = @{
            node_a   = $NodeA
            node_b   = $NodeB
            relation = $Relation
            agent    = $Agent
            quest_id = $QuestId
        } | ConvertTo-Json -Compress
        $tmp = Join-Path $env:TEMP ("osma-graph-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $data -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath edge "-" 2>$null
            Write-Host ("  [OK] Edge registrado: {0} -[{1}]-> {2}" -f $NodeA, $Relation, $NodeB) -ForegroundColor Green
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'query' {
        if (-not $Node) { throw 'Falta -Node' }
        $out = & $python $BrainScript $DbPath edges $Node 2>$null
        $results = $out | Out-String | ConvertFrom-Json
        Write-Host ("  Relaciones de '{0}': {1}" -f $Node, $results.Count) -ForegroundColor Cyan
        foreach ($e in ($results | Select-Object -First 20)) {
            Write-Host ("  {0} -[{1}]-> {2} ({3})" -f $e.node_a, $e.relation, $e.node_b, $e.agent) -ForegroundColor White
        }
    }
    'neighbors' {
        if (-not $Node) { throw 'Falta -Node' }
        $out = & $python $BrainScript $DbPath neighbors $Node $Depth 2>$null
        $results = $out | Out-String | ConvertFrom-Json
        Write-Host ("  Vecinos de '{0}' (depth={1}): {2}" -f $Node, $Depth, $results.Count) -ForegroundColor Cyan
        foreach ($n in ($results | Select-Object -First 20)) {
            $indent = '    ' * ([Math]::Max(0, $n.depth - 1))
            Write-Host ("  {0}{1} -[{2}]-> {3}" -f $indent, $n.from, $n.relation, $n.to) -ForegroundColor White
        }
    }
    'path' {
        if (-not $Start -or -not $End) { throw 'Falta -Start y -End' }
        $out = & $python $BrainScript $DbPath path $Start $End $MaxDepth 2>$null
        $path = $out | Out-String | ConvertFrom-Json
        if (-not $path -or $path.Count -eq 0) {
            Write-Host ("  No hay camino entre '{0}' y '{1}' (max_depth={2})" -f $Start, $End, $MaxDepth) -ForegroundColor Yellow
            exit 0
        }
        Write-Host ("  Camino: {0} -> {1} ({2} pasos)" -f $Start, $End, $path.Count) -ForegroundColor Cyan
        Write-Host ("  {0}" -f $Start) -ForegroundColor White
        foreach ($step in $path) {
            Write-Host ("    -[{0}]-> {1}" -f $step.relation, $step.to) -ForegroundColor Green
        }
    }
    'stats' {
        $out = & $python $BrainScript $DbPath graph-stats 2>$null
        $s = $out | Out-String | ConvertFrom-Json
        Write-Host ''
        Write-Host '  ARNES GRAPH - STATS' -ForegroundColor Cyan
        Write-Host ("  {0,-18} {1}" -f 'Nodos:', $s.nodes) -ForegroundColor White
        Write-Host ("  {0,-18} {1}" -f 'Edges:', $s.edges) -ForegroundColor White
        Write-Host ("  {0,-18}" -f 'Relaciones:') -ForegroundColor White
        foreach ($r in $s.relations.PSObject.Properties) {
            Write-Host ("    {0}: {1}" -f $r.Name, $r.Value) -ForegroundColor DarkGray
        }
        Write-Host ("  {0,-18}" -f 'Agentes activos:') -ForegroundColor White
        foreach ($a in $s.agents_active.PSObject.Properties) {
            Write-Host ("    {0}: {1} edges" -f $a.Name, $a.Value) -ForegroundColor DarkGray
        }
    }
}
