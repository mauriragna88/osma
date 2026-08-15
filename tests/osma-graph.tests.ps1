# ============================================================
# OSMA - verificacion del grafo de relaciones (osma-graph)
#
# Crea un proyecto temporal, registra edges con el CLI real y
# verifica neighbors/path/stats contra la base SQLite (salida
# humanizada del CLI).
#
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File tests/osma-graph.tests.ps1
# Exit: 0 = PASS | 1 = FAIL
# ============================================================
$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$GraphCli = Join-Path $Root 'osma-graph.ps1'
$work = Join-Path $Root ('.osma-graph-test-' + [guid]::NewGuid().ToString('N'))
$workArnes = Join-Path $work '.arnes'

function Assert-That {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "Assertion failed: $Message" }
}

New-Item -ItemType Directory -Path $workArnes -Force | Out-Null
try {
    Push-Location $work
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $GraphCli add -NodeA 'Login.tsx' -NodeB 'zod' -Relation 'uses' -Agent 'vivi' | Out-Null
        Assert-That ($LASTEXITCODE -eq 0) 'add edge 1 exit 0'
        & powershell -NoProfile -ExecutionPolicy Bypass -File $GraphCli add -NodeA 'zod' -NodeB 'validation.ts' -Relation 'imports' -Agent 'ansem' | Out-Null
        Assert-That ($LASTEXITCODE -eq 0) 'add edge 2 exit 0'
        & powershell -NoProfile -ExecutionPolicy Bypass -File $GraphCli add -NodeA 'Login.tsx' -NodeB 'auth-api' -Relation 'calls' -Agent 'vivi' | Out-Null
        Assert-That ($LASTEXITCODE -eq 0) 'add edge 3 exit 0'

        $neighbors = (& powershell -NoProfile -ExecutionPolicy Bypass -File $GraphCli neighbors -Node 'Login.tsx' -Depth 1 | Out-String)
        Assert-That ($neighbors -match "Vecinos de 'Login\.tsx' \(depth=1\): [2-9]") "neighbors de Login.tsx >= 2: $neighbors"

        $path = (& powershell -NoProfile -ExecutionPolicy Bypass -File $GraphCli path -Start 'Login.tsx' -End 'validation.ts' | Out-String)
        Assert-That ($path -match 'Camino: Login\.tsx -> validation\.ts') "camino Login.tsx -> validation.ts existe: $path"

        $stats = (& powershell -NoProfile -ExecutionPolicy Bypass -File $GraphCli stats | Out-String)
        Assert-That ($stats -match 'Nodos:\s+[3-9]\d*') "stats muestra >= 3 nodos: $stats"
        Assert-That ($stats -match 'Edges:\s+[3-9]\d*') "stats muestra >= 3 edges: $stats"

        Write-Output 'PASS osma-graph: add/neighbors/path/stats contra SQLite real'
        exit 0
    } finally { Pop-Location }
} finally {
    if (Test-Path -LiteralPath $work) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}
