# ADR-008 — OSMA V5: Memoria de Experiencias Validadas (experiencia → conocimiento reutilizable)

> **Fecha**: 2026-08-12
> **Autor**: Usuario + Sisyphus (orquestación) + Ansem (backend) + Kuja (QA) + Tywin (verificación)
> **Estado**: `accepted`
> **Supersede**: (ninguno — extiende ADR-007 sin invalidarlo)

---

## Contexto

ADR-007 entregó OSMA V4: memoria asociativa con co-activación, propagación de activación,
sueño/consolidación y contradicciones. Pero el requisito central del usuario quedaba a
mitad de camino: **ARGOS no solo acumula información, acumula experiencia**.

El problema: cuando ARGOS resolvía un problema, la solución quedaba enterrada en
observaciones episódicas sin estructura — y la próxima vez que aparecía un problema
parecido, ARGOS **volvía a razonar desde cero** en vez de reutilizar lo que ya funcionó.

El spec exigía transformar:

```
ARGOS resolviendo problemas
→ ARGOS adquiriendo experiencia resolviendo problemas
```

Primera vez: `problema → análisis → prueba → error → solución → validación`.
Siguiente: `problema parecido → reconocimiento → recuerdo → solución validada`.
Después de muchas: `patrón → conocimiento consolidado → respuesta eficiente`.

## Decisión

**Añadir la capa de Experiencias Validadas (V5) sobre el motor OSMA V4** — aditivo, sin
reescribir nada:

1. **Schema V5**: tablas `experiences` (situation→reasoning→conclusion→action→outcome +
   `reward_signal` −1..1 + `validation_status` + confidence/importance/retrievals),
   `patterns` (patrón abstraído + `check_procedure` + `source_experience_ids` = provenance),
   `experience_links` (sinapsis exp↔exp), `experience_observation_links` (puente con V4).

2. **Taxonomía de validación completa** (el spec: propuesta / hipótesis / intentada /
   parcialmente exitosa / verificada / fallida):
   - `proposal` (reward 0.0) · `hypothesis` (0 < r < 0.4) · `attempted` (−0.6 ≤ r < 0)
   - `partial` (0.4 ≤ r < 0.9) · `verified` (r ≥ 0.9) · `failed` (r < −0.6)
   - Mismo mapeo en record y validate (helper único `_status_from_reward`).

3. **Reward como señal de aprendizaje**: validación positiva (recompensa ≥ 0.4)
   incrementa `successful_retrievals` + confianza; negativa incrementa `failed_retrievals`
   y baja confianza. Las experiencias exitosas dejan huella más fuerte que las no
   verificadas.

4. **Reconocimiento de patrones** (`osma-pattern-detect`): clusteriza experiencias
   verified/partial con reward > 0 por solapamiento de entidades (≥2, union-find);
   cada cluster ≥2 genera un patrón con `check_procedure` = acción de la de mayor
   confianza (ej. `PATTERN problemas de acceso Supabase ↔ CHECK_RLS_POLICIES`).

5. **Episodios → conocimiento con provenance** (`osma-patterns`): cada patrón conserva
   `source_experience_ids` → ARGOS sabe **qué cree y por qué lo aprendió** (derived_from).

6. **Recuperación basada en experiencia** (`osma-experience-search`): pistas → patrón →
   experiencias → aplicabilidad (`apply` / `caution` / `obsolete` / `context_mismatch`) →
   ranking (verified-apply primero, confianza, recencia). **No aceptación ciega**:
   contexto/proyecto/tecnología/contradicción verificados antes de reutilizar.

7. **Supersesión contextual** (fix Tywin): una experiencia verificada más nueva solo
   obsoleta a una anterior si comparte patrón O (mismo proyecto Y ≥2 entidades) — no por
   proyecto solo.

8. **Integración ARGOS**: `agent_settled` registra la experiencia real de cada turno
   (reward +0.5 éxito / −1.0 fallo, reasoning/conclusion derivados); `before_agent_start`
   inyecta "ARGOS EXPERIENCIA PREVIA" para reutilizar antes de razonar de cero; tool
   `argos_experience_search` para consulta explícita.

## Alternativas consideradas

| Alternativa | Pros | Contras |
|---|---|---|
| **A. Nueva capa de experiencias V5 (ELEGIDA)** | Estructura explícita situación→razonamiento→solución→resultado; validation_status diferencia confianza; patrones con provenance; aditivo sobre V4 | 6 comandos nuevos que mantener; requiere validación explícita para volverse knowledge |
| **B. Solo reforzar observaciones existentes (reinforce)** | Cero schema nuevo | No distingue problema/solución/resultado; no permite reconocimiento de patrones ni reutilización dirigida; el reward no tendría dónde vivir |
| **C. LLM para clasificar cada experiencia** | Resúmenes ricos | Caro en tokens; el harness prefiere determinismo; la taxonomía por reward es suficiente |
| **D. Guardar soluciones como patrones de texto plano** | Simple | Sin pesos, sin validación, sin provenance — recae en el error original de "texto que no aprende" |

## Consecuencias

**Positivas**:
- ARGOS deja de empezar desde cero: problema parecido → recuerdo → solución validada
- Las soluciones verificadas se refuerzan (successful_retrievals, confianza) y las fallidas
  nunca se recomiendan como `apply`
- La abstracción emerge sola: N experiencias verificadas similares → patrón + procedimiento
- Provenance: ARGOS sabe por qué cree lo que cree (derived_from)
- Cero dependencias externas nuevas; migración V4→V5 idempotente sin pérdida
- 40/40 tests (16 de experiencias: taxonomía, boundaries, refuerzo, supersesión,
  provenance, no-aceptación ciega)

**Negativas / Riesgos**:
- La calidad del `reasoning`/`conclusion` en agent_settled depende de la info del turno
  (wm.goal/quest/nextAction/errors) — no captura el razonamiento completo del agente
- Los patrones dependen de co-ocurrencia de entidades (Fase 1 sin embeddings) — sin
  similitud semántica real hasta Fase 2
- 6+1 comandos nuevos amplían la superficie del motor
- Las experiencias registradas con reward 0.0 (proposal) requieren validación explícita
  posterior para volverse conocimiento confiable

## Razón (por qué esta)

El harness busca **hechos antes de actuar** — y un hecho central es: "esta solución ya
funcionó". OSMA V4 tenía la red asociativa pero no la estructura para distinguir
"qué sé" de "qué he verificado que funciona". La capa V5 añade exactamente eso: la
relación completa situación→razonamiento→conclusión→acción→resultado con una señal de
validación (`reward_signal`) que determina cuánto merece confianza cada experiencia.

La taxonomía de 6 estados y el reward (−1..+1) son el mecanismo por el que la experiencia
se convierte en conocimiento: no basta que un agente genere una conclusión — necesita
evidencia posterior (usuario confirma, test pasa, reutilización exitosa). Y la
provenance (`derived_from`) cierra el ciclo: el conocimiento consolidado siempre recuerda
de dónde vino, cumpliendo "no solo qué cree, sino por qué lo aprendió".

---
*Memoria: al registrar, guardar en arnes.db `amarant/arch-decisions`*
