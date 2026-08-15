#Requires -Version 5.1
<#
.SYNOPSIS
ARNES MEMORY - CLI de memoria cerebral (arnes.db SQLite+FTS5)

.DESCRIPTION
El cerebro del harness. Guarda, busca y exporta recuerdos de los agentes.
100% local - Python + SQLite nativo. CERO dependencias externas.

.EXAMPLE
.\osma-memory.ps1 init
.\osma-memory.ps1 save -Agent vivi -Topic vivi/ui-patterns -Type pattern -Content "User prefiere dark mode"
.\osma-memory.ps1 search -Query "dark mode" -Agent vivi
.\osma-memory.ps1 context
.\osma-memory.ps1 agent -Agent vivi
.\osma-memory.ps1 export
.\osma-memory.ps1 stats
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('init', 'save', 'search', 'recall', 'context', 'agent', 'get', 'update', 'reinforce', 'verify', 'reconsolidate', 'suggest-topic', 'revisions', 'compact', 'consolidate', 'consolidate-recent', 'checkpoint', 'capsule', 'continuity', 'cognitive-compact', 'aquest', 'atask', 'backup', 'export', 'import', 'stats', 'quest', 'quests', 'edge', 'edges', 'skill', 'route', 'reviews', 'experience', 'episode', 'patterns', 'analyze', 'osma-stats')]
    [string]$Command,

    [string]$Agent,
    [string]$Topic,
    [ValidateSet('bugfix', 'decision', 'pattern', 'discovery', 'preference', 'verdict', 'recommendation', 'action', 'session_summary')]
    [string]$Type = 'discovery',
    [string]$Content,
    [string]$Query,
    [string]$QuestId,
    [string]$Json,
    [switch]$Quiet,   # salida JSON capturable (para que el chat cargue la memoria)
    [switch]$Upsert,  # save: actualiza el topico si ya existe (en vez de duplicar)
    [int]$Score = 0,  # save: importancia 1-5 (5 = critico, aparece primero)
    [string[]]$Tags,  # save: etiquetas para filtrar (ej: -Tags schema,supabase)
    [string]$Tag,     # search/recall: filtrar por etiqueta
    [string]$Kind,    # save: working | episodic | semantic | procedural
    [double]$Confidence = -1,   # save: 0-1 (default segun tipo)
    [string]$Volatility = '',   # save: immutable | stable | slow | dynamic | ephemeral
    [string]$Evidence,          # save/verify/reinforce: JSON de fuentes
    [string]$Source,            # save: origen
    [string]$Verdict,           # verify: PASS | FAIL
    [int]$OlderThanDays = 30,  # compact: dias de antiguedad
    [int]$Hours = 24,          # consolidate-recent: horas recientes
    [int]$Id = 0,     # update/get/revisions/reinforce/verify/reconsolidate/capsule/continuity: id
    [switch]$Create,  # checkpoint/aquest: crear
    [switch]$List,    # checkpoint/aquest/atask: listar
    [string]$Goal,            # checkpoint
    [string]$Phase,           # checkpoint
    [string[]]$Completed,     # checkpoint: tareas completadas
    [string[]]$Pending,       # checkpoint: pendientes
    [string[]]$Files,         # checkpoint: archivos activos
    [string[]]$ModifiedFiles, # checkpoint: archivos modificados
    [int[]]$Decisions,        # checkpoint: ids de memoria criticos
    [string]$Skill,   # checkpoint: skill activa / skill: id
    [string]$SkillAction = '', # skill: register | exec | link | status | executions
    [string]$Stage,           # checkpoint: etapa de la skill
    [string[]]$Blockers,      # checkpoint
    [string[]]$Errors,        # checkpoint
    [string]$TestState,       # checkpoint
    [string]$BuildState,      # checkpoint
    [string]$GitState,        # checkpoint
    [string]$NextAction,      # checkpoint: SIGUIENTE ACCION EXACTA
    [string]$Risk,    # route: texto de riesgo adicional
    [string]$TaskId,  # atask: id de tarea (AUTH-03)
    [string]$Status,  # atask: nuevo status
    [int]$Attempts = 0,       # atask
    [int]$TokensUsed = 0,     # atask
    [string]$Summary,         # atask: resumen del resultado
    [string[]]$TaskDeps,      # atask: dependencias
    [string]$Acceptance,      # atask: criterios de aceptacion
    [int]$Limit = 20,
    [string]$OutDir,
    [string]$InDir,
    [switch]$Global  # Capa extra: memoria GLOBAL de patrones (~/.config/arnes/osma-global.db)
    # --- OSMA V5+ Experiencia (experience) ---
    ,[string]$Situation   # experience record: situacion / parte del recuerdo
    ,[string]$Reasoning   # experience record: razonamiento
    ,[string]$Conclusion  # experience record: conclusion
    ,[string]$Action      # experience record: accion tomada
    ,[string]$Outcome     # experience record: resultado real
    ,[double]$Reward = 0  # experience record: senal -1..1 (>=0.9 verified)
    ,[string]$Project     # experience record: nombre del proyecto
    ,[string]$Cues        # cue-search: pistas separadas por comas
    ,[ValidateSet('record','search','cues','stats','validate','analyze')]
    [string]$ExperienceAction = 'record'  # experience: subcomando
)

$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
# Memoria POR PROYECTO: la DB vive en .arnes de la carpeta donde trabajas (no global)
$ArnesDir = Join-Path (Get-Location) '.arnes'
$DbPath = Join-Path $ArnesDir 'arnes.db'
# Capa GLOBAL (opcional): ~/.config/arnes/osma-global.db
# Guarda patrones/lecciones reutilizables que cualquier proyecto consulta.
# No reemplaza la memoria local — la COMPLEMENTA.
if ($Global) {
    $GlobalArnesDir = Join-Path $env:USERPROFILE '.config\arnes'
    if (-not (Test-Path $GlobalArnesDir)) { New-Item -ItemType Directory -Path $GlobalArnesDir -Force | Out-Null }
    $DbPath = Join-Path $GlobalArnesDir 'osma-global.db'
}
$BrainScript = Join-Path $PSScriptRoot 'osma_brain.py'

