# ADR-009 — OSMA V6: Memoria Multidimensional y Múltiples Rutas de Evocación

> **Fecha**: 2026-08-12
> **Autor**: Usuario + Sisyphus (orquestación) + Ansem (backend) + Kuja (QA) + Tywin (verificación)
> **Estado**: `accepted`
> **Supersede**: (ninguno — extiende ADR-008 sin invalidarlo)

---

## Contexto

ADR-008 entregó la capa de Experiencias Validadas (V5): situación→razonamiento→solución→
resultado con reward y taxonomía de estados. Pero el requisito central del usuario quedaba
a medio camino: **una experiencia importante no debe almacenarse como un único bloque de
texto ni depender de una sola palabra clave para recuperarse**.

El spec exigía:
- **Múltiples claves de evocación**: cada experiencia se descompone en componentes
  (proyecto, agente, conceptos, tecnologías, problema, error, acción, razonamiento,
  solución, resultado, validación, momento, archivos, quest, sesión, entidades, patrones)
  y cada componente es una CUE / pista de recuperación. `EPISODE_742` recuperable desde
  `RLS`, desde `Vivi`, desde `permission denied`, desde combinaciones.
- **Densidad útil**: más conexiones no es mejor — `cue_quality` distintiva
  ("software" < "Supabase RLS permission denied") y `retrieval_routes` (rutas independientes).
- **Recuperación combinatoria no lineal**: 1 cue activa, 2 confirman, 3-4 hacen
  sobresalir al episodio correcto.
- **Mnemonotecnia computacional**: anchors automáticos para conocimiento importante pero
  con pocas asociaciones.
- **Salience funcional** (NO emociones): significancia operativa para ARGOS.
- **Dimensiones independientes**: importance/confidence/association_strength/salience/
  retrieval_strength/recency/frequency/reward — cada una evoluciona por su cuenta.
- **Recuerdos ricos vs ruidosos**: más componentes significativos = más rutas, NO más texto.
- **PATTERN COMPLETION**: CUE → activación → propagación → convergencia → competencia →
  episodio ganador → reconstrucción → contexto para ARGOS.

## Decisión

**Añadir la capa de Memoria Multidimensional (V6) sobre V5** — aditivo, sin reescribir:

1. **Schema V6**: tabla `experience_cues` (experience_id, component_type de 17 tipos,
   value, `cue_quality`, source extracted/generated/manual) + columnas nuevas en
   `experiences`: salience, summary, entities, concepts, files, temporal_context,
   session_id, quest_id, retrieval_anchors, y las dimensiones independientes
   `association_strength`, `retrieval_strength`, `frequency` (recency derivada de
   created_at/last_used_at).

2. **Descomposición completa**: `osma-experience-record` descompone TODO al crear
   (17 tipos); `osma-experience-analyze` para backfill + generación de anchors.
   `reasoning` y `action` son cues propios (action distinto de solution);
   `concept` = términos conceptuales largos (≥5 chars, no stopword, no tecnología);
   `pattern` = patrones cuyo título aparece en el texto de la experiencia.

3. **cue_quality (IDF inverso)**: `q = clamp(0.1, 1.0, 1/(1+log(1+n)))` donde n =
   experiencias que comparten el valor. Cue rara → alta; cue compartida por muchas → baja.

4. **Convergencia no lineal** (`osma-cue-search`):
   `episode_activation_score = clamp(0, 10, Σqᵢ + 0.1·k²·avg(q))` — el término k² da el
   efecto superlineal: pocas pistas independientes que convergen hacen sobresalir al
   episodio correcto. Competencia entre episodios por ranking (score, salience,
   confidence). Winner = top con score≥1.0, no failed, no obsolete.

5. **PATTERN COMPLETION completo**: activación por cues → **propagación asociativa de 1
   salto** por `experience_links` (decaimiento por distancia y peso, `via_association:
   true`) → convergencia → competencia → winner con reconstruction
   (summary/solution/outcome/validation) → contexto para ARGOS.

