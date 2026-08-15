# ADR-007 — OSMA V4: de base de datos de recuerdos a memoria asociativa adaptativa

> **Fecha**: 2026-08-12
> **Autor**: Usuario + Sisyphus (orquestación) + Ansem (backend) + Kuja (QA) + Tywin (verificación)
> **Estado**: `accepted`
> **Supersede**: (ninguno — evoluciona ADR-001 sin invalidarlo)

---

## Contexto

OSMA (`cli/arnes_brain.py` + `arnes.db` SQLite/FTS5) era una base de datos de recuerdos
con "sabor cognitivo": el esquema V3 ya declaraba pesos de asociación, co-activación,
estados de memoria y curva de olvido — pero el comportamiento asociativo **no existía**:

1. `edges` tenía columnas `weight`, `coactivation_count`, `success_count`… pero
   `add_edge()` nunca las escribía ni nadie las leía (grafo muerto)
2. La recuperación era únicamente FTS5 BM25 (keyword) — sin propagación por el grafo
3. El decaimiento (`_effective_retrieval`) se calculaba en consulta y **nunca se persistía**
4. La consolidación era heurística (`salience()` + concatenación naive + truncado 12K)
5. No había detección automática de contradicciones (solo `verify(FAIL)` → `contested`)
6. Export/import parcial, con pérdida de columnas cognitivas

El requisito del usuario: OSMA debe ser una **memoria asociativa que aprende con la
experiencia** — refuerza conexiones que se repiten, debilita las que no se usan, propaga
activación por niveles, consolida recuerdos pequeños en conocimiento estable, detecta
contradicciones, y **aprende qué recordar, qué relacionar, qué reforzar, qué olvidar y
qué recuperar**. Y debe integrarse en el ciclo de vida del coding agent **ARGOS**
(`pi/extensions/argos-*.ts`): el aprendizaje debe ocurrir como subproducto de que ARGOS trabaje.

## Decisión

**Evolucionar OSMA de forma incremental (no reescribir): activar el esqueleto V3 que ya
anticipaba la asociación + añadir la capa de aprendizaje + cablearla a los hooks de ARGOS.**

La arquitectura tiene cuatro capas aditivas:

1. **Schema V4 (aditivo, idempotente)** en `_migrate()`:
   - `observations` ADD: `activation_level`, `last_decay_at`, `successful_retrievals`, `decay_base`
   - Tablas nuevas: `observation_links` (sinapsis obs↔obs con `weight`,
     `coactivation_count`, `successful/failed_coactivations`, `last_coactivated_at`),
     `contradictions` (status open/resolved, `superseded_id`), `consolidations`
     (status pending/done, `source_ids` como evidencia)
   - `meta.schema_version` → `'4'`

2. **Capa de aprendizaje** (`arnes_brain.py`, 15 métodos: 11 públicos `osma_*` + 4 helpers):
   - `osma-migrate`: backfill de links por co-ocurrencia de entidades (≥2 compartidas)
   - `osma-link`: co-activación con señales (coactivation/success/correction/same_quest)
   - `osma-recall`: recall BM25 + **propagación de activación** BFS por niveles
     (`act_child = act_parent × 0.8 × weight`, umbral 0.25, budget)
   - `osma-reinforce`: refuerzo por utilidad (éxito → score+retrieval; fallo → confidence−, contested)
   - `osma-sleep`: **el sueño** — decaimiento persistido desde `decay_base` (nunca compuesto),
     transiciones ACTIVE→WARM→COLD→ARCHIVED, dedup en consolidaciones `pending`,
     detección de contradicciones, debilitamiento de links inactivos (×0.995/periodo)
   - `osma-context`: paquete de recuperación contextual con **presupuesto de tokens**
   - `osma-contradiction-resolve`: perdedor → `superseded` + edge `replaced_by`