# Verificar Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host '[ERROR] Python no encontrado. El arnes necesita Python 3.14+.' -ForegroundColor Red
    exit 1
}

# Cargar la config para obtener los agentes conocidos (init)
function Get-KnownAgents {
    $agents = @()
    $configPath = Join-Path $ArnesDir 'config.json'
    if (Test-Path $configPath) {
        try {
            $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
            foreach ($prop in $cfg.characters.PSObject.Properties) {
                $agents += [ordered]@{
                    id    = $prop.Name
                    name  = $prop.Value.name
                    class = $prop.Value.class
                    role  = $prop.Value.role
                    model = $prop.Value.model_opencode
                }
            }
        } catch {}
    }
    # Los 16 agentes del party (fallback completo si no hay config con characters)
    $allAgents = @(
        @{ id = 'atlas'; name = 'Atlas'; class = 'Player'; role = 'Orchestrator' },
        @{ id = 'vivi'; name = 'Vivi'; class = 'Mage'; role = 'Frontend' },
        @{ id = 'ansem'; name = 'Ansem'; class = 'Paladin'; role = 'Backend' },
        @{ id = 'kuja'; name = 'Kuja'; class = 'Rogue'; role = 'QA' },
        @{ id = 'eiko'; name = 'Eiko'; class = 'Cleric'; role = 'DevOps' },
        @{ id = 'amarant'; name = 'Amarant'; class = 'Monk'; role = 'Architecture' },
        @{ id = 'eremez'; name = 'Eremez'; class = 'Ranger'; role = 'Research' },
        @{ id = 'auron'; name = 'Auron'; class = 'Warden'; role = 'Security' },
        @{ id = 'bran'; name = 'Bran'; class = 'Seer'; role = 'Analyst' },
        @{ id = 'quina'; name = 'Quina'; class = 'Banker'; role = 'Tokens' },
        @{ id = 'varys'; name = 'Varys'; class = 'Spider'; role = 'Tracker' },
        @{ id = 'tywin'; name = 'Tywin'; class = 'Verifier'; role = 'Verifier' },
        @{ id = 'sam'; name = 'Sam'; class = 'Archivist'; role = 'Counselor' },
        @{ id = 'bard'; name = 'Bard'; class = 'Bard'; role = 'Improvement' },
        @{ id = 'tidus'; name = 'Tidus'; class = 'Warden'; role = 'Infrastructure' },
        @{ id = 'ragnarok'; name = 'Ragnarok'; class = 'Warden'; role = 'Procurement' }
    )
    foreach ($e in $allAgents) {
        if ($agents.id -notcontains $e.id) { $agents += $e }
    }
    return $agents
}