6. **Salience funcional**: señales operativas verificables (sin simular emociones):
   base por tipo (decision 0.7, bugfix 0.6, verdict 0.5…) + verified +0.15 /
   failed +0.05 + arch-decision +0.2 + corrección fuerte (reward<−0.6) +0.10 +
   reuso (éxito o fracaso repetido) +0.03. Clamp 0..1.

7. **Dimensiones independientes**: importance (validación), confidence (validación/
   corrección), association_strength (promedio de pesos de links), salience (señales
   funcionales), retrieval_strength (+0.05 reuso éxito / −0.05 fallo, cap 0..1),
   frequency (incrementa en cada reuso), recency (derivada), reward (resultado). Cada una
   se actualiza en su propio camino — nunca se deriva de otra.

8. **Retrieval anchors**: para experiencias con `retrieval_routes < 3` Y (importance≥0.5
   O salience≥0.6) se generan anchors automáticos: alias de tabla estática
   ("permission denied"→["access denied","acceso denegado",…], "rls"→["row level
   security",…], "login"→["autenticacion","auth",…]) + tokens distintivos (IDF n≤3).
   `osma-anchor-add` permite anchors manuales.

9. **Integración ARGOS**: `agent_settled` pasa session_id/quest_id; tool
   `argos_cue_search` (multi-cue, separado por comas, con winner + reconstrucción);
   `argos_experience_search` muestra salience y retrieval_routes.

## Alternativas consideradas

| Alternativa | Pros | Contras |
|---|---|---|
| **A. Capa V6 multidimensional (ELEGIDA)** | Múltiples rutas reales, convergencia no lineal, salience funcional, anchors, dimensiones separadas — cumple el spec completo; aditivo | 7 comandos nuevos; 17 tipos de cues que mantener |
| **B. Mejorar FTS5 con más keywords** | Simple | Sigue siendo "texto → búsqueda", no "experiencia → componentes → rutas"; sin convergencia ni salience |
| **C. Embeddings semánticos ahora** | Recall semántico superior | Rompe stdlib-only; la convergencia de cues ya da lo esencial; Fase 2 pendiente |
| **D. Simular emociones para salience** | Atractivo conceptual | Prohibido explícitamente por el spec ("no simules emociones") — salience debe ser funcional |

## Consecuencias

**Positivas**:
- Un episodio se recupera desde CUALQUIERA de sus partes: RLS, Vivi, permission denied,
  middleware+Supabase — sin depender de las palabras originales
- La convergencia de pistas independientes hace sobresalir al episodio correcto
  (no lineal, verificado: 4 cues > 2x sobre 1 cue)
- PATTERN COMPLETION con reconstrucción completa para ARGOS
- Salience funcional sin emociones; dimensiones que evolucionan independientemente
- Cero dependencias nuevas; migración V5→V6 idempotente sin pérdida
- 49/49 tests (9 de multidimensionalidad: rutas múltiples, cue_quality, convergencia,
  anchors, salience, no-aceptación ciega, propagación)

**Negativas / Riesgos**:
- 17 tipos de cues amplían la superficie de descomposición (más código, más tests)
- La propagación asociativa es de 1 salto — la propagación multi-nivel completa queda
  como mejora futura (Fase 2 junto con embeddings)
- cue_quality depende de la población de experiencias (IDF) — en proyectos nuevos con
  pocas experiencias todas las cues puntúan alto; se normaliza con el uso
- Los anchors dependen de la tabla de alias estática (mantenible) + tokens distintivos

## Razón (por qué esta)

El harness busca **hechos antes de actuar** — y "la experiencia se recuerda por sus
partes, no por sus palabras" es un hecho de la memoria humana que el spec quiere replicar
funcionalmente. V5 dio la estructura de la experiencia; V6 da la **evocación
multidimensional**: cada componente es una puerta de entrada, la convergencia de varias
puertas independientes eleva la confianza, la salience funcional prioriza lo significativo
sin inventar emociones, y los anchors rescatan lo importante que sería difícil de evocar.
El paso final (PATTERN COMPLETION con propagación) convierte la recuperación en un
proceso de reconstrucción, no de búsqueda textual.

---
*Memoria: al registrar, guardar en arnes.db `amarant/arch-decisions`*
