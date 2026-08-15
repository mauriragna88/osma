# ADR-010 — OSMA V7: Episode Pattern Completion y Reactivación

> **Fecha**: 2026-08-12
> **Autor**: Usuario + Sisyphus (orquestación) + Ansem (backend) + Kuja (QA) + Tywin (verificación)
> **Estado**: `accepted`
> **Supersede**: (ninguno — extiende ADR-009 sin invalidarlo)

---

## Contexto

ADR-009 entregó la memoria multidimensional (V6): experiencias descompuestas en cues con
cue_quality y recuperación combinatoria no lineal. Pero el spec de este quest exigía el
comportamiento central que faltaba: **reconstrucción del recuerdo desde una parte** y,
sobre todo, **reactivación** — "recordar también modifica la memoria".

Tres requisitos del spec quedaban sin cubrir:
1. **Pattern Completion completo**: "una parte del recuerdo evoca el recuerdo completo" —
   un fragmento mínimo ("aquella vez que falló el login", "el problema de RLS") debe
   reconstruir el episodio correcto, con `episode_id` como nodo agrupador.
2. **Competencia entre recuerdos**: una palabra compartida por cientos de experiencias
   (ej. `Supabase` en EduClave/Bienesr/POS/RES) NO debe reconstruir arbitrariamente — las
   pistas combinadas deben decidir el ganador (cue overlap + association strength +
   recency + importance + project context + episode coherence).
3. **Reactivación (el gap crítico)**: recuperar correctamente un episodio debe REFORZAR
   las conexiones de los nodos participantes (RLS↔EPISODE y asociaciones internas) —
   OSMA no debe ser una memoria estática.

## Decisión

**Añadir la capa de Episode Pattern Completion (V7) sobre V6** — aditivo:

1. **`episode_id` como identidad de episodio**: formato `EPISODE_XXXX` (zero-padded),
   expuesto en cada resultado y en el winner de `osma_cue_search`. La reconstrucción
   completa vive en `osma-episode` (nuevo comando): devuelve el episodio entero —
   summary/situation/reasoning/conclusion/action/outcome, validation, reward, todas las
   dimensiones (confidence/importance/salience/retrieval_strength/frequency/
   association_strength), cues con coactivation_count, routes, related_experiences
   (links con weight), related_observations, y patrones que lo originaron.

2. **Reactivación post-recuperación** (`_reactivate`): cuando `osma_cue_search` produce
   winner, se refuerza la memoria:
   - winner: `frequency +1`, `retrieval_strength +0.03` (cap 1.0)
   - cues matcheados del winner: `coactivation_count +1`, `last_coactivated_at = now`
   - links winner↔co-activados: weight `+0.05` (cap 1.0) Y `coactivation_count +1`
     (fix Tywin: antes solo subía el peso, no la coactivación)
   - La respuesta expone `reactivation: {episode_id, reinforced_links, cues_reinforced,
     retrieval_strength_delta, frequency_delta}` — null si no hay winner.
   - Un fallo de escritura NUNCA rompe la búsqueda (try/except → reactivation null).

3. **Competencia con señales completas** (`competition_score`, fix Tywin): el ranking
   mantiene `episode_activation_score` como factor primario y añade un tie-breaker
   compuesto con pesos configurables (W_RECENCY=0.05, W_IMPORTANCE=0.05, W_PROJECT=0.03,
   W_COHERENCE=0.02): recency_norm (1/(1+días)), importance, project_match (query.project
   vs episodio) y episode_coherence (k/3 cues matcheados). La combinación de pistas
   desambigua episodios que comparten una palabra genérica.

4. **Integración FTS5** (fix Tywin): los cues estructurados se matchean
   determinísticamente (`LIKE` sobre experience_cues — el índice correcto para la
   taxonomía de cues), y se añade un fallback FTS5 opcional (`_FTS5_FALLBACK=True`) que,
   para cues sin match estructurado, usa la ruta `recall()` existente sobre
   `observations_fts` y añade experiencias relacionadas vía `experience_observation_links`
   como entradas `via_fts: true` (participan en ranking, nunca winner sin cue directo).

5. **Integración ARGOS**: `argos_cue_search` muestra `episode_id` por resultado y la
   línea `## Reactivacion: EPISODE_XXXX — refuerzo aplicado`; tool nueva `argos_episode`
   para reconstrucción explícita del episodio completo.

## Alternativas consideradas

| Alternativa | Pros | Contras |
|---|---|---|
| **A. Capa V7 pattern completion (ELEGIDA)** | Reactivación real (recordar modifica la memoria), episode_id visible, reconstrucción completa, competencia con todas las señales, fallback FTS5 | 1 comando nuevo + extensiones; complejidad de ranking con 4 pesos |
| **B. Solo mejorar el winner con más campos** | Simple | Sin reactivación (el gap central del spec), sin episode_id, sin reconstrucción |
| **C. Memorizar todo el historial textual** | Nunca pierde contexto | Viola "recuerdos ricos vs ruidosos" — costo de tokens; sin estructura |
| **D. Winner determinista sin competencia (siempre el más reciente)** | Simple | Ignora cue overlap/coherence — el spec exige desambiguación por pistas combinadas |

## Consecuencias

**Positivas**:
- "Una parte del recuerdo evoca el recuerdo completo": fragmentos mínimos reconstruyen
  el episodio correcto (probado: `{permission denied, rls}` → episodio RLS completo)
- **La memoria cambia al recordar**: la reactivación refuerza lo que se recupera
  (frequency, retrieval_strength, cues, links) — OSMA deja de ser estática
- La competencia combina todas las señales del spec (overlap, fuerza asociativa,
  recencia, importancia, contexto de proyecto, coherencia episódica)
- `osma-episode` da la reconstrucción completa para ARGOS (contexto listo para el agente)
- Integración FTS5 como fallback documentado; cero dependencias nuevas
- 58 tests (9 de episodios: reactivación, episode_id, refuerzo de cues/links,
  reconstrucción, competencia, sin-winner→null, migración idempotente)

**Negativas / Riesgos**:
- La reactivación escribe en cada búsqueda con winner (write-amplification) — mitigado
  con commits puntuales y try/except
- Los pesos del competition_score son tunables manuales — requieren calibración con uso
  real (como GAMMA de convergencia)
- El fallback FTS5 depende de `experience_observation_links` (puente obs↔experiencia) —
  si no hay observaciones ligadas, el fallback no aporta
- Los 2 timeouts de la suite completa son flaky de infraestructura (arranque Python bajo
  carga), no fallos de lógica — confirmados en verde al correr aislados

## Razón (por qué esta)

El spec pide "evocar una experiencia completa a partir de una parte significativa de
ella" — y el mecanismo clave que lo distingue de "buscar recuerdos similares" es la
**reactivación**: la recuperación debe dejar una huella. V7 implementa el ciclo completo
del spec: CUE → ACTIVACIÓN → PROPAGACIÓN → COMPETENCIA → EPISODIO GANADOR → PATTERN
COMPLETION → CONTEXTO RECONSTRUIDO — y cierra el bucle con el refuerzo post-recuperación
que hace que la topología siga emergiendo del uso. La competencia con todas las señales
(no solo overlap) responde al requisito de no reconstruir arbitrariamente cuando una
palabra es compartida por muchos proyectos. Y `episode_id` da al agente la identidad
estable del recuerdo que puede nombrar, reconstruir y reutilizar.

---
*Memoria: al registrar, guardar en arnes.db `amarant/arch-decisions`*