3. **Integración ARGOS** (`pi/extensions/argos-*.ts`): `before_agent_start` inyecta el
   paquete contextual; `agent_settled` vincula y refuerza cada turno; `session_compact`
   dispara el sueño; working memory rastrea IDs recuperados para la co-activación.

4. **Alcance Fase 1**: sin embeddings (solo co-activación + propagación + FTS5, cero
   dependencias externas). Embeddings → Fase 2 opcional. Resumen de consolidaciones →
   agente LLM (batch) vía `osma-consolidation-finalize` (provisional determinístico hasta
   entonces).

## Alternativas consideradas

| Alternativa | Pros | Contras |
|---|---|---|
| **A. Reescritura completa de OSMA** | Limpia, sin deuda | Descarta 1,571 líneas funcionales; riesgo de pérdida de memoria; meses de trabajo; viola "no destruyas lo existente" |
| **B. Incremental: activar V3 + capa asociativa (ELEGIDA)** | El esquema V3 ya anticipaba el 70% (pesos, estados, olvido); aditivo y reversible; migración sin pérdida; integración en hooks existentes | Complejidad nueva en el motor; requiere mantener 3 generaciones de formatos legacy |
| **C. Embeddings ahora (semántica real)** | Recall semántico superior | Dependencia externa nueva (rompe stdlib-only); alarga implementación; la co-activación ya da asociación útil sin embeddings |
| **D. Consolidación solo determinística** | Sin LLM en el job | Resúmenes pobres; el harness ya tiene agentes que resumen (context-digest) — el batch LLM es natural |

## Consecuencias

**Positivas**:
- ARGOS aprende co-activación, refuerzo y olvido **como subproducto de trabajar**
  (hooks `agent_settled` / `session_compact`), sin intervención manual
- "Continuemos con EduClave" → `before_agent_start` reconstruye decisiones, errores+
  soluciones, agentes y contradicciones desde la red (continuidad real entre sesiones)
- La topología de la memoria **emerge del uso** (comunidades por co-activación)
- Presupuesto de tokens controlado (paquete contextual ≤ max_tokens; consolidados baratos)
- Cero dependencias externas nuevas (stdlib Python; sin deps npm)
- Migración idempotente y sin pérdida: DB real V3→V4 con 653 links backfilled y backup
- 24/24 tests (9 nuevos de aprendizaje con reloj inyectable) prueban que OSMA aprende

**Negativas / Riesgos**:
- Sin embeddings, la similitud semántica real queda pendiente (Fase 2) — el recall sigue
  siendo keyword FTS5 + asociación por entidades/co-activación
- Los JSONL v1 legacy (`memory/*.jsonl`) NO se reimportan (ruido operativo sin campos
  cognitivos) — la topología nueva parte del uso nuevo, no del historial legacy
- `decay_base` se backfillea solo en el próximo init del brain (no se tocó la DB real
  manualmente post-migración)
- Complejidad nueva en el motor: 11 comandos nuevos que deben mantenerse documentados

## Razón (por qué esta)

El harness busca **hechos antes de actuar, no opiniones** — y la memoria debe reflejar
la experiencia real de ARGOS, no texto estático. El esquema V3 ya declaraba la anatomía
de una memoria asociativa (pesos, estados, olvido, spaced review) pero sin comportamiento:
reescribir habría tirado ese diseño anticipado. La evolución incremental activa lo que ya
estaba esqueleto (edges, estados, revisiones, capsule) y añade solo lo que falta
(co-activación, propagación, refuerzo por utilidad, sueño, contradicciones), todo aditivo,
conmutable y verificado con pruebas de aprendizaje reales contra el motor.

Al igual que ADR-006 (gate determinístico antes que otro LLM), aquí el principio es el
mismo: **la memoria que aprende por co-activación y utilidad observable gana sobre la
memoria que solo acumula texto**. Y se integra en ARGOS porque ARGOS es quien vive las
experiencias que OSMA debe recordar.

---
*Memoria: al registrar, guardar en arnes.db `amarant/arch-decisions`*
