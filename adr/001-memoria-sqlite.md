# ADR-001 — Memoria propia con SQLite + FTS5

> **Fecha**: 2026-08-05
> **Autor**: Usuario + Atlas + Amarant
> **Estado**: `accepted`

---

## Contexto

El harness necesitaba memoria persistente para que los agentes no pierdan contexto entre
sesiones y no alucinen. La opción obvia era usar arnes.db (sistema externo de Go con SQLite),
pero el usuario decidió que el arnes debe ser **100% independiente** — cero dependencia de
sistemas externos (arnes.db, ARNES, openspec).

## Decisión

Construir memoria propia con **SQLite + FTS5** (`arnes.db`), accesible vía Python nativo
(ya instalado, sin dependencias). FTS5 para búsqueda de texto instantánea tipo cerebro.
Export JSONL para portabilidad/git.

## Alternativas consideradas

| Alternativa | Pros | Contras |
|---|---|---|
| arnes.db (SQLite externo) | Listo, con MCP | Dependencia externa, binario WDAC-bloqueado |
| Archivos JSONL puros | Portable, diffeable en git | Búsqueda lenta a escala, sin índices |
| SQLite + FTS5 propio | Rápido, local, índices, cero deps | Binario (hay que exportar para git) — resuelto con JSONL |

## Consecuencias

**Positivas**:
- Cero dependencias externas — el arnes corre en cualquier máquina con Python
- Búsqueda FTS5 instantánea (recall selectivo anti-alucinación)
- Export JSONL para git/backup

**Negativas / Riesgos**:
- SQLite es binario — no se diffea en git; mitigado con export JSONL
- Hay que mantener el CLI propio (arnes-memory.ps1 + arnes_brain.py)

## Razón (por qué esta)

El principio rector del arnes: "como mejor tengamos nuestro arnes, el modelo es indistinto".
La memoria propia es la base de la independencia — nadie más controla nuestro cerebro.