# Función helper: ejecutar python con stdin desde archivo temporal (evita encoding cp1252)
function Invoke-Brain {
    param([string]$CommandName, [string]$JsonData = '')
    if ($JsonData) {
        $tmp = Join-Path $env:TEMP ("arnes-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $JsonData -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath $CommandName "-" 2>$null
            return $out
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    } else {
        $out = & $python $BrainScript $DbPath $CommandName @($CommandArgs) 2>$null
        return $out
    }
}

switch ($Command) {
    'init' {
        $agentsJson = Get-KnownAgents | ConvertTo-Json -Depth 5 -Compress
        $tmp = Join-Path $env:TEMP ("arnes-init-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $agentsJson -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath init "-" 2>$null
            $stats = $out | Out-String | ConvertFrom-Json
            Write-Host ''
            Write-Host '  OSMA inicializado' -ForegroundColor Cyan
            Write-Host ("  {0,-18} {1}" -f 'Agentes:', $stats.agents) -ForegroundColor White
            Write-Host ("  {0,-18} {1}" -f 'Observaciones:', $stats.observations) -ForegroundColor White
            Write-Host ("  {0,-18} {1}" -f 'Quests:', $stats.quests) -ForegroundColor White
            Write-Host ("  {0,-18} {1}" -f 'DB:', $DbPath) -ForegroundColor DarkGray
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'save' {
        if (-not $Content) { throw 'Falta -Content' }
        $saveAgent = if ($Agent) { $Agent } else { 'atlas' }
        $saveTopic = if ($Topic) { $Topic } else { 'atlas/general' }
        $data = @{
            agent     = $saveAgent
            topic_key = $saveTopic
            type      = $Type
            content   = $Content
            quest_id  = $QuestId
            score     = $Score
            tags      = @($Tags)
            memory_kind = $(if ($Kind) { $Kind } else { $null })
            confidence  = $(if ($Confidence -ge 0) { $Confidence } else { $null })
            volatility  = $(if ($Volatility) { $Volatility } else { $null })
            evidence    = $(if ($Evidence) { $Evidence } else { $null })
            source      = $(if ($Source) { $Source } else { $null })
        } | ConvertTo-Json -Compress
        if ($Upsert) {
            $data = @{
                agent = $saveAgent; topic_key = $saveTopic; type = $Type
                content = $Content; quest_id = $QuestId; score = $Score
                tags = @($Tags); memory_kind = $(if ($Kind) { $Kind } else { $null })
                confidence = $(if ($Confidence -ge 0) { $Confidence } else { $null })
                volatility = $(if ($Volatility) { $Volatility } else { $null })
                evidence = $(if ($Evidence) { $Evidence } else { $null })
                source = $(if ($Source) { $Source } else { $null })
                upsert = $true
            } | ConvertTo-Json -Compress
        }
        $tmp = Join-Path $env:TEMP ("arnes-save-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $data -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath save "-" 2>$null
            $res = $out | Out-String | ConvertFrom-Json
            if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 3); exit 0 }
            Write-Host ("  [OK] Memoria guardada (id={0})" -f $res.id) -ForegroundColor Green
            Write-Host ("       {0} | {1}" -f $saveAgent, $saveTopic) -ForegroundColor DarkGray
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'get' {
        if ($Id -le 0) { throw 'Falta -Id' }
        $out = & $python $BrainScript $DbPath get $Id 2>$null
        $r = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($r | ConvertTo-Json -Compress -Depth 4); exit 0 }
        if ($r.error) { Write-Host ("  [!] " + $r.error) -ForegroundColor Yellow; exit 0 }
        Write-Host ("  [{0}] {1} | {2}" -f $r.id, $r.agent, $r.topic_key) -ForegroundColor Yellow
        Write-Host ("       {0}" -f $r.content) -ForegroundColor White
        Write-Host ("       ({0} | {1})" -f $r.type, $r.created_at) -ForegroundColor DarkGray
    }
    'update' {
        if ($Id -le 0) { throw 'Falta -Id (update -Id 5 -Content ...)' }
        if (-not $Content -and -not $Topic) { throw 'Falta -Content o -Topic' }
        $data = @{ id = $Id; content = $Content; topic_key = $Topic } | ConvertTo-Json -Compress
        $tmp = Join-Path $env:TEMP ("arnes-update-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $data -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath update "-" 2>$null
            $res = $out | Out-String | ConvertFrom-Json
            if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 3); exit 0 }
            Write-Host ("  [OK] Observacion {0} actualizada" -f $res.id) -ForegroundColor Green
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'recall' {
        if (-not $Query) { throw 'Falta -Query' }
        $agentArg = if ($Agent) { $Agent } else { '-' }
        $tagArg = if ($Tag) { $Tag } else { '-' }
        $out = & $python $BrainScript $DbPath recall $Query $agentArg $Limit $tagArg 2>$null
        if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
        $hits = $out | Out-String | ConvertFrom-Json
        Write-Host ("  Recall: {0} recuerdos relevantes" -f $hits.Count) -ForegroundColor Cyan
        foreach ($h in $hits) {
            Write-Host ("  [{0}] {1} | conf={2} rs={3}" -f $h.id, $h.topic_key, $h.confidence, $h.effective_retrieval) -ForegroundColor Yellow
            $short = $h.content
            if ($short.Length -gt 90) { $short = $short.Substring(0, 90) + '...' }
            Write-Host ("       {0}" -f $short) -ForegroundColor White
        }
    }
    'reinforce' {
        if ($Id -le 0) { throw 'Falta -Id' }
        $data = @{ id = $Id; evidence = $Evidence; success = $true } | ConvertTo-Json -Compress
        $tmp = Join-Path $env:TEMP ("arnes-reinf-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $data -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath reinforce "-" 2>$null
            if (-not $Quiet) { Write-Host ("  [OK] Memoria {0} reforzada (storage/confianza subieron)" -f $Id) -ForegroundColor Green }
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'verify' {
        if ($Id -le 0) { throw 'Falta -Id' }
        if (-not $Verdict) { $Verdict = 'PASS' }
        $data = @{ id = $Id; verdict = $Verdict; evidence = $Evidence } | ConvertTo-Json -Compress
        $tmp = Join-Path $env:TEMP ("arnes-verif-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $data -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath verify "-" 2>$null
            if (-not $Quiet) { Write-Host ("  [OK] Memoria {0} verificada: {1}" -f $Id, $Verdict) -ForegroundColor $(if ($Verdict -eq 'PASS') { 'Green' } else { 'Yellow' }) }
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'reconsolidate' {
        if ($Id -le 0 -or -not $Content) { throw 'Falta -Id y -Content (reconsolidate -Id 8 -Content "...")' }
        $data = @{ id = $Id; content = $Content; evidence = $Evidence } | ConvertTo-Json -Compress
        $tmp = Join-Path $env:TEMP ("arnes-recon-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $data -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath reconsolidate "-" 2>$null
            if (-not $Quiet) { Write-Host ("  [OK] Memoria {0} reconsolidada (revision guardada)" -f $Id) -ForegroundColor Green }
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'skill' {
        if (-not $SkillAction) { throw 'Falta -SkillAction (register | exec | link | status | executions)' }
        if ($SkillAction -eq 'status') {
            $out = & $python $BrainScript $DbPath skill status $(if ($Skill) { $Skill } else { '-' }) 2>$null
            if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
            $rows = $out | Out-String | ConvertFrom-Json
            if (-not $rows) { Write-Host '  Sin skills registradas.' -ForegroundColor DarkGray; exit 0 }
            foreach ($s in @($rows)) {
                Write-Host ("  {0,-28} state={1,-12} mastery={2} ({3} ok / {4} fail)" -f $s.skill_id, $s.state, $s.mastery, $s.success_count, $s.failure_count) -ForegroundColor White
            }
        }
        elseif ($SkillAction -eq 'register') {
            $data = @{ skill_id = $Skill; version = '1.0'; triggers = @() } | ConvertTo-Json -Compress
            $tmp = Join-Path $env:TEMP ("arnes-skill-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content -Path $tmp -Value $data -Encoding UTF8
            try {
                $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath skill register "-" 2>$null
                if (-not $Quiet) { Write-Host ("  [OK] Skill '{0}' registrada (new)" -f $Skill) -ForegroundColor Green }
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
        }
        elseif ($SkillAction -eq 'exec') {
            $data = @{ skill_id = $Skill; version = '1.0'; agent = $Agent; quest_id = $QuestId;
                       success = $true; verdict = 'PASS'; evidence = $Evidence } | ConvertTo-Json -Compress
            $tmp = Join-Path $env:TEMP ("arnes-skillx-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content -Path $tmp -Value $data -Encoding UTF8
            try {
                $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath skill exec "-" 2>$null
                if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
                $res = $out | Out-String | ConvertFrom-Json
                Write-Host ("  [OK] Ejecucion de '{0}': {1} (mastery={2}, {3} totales)" -f $res.skill, $res.state, $res.mastery, $res.total) -ForegroundColor Green
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
        }
        else { throw "SkillAction '$SkillAction' no soportada en CLI" }
    }
    'route' {
        if (-not $Query) { throw 'Falta -Query' }
        $out = & $python $BrainScript $DbPath route $Query $(if ($Risk) { $Risk } else { '-' }) 2>$null
        $r = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($r | ConvertTo-Json -Compress -Depth 3); exit 0 }
        $color = switch ($r.path) { 'FAST' { 'Green' } 'RECALL' { 'Cyan' } 'SKILL' { 'Magenta' } 'DELIBERATE' { 'Yellow' } 'DEEP' { 'Red' } default { 'White' } }
        Write-Host ("  [ROUTE] {0} -> {1}" -f $r.path, $r.reason) -ForegroundColor $color
    }
    'reviews' {
        $out = & $python $BrainScript $DbPath reviews $Limit 2>$null
        if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
        $rows = $out | Out-String | ConvertFrom-Json
        if ($rows.Count -eq 0) { Write-Host '  Nada por revalidar ahora.' -ForegroundColor DarkGray; exit 0 }
        Write-Host ("  {0} memorias por revalidar:" -f $rows.Count) -ForegroundColor Cyan
        foreach ($r in $rows) { Write-Host ("  [{0}] {1} (vol={2})" -f $r.memory_id, $r.topic_key, $r.volatility) -ForegroundColor White }
    }
    'checkpoint' {
        if ($List) {
            $out = & $python $BrainScript $DbPath checkpoint list $Limit 2>$null
            if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
            $rows = $out | Out-String | ConvertFrom-Json
            foreach ($c in $rows) { Write-Host ("  [#{0}] quest={1} agent={2} continuidad={3} | {4}" -f $c.id, $c.quest_id, $c.agent, $c.continuity_score, $c.created_at) -ForegroundColor White; if ($c.next_action) { Write-Host ("        NEXT: {0}" -f $c.next_action) -ForegroundColor DarkGray } }
            exit 0
        }
        if ($Id -gt 0) {
            $out = & $python $BrainScript $DbPath checkpoint get $Id 2>$null
            if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
            $c = $out | Out-String | ConvertFrom-Json
            if ($c.error) { Write-Host ("  [!] " + $c.error) -ForegroundColor Yellow; exit 0 }
            Write-Host ("  COGNITIVE CHECKPOINT #{0}" -f $c.id) -ForegroundColor Cyan
            Write-Host ("  Quest: {0} | Agent: {1} | Continuidad: {2}" -f $c.quest_id, $c.agent, $c.continuity_score) -ForegroundColor White
            Write-Host ("  Goal: {0}" -f $c.goal) -ForegroundColor White
            Write-Host ("  Phase: {0}" -f $c.phase) -ForegroundColor DarkGray
            if ($c.pending_tasks) { Write-Host ("  Pending: {0}" -f ($c.pending_tasks -join ' | ')) -ForegroundColor Yellow }
            if ($c.critical_memory_ids) { Write-Host ("  Decisions: {0}" -f (($c.critical_memory_ids | ForEach-Object { "#" + $_ }) -join ' ')) -ForegroundColor Yellow }
            if ($c.active_skill) { Write-Host ("  Skill: {0}" -f $c.active_skill) -ForegroundColor Magenta }
            if ($c.blockers) { Write-Host ("  Blocker: {0}" -f ($c.blockers -join ' | ')) -ForegroundColor Red }
            Write-Host ("  NEXT ACTION: {0}" -f $c.next_action) -ForegroundColor Green
            exit 0
        }
        if ($Create -and $NextAction) {
            $data = @{
                quest_id = $QuestId; agent = $Agent; goal = $Goal; phase = $Phase
                completed_tasks = @($Completed); pending_tasks = @($Pending)
                active_files = @($Files); modified_files = @($ModifiedFiles)
                active_decisions = @(); critical_memory_ids = @($Decisions)
                active_skill = $Skill
                skill_state = @{ stage = $Stage }
                blockers = @($Blockers); errors = @($Errors)
                test_state = $TestState; build_state = $BuildState; git_state = $GitState
                next_action = $NextAction; reason = 'manual'
            } | ConvertTo-Json -Depth 5 -Compress
            $tmp = Join-Path $env:TEMP ("arnes-cp-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content -Path $tmp -Value $data -Encoding UTF8
            try {
                $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath checkpoint create "-" 2>$null
                $res = $out | Out-String | ConvertFrom-Json
                if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress); exit 0 }
                Write-Host ("  [OK] COGNITIVE CHECKPOINT #{0} creado (continuidad {1})" -f $res.id, $res.continuity_score) -ForegroundColor Green
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            exit 0
        }
        throw 'Uso: checkpoint -Create -QuestId X -Agent X -Goal ... -NextAction ... | checkpoint -Id N | checkpoint -List'
    }
    'capsule' {
        if ($Id -le 0) { throw 'Falta -Id (capsule -Id 5)' }
        $out = & $python $BrainScript $DbPath capsule $Id 2>$null
        if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
        $r = $out | Out-String | ConvertFrom-Json
        Write-Host ("  RECOVERY CAPSULE #{0} (continuidad {1})" -f $r.id, $r.continuity_score) -ForegroundColor Cyan
        Write-Host ''
        Write-Host "  $($r.capsule)" -ForegroundColor White
        Write-Host ''
    }
    'continuity' {
        if ($Id -le 0) { throw 'Falta -Id' }
        $out = & $python $BrainScript $DbPath continuity $Id 2>$null
        if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
        $r = $out | Out-String | ConvertFrom-Json
        Write-Host ("  [CONTINUIDAD] Checkpoint #{0}: {1}" -f $r.id, $r.continuity_score) -ForegroundColor Green
    }
    'consolidate-recent' {
        $out = & $python $BrainScript $DbPath consolidate-recent $Hours 2>$null
        if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
        $r = $out | Out-String | ConvertFrom-Json
        Write-Host ("  [CONSOLIDACION] {0} experiencias: working={1} episodic={2} semantic={3} procedural={4} noise={5}" -f $r.classified, $r.working, $r.episodic, $r.semantic, $r.procedural, $r.noise) -ForegroundColor Green
    }
    'cognitive-compact' {
        # Flujo completo: consolidar reciente + checkpoint + capsule
        & $python $BrainScript $DbPath consolidate-recent $Hours 2>$null | Out-Null
        $data = @{
            quest_id = $QuestId; agent = $Agent; goal = $Goal
            completed_tasks = @($Completed); pending_tasks = @($Pending)
            active_files = @($Files); critical_memory_ids = @($Decisions)
            active_skill = $Skill; skill_state = @{ stage = $Stage }
            blockers = @($Blockers); errors = @($Errors)
            test_state = $TestState; build_state = $BuildState; git_state = $GitState
            next_action = $NextAction; reason = 'cognitive-compact'
        } | ConvertTo-Json -Depth 5 -Compress
        $tmp = Join-Path $env:TEMP ("arnes-cc-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $data -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath checkpoint create "-" 2>$null
            $res = $out | Out-String | ConvertFrom-Json
            if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress); exit 0 }
            Write-Host ("  [COGNITIVE COMPACTION] Checkpoint #{0} creado (continuidad {1})" -f $res.id, $res.continuity_score) -ForegroundColor Green
            Write-Host '  Recovery Capsule:' -ForegroundColor Cyan
            & $python $BrainScript $DbPath capsule $res.id 2>$null | Out-String | ConvertFrom-Json | ForEach-Object { Write-Host "  $($_.capsule)" -ForegroundColor White }
            Write-Host ''
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'suggest-topic' {
        if (-not $Query) { throw 'Falta -Query (texto para sugerir topico)' }
        # Heuristica: palabras significativas (mayores a 3 letras, sin stopwords) -> topico kebab-case
        $stop = @('para', 'que', 'con', 'los', 'las', 'una', 'uno', 'este', 'esta', 'como', 'cual', 'sobre', 'para', 'por', 'del', 'al', 'su', 'sus', 'the', 'and', 'with', 'from', 'this', 'that', 'was', 'were', 'will', 'have', 'has', 'been', 'our', 'your')
        $words = @($Query.ToLower() -split '\W+' | Where-Object { $_.Length -gt 3 -and $_ -notin $stop })
        $topic = ($words | Select-Object -First 3) -join '-'
        if (-not $topic) { $topic = 'topic-' + (Get-Date -Format 'yyyyMMdd-HHmm') }
        if ($Quiet) { Write-Output $topic; exit 0 }
        Write-Host ("  [OK] Topico sugerido: {0}" -f $topic) -ForegroundColor Green
    }
    'revisions' {
        if ($Id -le 0) { throw 'Falta -Id (revisions -Id 8)' }
        $out = & $python $BrainScript $DbPath revisions $Id 2>$null
        $revs = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($revs | ConvertTo-Json -Compress -Depth 4); exit 0 }
        if ($revs.Count -eq 0) { Write-Host '  Sin revisiones (el topico nunca se sobrescribio).' -ForegroundColor DarkGray; exit 0 }
        Write-Host ("  Revisiones de la observacion {0}:" -f $Id) -ForegroundColor Cyan
        foreach ($rv in $revs) {
            $short = $rv.content
            if ($short.Length -gt 90) { $short = $short.Substring(0, 90) + '...' }
            Write-Host ("  [rev {0} | {1}] {2}" -f $rv.id, $rv.created_at, $short) -ForegroundColor White
        }
    }
    'compact' {
        $out = & $python $BrainScript $DbPath compact $OlderThanDays 2>$null
        $res = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 3); exit 0 }
        Write-Host ("  [OK] Compactado: {0} observaciones > digests ({1})" -f $res.compacted, $res.digests) -ForegroundColor Green
        Write-Host ("       corte: antes de {0}" -f $res.cutoff) -ForegroundColor DarkGray
    }
    'backup' {
        $target = Join-Path $ArnesDir 'backups'
        if (-not (Test-Path $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupDir = Join-Path $target "backup-$stamp"
        $out = & $python $BrainScript $DbPath export $backupDir 2>$null
        $res = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 3); exit 0 }
        Write-Host ("  [OK] Backup creado en {0} ({1} archivos)" -f $backupDir, $res.count) -ForegroundColor Green
    }
    'search' {
        if (-not $Query) { throw 'Falta -Query' }
        $agentArg = if ($Agent) { $Agent } else { '-' }
        $tagArg = if ($Tag) { $Tag } else { '-' }
        $out = & $python $BrainScript $DbPath search $Query $agentArg $Limit $tagArg 2>$null
        if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }        $results = $out | Out-String | ConvertFrom-Json
        if (-not $results -or $results.Count -eq 0) {
            Write-Host '  No se encontraron recuerdos.' -ForegroundColor Yellow
            exit 0
        }
        Write-Host ("  {0} recuerdo(s) encontrado(s)" -f $results.Count) -ForegroundColor Cyan
        foreach ($r in ($results | Select-Object -First 10)) {
            Write-Host ('  [{0}] {1}' -f $r.id, $r.topic_key) -ForegroundColor Yellow
            $short = $r.content
            if ($short.Length -gt 100) { $short = $short.Substring(0, 100) + '...' }
            Write-Host ("       {0}" -f $short) -ForegroundColor White
            Write-Host ("       ({0} | {1})" -f $r.agent, $r.created_at) -ForegroundColor DarkGray
        }
    }
    'context' {
        $out = & $python $BrainScript $DbPath context $Limit 2>$null
        $results = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($results | ConvertTo-Json -Compress -Depth 5); exit 0 }
        Write-Host ("  Contexto reciente: {0} observaciones" -f $results.Count) -ForegroundColor Cyan
        foreach ($r in ($results | Select-Object -First 10)) {
            Write-Host ('  [{0}] {1} | {2}' -f $r.id, $r.agent, $r.topic_key) -ForegroundColor White
        }
    }
    'agent' {
        if (-not $Agent) { throw 'Falta -Agent' }
        $out = & $python $BrainScript $DbPath agent $Agent $Limit 2>$null
        $results = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($results | ConvertTo-Json -Compress -Depth 5); exit 0 }
        Write-Host ("  Memoria de {0}: {1} recuerdos" -f $Agent, $results.Count) -ForegroundColor Cyan
        foreach ($r in ($results | Select-Object -First 10)) {
            Write-Host ('  [{0}] {1}' -f $r.id, $r.topic_key) -ForegroundColor Yellow
            $short = $r.content
            if ($short.Length -gt 80) { $short = $short.Substring(0, 80) + '...' }
            Write-Host ("       {0}" -f $short) -ForegroundColor White
        }
    }
    'export' {
        $target = if ($OutDir) { $OutDir } else { Join-Path $ArnesDir 'memory\export' }
        $out = & $python $BrainScript $DbPath export $target 2>$null
        Write-Host ("  [OK] Memoria exportada a {0}" -f $target) -ForegroundColor Green
    }
    'import' {
        $target = if ($InDir) { $InDir } else { Join-Path $ArnesDir 'memory\export' }
        if (-not (Test-Path $target)) { Write-Host '  No hay JSONL para importar.' -ForegroundColor Yellow; exit 0 }
        $out = & $python $BrainScript $DbPath import $target 2>$null
        $res = $out | Out-String | ConvertFrom-Json
        Write-Host ("  [OK] {0} recuerdos importados" -f $res.imported) -ForegroundColor Green
    }
    'stats' {
        $out = & $python $BrainScript $DbPath stats 2>$null
        $s = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($s | ConvertTo-Json -Compress -Depth 3); exit 0 }
        Write-Host ''
        Write-Host '  OSMA - STATS' -ForegroundColor Cyan
        Write-Host ("  {0,-18} {1}" -f 'Agentes:', $s.agents) -ForegroundColor White
        Write-Host ("  {0,-18} {1}" -f 'Observaciones:', $s.observations) -ForegroundColor White
        Write-Host ("  {0,-18} {1}" -f 'Quests:', $s.quests) -ForegroundColor White
        Write-Host ("  {0,-18} {1}" -f 'Sesiones:', $s.sessions) -ForegroundColor White
        Write-Host ("  {0,-18} {1}" -f 'Edges:', $s.edges) -ForegroundColor White
        Write-Host ("  {0,-18} {1:N0} bytes" -f 'DB:', $s.db_size_bytes) -ForegroundColor White
    }
    'quest' {
        if (-not $Json) { throw 'Falta -Json (JSON con datos del quest)' }
        $tmp = Join-Path $env:TEMP ("arnes-quest-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $Json -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath quest "-" 2>$null
            Write-Host ("  [OK] Quest registrado: {0}" -f ($out | Out-String).Trim()) -ForegroundColor Green
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'quests' {
        $out = & $python $BrainScript $DbPath quests 2>$null
        $results = $out | Out-String | ConvertFrom-Json
        Write-Host ("  Historial de quests: {0}" -f $results.Count) -ForegroundColor Cyan
        foreach ($q in ($results | Select-Object -First 10)) {
            $color = if ($q.result -eq 'PASS') { 'Green' } else { 'Red' }
            Write-Host ("  {0} [{1}] {2} ({3} tokens)" -f $q.id, $q.result, $q.quest_type, $q.tokens_used) -ForegroundColor $color
        }
    }
    'aquest' {
        if ($Create -and $QuestId) {
            $data = @{ id = $QuestId; description = $Goal; mode = $Phase } | ConvertTo-Json -Compress
            $tmp = Join-Path $env:TEMP ("arnes-aq-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content $tmp $data -Encoding UTF8
            try { $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath aquest create "-" 2>$null; if (-not $Quiet) { Write-Host ("  [OK] Quest autonoma: {0}" -f (($out | Out-String).Trim())) -ForegroundColor Green } } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            exit 0
        }
        if ($List) {
            $out = & $python $BrainScript $DbPath aquest list $Limit 2>$null
            if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
            $rows = $out | Out-String | ConvertFrom-Json
            foreach ($q in $rows) { Write-Host ("  {0} [{1}] {2} ({3})" -f $q.id, $q.status, $q.mode, $q.created_at) -ForegroundColor White }
            exit 0
        }
        if ($QuestId) {
            $out = & $python $BrainScript $DbPath aquest progress $QuestId 2>$null
            if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
            $p = $out | Out-String | ConvertFrom-Json
            Write-Host ("  Quest {0}: {1}% | pass={2} running={3} ready={4} blocked={5} fail={6} total={7}" -f $QuestId, $p.pct, $p.pass, $p.running, $p.ready, $p.blocked, $p.fail, $p.total) -ForegroundColor Green
            exit 0
        }
        throw 'Uso: aquest -Create -QuestId X -Goal ... | aquest -List | aquest -QuestId X'
    }
    'atask' {
        if ($Create -and $QuestId -and $TaskId -and $Agent -and $Content) {
            $data = @{ quest_id = $QuestId; task_id = $TaskId; description = $Content; agent = $Agent; dependencies = @($TaskDeps); acceptance = $Acceptance } | ConvertTo-Json -Compress
            $tmp = Join-Path $env:TEMP ("arnes-at-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content $tmp $data -Encoding UTF8
            try { $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath atask create "-" 2>$null; if (-not $Quiet) { Write-Host ("  [OK] Tarea creada: {0} -> {1}" -f $TaskId, $Agent) -ForegroundColor Green } } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            exit 0
        }
        if ($List) {
            $out = & $python $BrainScript $DbPath atask list $(if ($QuestId) { $QuestId } else { '-' }) $(if ($Status) { $Status } else { '-' }) 2>$null
            if ($Quiet) { Write-Output (($out | Out-String).Trim()); exit 0 }
            $rows = $out | Out-String | ConvertFrom-Json
            foreach ($t in $rows) { Write-Host ("  {0,-10} [{1,-8}] {2,-10} -> {3}" -f $t.task_id, $t.status, $t.agent, $t.description) -ForegroundColor White }
            exit 0
        }
        if ($Id -gt 0 -and ($Status -or $Summary)) {
            $data = @{ id = $Id; status = $Status; summary = $Summary; blockers = @($Blockers); evidence = $Evidence; attempts = $Attempts; model = $Skill; tokens_used = $TokensUsed } | ConvertTo-Json -Compress
            $tmp = Join-Path $env:TEMP ("arnes-atup-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content $tmp $data -Encoding UTF8
            try { $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath atask update "-" 2>$null; if (-not $Quiet) { Write-Host ("  [OK] Tarea {0} -> {1}" -f $Id, $Status) -ForegroundColor Green } } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            exit 0
        }
        throw 'Uso: atask -Create -QuestId X -TaskId AUTH-03 -Agent ansem -Content "..." -TaskDeps T2,T3 | atask -List -QuestId X | atask -Id N -Status pass -Summary ...'
    }
    'edge' {
        if (-not $Json) { throw 'Falta -Json (JSON con node_a, node_b, relation)' }
        $tmp = Join-Path $env:TEMP ("arnes-edge-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content -Path $tmp -Value $Json -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath edge "-" 2>$null
            Write-Host ("  [OK] Edge registrado: {0}" -f ($out | Out-String).Trim()) -ForegroundColor Green
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'edges' {
        $nodeArg = if ($Query) { $Query } else { '-' }
        $out = & $python $BrainScript $DbPath edges $nodeArg 2>$null
        $results = $out | Out-String | ConvertFrom-Json
        Write-Host ("  Relaciones: {0}" -f $results.Count) -ForegroundColor Cyan
        foreach ($e in ($results | Select-Object -First 15)) {
            Write-Host ("  {0} -[{1}]-> {2} ({3})" -f $e.node_a, $e.relation, $e.node_b, $e.agent) -ForegroundColor White
        }
    }
    # ===== OSMA V5-V7: Experiencia, cues, episodios, patrones (memoria de alto nivel) =====
    'experience' {
        $sub = $(if ($ExperienceAction) { $ExperienceAction } else { 'record' })
        if ($sub -eq 'record') {
            if (-not $Situation) { throw 'experience record: falta -Situation' }
            $data = @{
                situation = $Situation
                reasoning = $Reasoning
                conclusion = $Conclusion
                action = $Action
                outcome = $Outcome
                reward = $Reward
                agent = $(if ($Agent) { $Agent } else { 'atlas' })
                project = $(if ($Project) { $Project } elseif ($Topic) { $Topic } else { $null })
                topic_key = $(if ($Topic) { $Topic } else { "experience/$(Get-Date -Format 'yyyyMMdd')" })
                quest_id = $(if ($QuestId) { $QuestId } else { $null })
            } | ConvertTo-Json -Compress
            $tmp = Join-Path $env:TEMP ("arnes-exp-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content $tmp $data -Encoding UTF8
            try {
                $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath osma-experience-record "-" 2>$null
                $res = $out | Out-String | ConvertFrom-Json
                if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 5); exit 0 }
                Write-Host ("  [OK] Experiencia #{0} registrada. cues={1} salience={2}, links exp={3} obs={4}" -f $res.id, $res.cues_created, $res.salience, $res.linked_experiences, $res.linked_observations) -ForegroundColor Green
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            exit 0
        }
        elseif ($sub -eq 'search') {
            if (-not $Query) { throw 'experience search: falta -Query' }
            $projArg = if ($Project) { $Project } else { '-' }
            $agentArg = if ($Agent) { $Agent } else { '-' }
            $out = & $python $BrainScript $DbPath osma-experience-search $Query $projArg $agentArg $Limit 2>$null
            $res = $out | Out-String | ConvertFrom-Json
            if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 5); exit 0 }
            if (-not $res -or $res.Count -eq 0) { Write-Host '  Sin experiencias para el query.' -ForegroundColor Yellow; exit 0 }
            Write-Host ("  {0} experiencia(s)" -f $res.Count) -ForegroundColor Cyan
            foreach ($e in ($res | Select-Object -First 5)) {
                Write-Host ("  [#{0}] [{1}] app={2} conf={3} sal={4}" -f $e.id, $e.validation_status, $e.applicability, $e.confidence, $e.salience) -ForegroundColor Yellow
                $short = $e.summary; if ($short -and $short.Length -gt 90) { $short = $short.Substring(0,90) + '...' }
                Write-Host ("       {0}" -f $short) -ForegroundColor White
            }
            exit 0
        }
        elseif ($sub -eq 'cues') {
            if (-not $Cues) { throw 'experience cues: falta -Cues (comma-separated)' }
            $data = @{ cues = @($Cues -split ',') ; project = $(if ($Project) { $Project } else { $null }); agent = $(if ($Agent) { $Agent } else { $null }) } | ConvertTo-Json -Compress
            $tmp = Join-Path $env:TEMP ("arnes-cue-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content $tmp $data -Encoding UTF8
            try {
                $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath osma-cue-search "-" 2>$null
                $res = $out | Out-String | ConvertFrom-Json
                if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 6); exit 0 }
                $w = $res.winner
                if (-not $w) { Write-Host ('  Sin winner para cues [{0}]' -f $Cues) -ForegroundColor Yellow; exit 0 }
                Write-Host ("  WINNER: {0}  score={1}  reactivation={2}" -f $w.episode_id, $w.episode_activation_score, [bool]$res.reactivation) -ForegroundColor Green
                $short = $w.summary; if ($short -and $short.Length -gt 80) { $short = $short.Substring(0,80) + '...' }
                Write-Host ("    {0}" -f $short) -ForegroundColor White
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            exit 0
        }
        elseif ($sub -eq 'stats') {
            $out = & $python $BrainScript $DbPath osma-experience-stats 2>$null
            $s = $out | Out-String | ConvertFrom-Json
            if ($Quiet) { Write-Output ($s | ConvertTo-Json -Compress -Depth 4); exit 0 }
            Write-Host "  OSMA EXPERIENCIAS" -ForegroundColor Cyan
            Write-Host ("  {0,-26} {1}" -f 'Total:', $s.experiences) -ForegroundColor White
            Write-Host ("  {0,-26} {1}" -f 'Patrones (V5):', $s.patterns) -ForegroundColor Green
            Write-Host ("  {0,-26} {1}" -f 'Verified:', $s.verified) -ForegroundColor Green
            Write-Host ("  {0,-26} {1}" -f 'Failed:', $s.failed) -ForegroundColor Red
            Write-Host ("  {0,-26} {1}" -f 'Reward promedio:', $s.avg_reward) -ForegroundColor White
            Write-Host ("  {0,-26} {1}" -f 'Reuses ok/fail:', ("{0}/{1}" -f $s.reused_successfully, $s.reused_failed)) -ForegroundColor White
            Write-Host ("  {0,-26} {1}" -f 'Cues (V6):', $s.total_cues) -ForegroundColor Green
            Write-Host ("  {0,-26} {1}" -f 'Anchors:', $s.total_anchors) -ForegroundColor Green
            Write-Host ("  {0,-26} {1}" -f 'Avg salience:', $s.avg_salience) -ForegroundColor White
            Write-Host ("  {0,-26} {1}" -f 'Avg rutas:', $s.avg_retrieval_routes) -ForegroundColor White
            exit 0
        }
        elseif ($sub -eq 'validate') {
            if (-not $Id -or -not $Reward) { throw 'experience validate: falta -Id y -Reward' }
            $data = @{ experience_id = $Id; reward = $Reward; result = $(if ($Outcome) { $Outcome } else { $null }) } | ConvertTo-Json -Compress
            $tmp = Join-Path $env:TEMP ("arnes-exv-" + [guid]::NewGuid().ToString('N') + ".json")
            Set-Content $tmp $data -Encoding UTF8
            try {
                $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath osma-experience-validate "-" 2>$null
                $res = $out | Out-String | ConvertFrom-Json
                if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 5); exit 0 }
                Write-Host ("  [OK] Experiencia #{0} -> {1} (conf={2}, imp={3})" -f $Id, $res.validation_status, $res.confidence, $res.importance) -ForegroundColor Green
            } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
            exit 0
        }
        elseif ($sub -eq 'analyze') {
            $expId = if ($Id -gt 0) { [string]$Id } else { '-' }
            $out = & $python $BrainScript $DbPath osma-experience-analyze $expId 2>$null
            $res = $out | Out-String | ConvertFrom-Json
            if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 6); exit 0 }
            Write-Host ("  [OK] Analisis: {0} experiencias, {1} cues, {2} anchors" -f $res.experiences_scanned, $res.cues_created, $res.anchors_created) -ForegroundColor Green
            exit 0
        }
        else { throw 'experience: usa record | search | cues | stats | validate | analyze' }
    }
    'episode' {
        if ($Id -le 0) { throw 'episode: falta -Id (experience_id)' }
        $data = @{ experience_id = $Id } | ConvertTo-Json -Compress
        $tmp = Join-Path $env:TEMP ("arnes-ep-" + [guid]::NewGuid().ToString('N') + ".json")
        Set-Content $tmp $data -Encoding UTF8
        try {
            $out = Get-Content $tmp -Raw | & $python $BrainScript $DbPath osma-episode "-" 2>$null
            $e = $out | Out-String | ConvertFrom-Json
            if ($Quiet) { Write-Output ($e | ConvertTo-Json -Compress -Depth 6); exit 0 }
            if (-not $e -or $e.error) { Write-Host ("  {0}" -f $e.error) -ForegroundColor Red; exit 1 }
            Write-Host ("  {0} [{1}] reward={2}" -f $e.episode_id, $e.validation_status, $e.reward_signal) -ForegroundColor Green
            Write-Host ("  S: {0}" -f $e.summary) -ForegroundColor White
            Write-Host ("  Solucion: {0}" -f $e.solution) -ForegroundColor White
            Write-Host ("  Outcome : {0}" -f $e.outcome) -ForegroundColor White
            Write-Host ("  Dims: conf={0} imp={1} sal={2} retr={3} freq={4} assoc={5}" -f $e.confidence, $e.importance, $e.salience, $e.retrieval_strength, $e.frequency, $e.association_strength) -ForegroundColor DarkGray
            Write-Host ("  Cues: {0}" -f (($e.cues | ForEach-Object { $_.value }) -join ', ')) -ForegroundColor DarkGray
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    'patterns' {
        $out = & $python $BrainScript $DbPath osma-patterns 2>$null
        $res = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 5); exit 0 }
        if (-not $res -or $res.Count -eq 0) { Write-Host '  Sin patrones aun. Corre: osma-memory.ps1 analysis / osma-memory.ps1 patterns -detect' -ForegroundColor Yellow; exit 0 }
        Write-Host ("  Patrones: {0}" -f $res.Count) -ForegroundColor Cyan
        foreach ($p in ($res | Select-Object -First 10)) {
            Write-Host ("  [PATTERN] {0}" -f $p.topic) -ForegroundColor Yellow
            Write-Host ("      check: {0}" -f $p.check_procedure) -ForegroundColor White
            Write-Host ("      de: {0}" -f ($p.source_experience_ids -join ', ')) -ForegroundColor DarkGray
        }
    }
    'analyze' {
        # Backfill V6: descompone experiencias existentes en cues + generacion de anchors
        $out = & $python $BrainScript $DbPath osma-experience-analyze 2>$null
        $res = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($res | ConvertTo-Json -Compress -Depth 6); exit 0 }
        Write-Host ("  [OK] Analisis V6: {0} experiencias, {1} cues, {2} anchors" -f $res.experiences_scanned, $res.cues_created, $res.anchors_created) -ForegroundColor Green
    }
    'osma-stats' {
        # Summary defensivo V4-V7 del cerebro OSMA (mismo que cli osma-stats)
        $out = & $python $BrainScript $DbPath osma-stats 2>$null
        $s = $out | Out-String | ConvertFrom-Json
        if ($Quiet) { Write-Output ($s | ConvertTo-Json -Compress -Depth 4); exit 0 }
        Write-Host '  OSMA - RESUMEN (V4-V7)' -ForegroundColor Cyan
        Write-Host ("  {0,-26} {1}" -f 'Links asociativos:', $s.links) -ForegroundColor White
        Write-Host ("  {0,-26} {1}" -f 'Observaciones activas:', $s.active) -ForegroundColor White
        Write-Host ("  {0,-26} {1}" -f 'Contradicciones abiertas:', $s.contradictions_open) -ForegroundColor White
        Write-Host ("  {0,-26} {1}" -f 'Consolidaciones pendientes:', $s.consolidations_pending) -ForegroundColor White
        Write-Host ("  {0,-26} {1}" -f 'Experiencias (V5):', $s.total_experiences) -ForegroundColor Green
        Write-Host ("  {0,-26} {1}" -f 'Cues (V6):', $s.total_cues) -ForegroundColor Green
        Write-Host ("  {0,-26} {1}" -f 'Episodios:', $s.total_episodes) -ForegroundColor Green
    }
}
