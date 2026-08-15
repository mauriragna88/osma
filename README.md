# OSMA — Memoria cerebral para agentes de código (open source)

OSMA es la **memoria asociativa adaptativa** del harness ARGOS: un cerebro local
SQLite+FTS5, **cero dependencias externas** (solo Python 3 stdlib), que aprende
de la experiencia de tus agentes de código.

> "El harness busca hechos antes de actuar — y la memoria debe reflejar la
> experiencia real, no texto estático." — ADR-007

## Qué es

OSMA guarda **recuerdos** (observations), **experiencias validadas**
(situación → razonamiento → solución → resultado, con reward), **conocimiento
consolidado** (patterns con provenance) y una **red asociativa** que emerge del
uso (co-activación, propagación, reactivación: *recordar modifica la memoria*).

| Capa | Archivo | Qué aporta |
|---|---|---|
| Motor | `osma_brain.py` | SQLite+FTS5, migraciones V1→V7, ~50 comandos `osma-*` |
| CLI | `osma-memory.ps1` | `init/save/recall/context/experience/episode/cue-search/patterns/osma-stats` |
| Grafo | `osma-graph.ps1` | Relaciones (edges) + BFS path-finding |
| Utilidades | `osma-backfill-*.ps1` | Backfill de experiencias desde observaciones |
| Detección | `osma-scan-projects.ps1` | Auto-identifica proyectos con ARGOS/OSMA |

## Instalación

OSMA es un CLI per-proyecto: la memoria de cada proyecto vive en
`<proyecto>/.arnes/arnes.db`. El repositorio OSMA se instala **globalmente** y
cualquier harness (ARGOS, opencode, pi, claude, codex, dsh) lo resuelve:

```powershell
# 1. clona este repo
git clone https://github.com/tu-usuario/osma.git

# 2. instala en ~/.config/arnes/osma (global, una vez por máquina)
.\osma\install.ps1

# 3. o apunta la variable de entorno
$env:ARNES_OSMA_ROOT = "C:\ruta\a\osma"
```

Resolución de OSMA (orden):
1. `$env:ARNES_OSMA_ROOT`
2. `~/.config/arnes/osma`
3. `./cli/` (fallback local de desarrollo)

## Uso rápido

```powershell
.\osma-memory.ps1 init                                        # crea .arnes/arnes.db
.\osma-memory.ps1 save -Agent vivi -Topic vivi/patron -Type pattern -Content "..."
.\osma-memory.ps1 recall -Query "supabase rls"
.\osma-memory.ps1 experience -ExperienceAction record -Situation "..." -Outcome "..." -Reward 0.9
.\osma-memory.ps1 experience -ExperienceAction cues -Cues "rls, permission denied"
.\osma-memory.ps1 osma-stats                                  # resumen V4-V7
```

## Tests

```powershell
# tests TypeScript (node:test + tsx)
pnpm install
pnpm test

# test de grafo (PowerShell)
powershell -NoProfile -ExecutionPolicy Bypass -File tests/osma-graph.tests.ps1
```

## Arquitectura (ADRs)

Ver `adr/` — la memoria evoluciona por versiones aditivas idempotentes:
V1 (SQLite) · V4 (asociativa) · V5 (experiencias validadas) · V6 (cues
multidimensionales) · V7 (pattern completion + reactivación).

## Licencia

MIT. Ver `LICENSE`.