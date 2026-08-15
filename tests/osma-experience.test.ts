import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { runBrain } from "./runBrain.js";

// OSMA V5 Experience Memory — prueba que la memoria aprende DE experiencia validada.
// Mismo patron de osma-associative.test.ts: usa osma_brain.py (raiz) y ejecuta el engine de verdad
// temporal y ejecuta el engine de verdad via runBrain (stdin JSON, sin mocks).
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));

function makeFakeArnesProject(): string {
  const dir = mkdtempSync(join(tmpdir(), "osma-exp-"));
  mkdirSync(join(dir, ".arnes"), { recursive: true });

  return dir;
}

/** init + assert ok. Devuelve el dir listo. */
async function initProject(dir: string): Promise<string> {
  const res = await runBrain(dir, ["init"]);
  assert.equal(res.ok, true, `init fallo: ${res.error}`);
  return dir;
}

/** record helper: devuelve el id de la experiencia creada. */
async function record(
  dir: string,
  data: Record<string, unknown>
): Promise<number> {
  const res = await runBrain(dir, ["osma-experience-record", "-"], data);
  assert.equal(res.ok, true, `osma-experience-record fallo: ${res.error}`);
  return (res.data as { id: number }).id;
}

/** expStats helper: osma-experience-stats. */
async function expStats(dir: string): Promise<any> {
  const res = await runBrain(dir, ["osma-experience-stats"]);
  assert.equal(res.ok, true, `osma-experience-stats fallo: ${res.error}`);
  return res.data as any;
}

/** search helper: array de {id, applicability, derived_from, source_pattern, ...}. */
async function search(
  dir: string,
  query: string,
  project = "-",
  agent = "-",
  limit = "5"
): Promise<any[]> {
  const res = await runBrain(
    dir,
    ["osma-experience-search", query, project, agent, limit]
  );
  assert.equal(res.ok, true, `osma-experience-search fallo: ${res.error}`);
  return res.data as any[];
}

/** patternDetect helper. */
async function patternDetect(dir: string): Promise<any> {
  const res = await runBrain(dir, ["osma-pattern-detect"]);
  assert.equal(res.ok, true, `osma-pattern-detect fallo: ${res.error}`);
  return res.data as any;
}

// ---------------------------------------------------------------------------
// a. Status derivado del reward: un resultado validado se vuelve conocimiento
//    confiable inmediatamente (verified) y uno fallido se marca failed.
// ---------------------------------------------------------------------------
test("OSMA deriva el estado de validacion del reward: reward 1.0 -> verified y reward -1.0 -> failed", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, {
    situation: "Error autenticacion alumnos login supabase",
    reward: 1.0, project: "educlave", topic_key: "educlave/auth",
  });
  await record(dir, {
    situation: "Error migracion datos usuarios supabase",
    reward: -1.0, project: "educlave", topic_key: "educlave/db",
  });

  const stats = await expStats(dir);
  assert.equal(stats.experiences, 2, `experiences == 2 (got ${JSON.stringify(stats)})`);
  assert.equal(stats.verified, 1, `un reward 1.0 debe quedar verified: ${JSON.stringify(stats)}`);
  assert.equal(stats.failed, 1, `un reward -1.0 debe quedar failed: ${JSON.stringify(stats)}`);
  assert.equal(stats.avg_reward, 0.0, `promedio de 1.0 y -1.0 es 0.0 (got ${stats.avg_reward})`);
});

// ---------------------------------------------------------------------------
// b. Reconocimiento de patrones: 2 experiencias validadas con >=2 entidades
//    compartidas -> pattern-detect crea el patron y stats lo refleja.
// ---------------------------------------------------------------------------
test("OSMA reconoce patrones: 2 experiencias validadas con entidades compartidas -> pattern-detect crea patron", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, {
    situation: "Error acceso supabase rls login alumnos",
    action: "revisar policy rls user_id", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });
  await record(dir, {
    situation: "Error acceso supabase rls login sesiones",
    action: "revisar policy rls user_id", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });

  const before = await expStats(dir);
  assert.equal(before.patterns, 0, `aun no hay patrones: ${JSON.stringify(before)}`);

  const detect = await patternDetect(dir);
  assert.ok(detect.patterns_created >= 1, `debe crear al menos 1 patron: ${JSON.stringify(detect)}`);
  assert.ok(detect.experiences_covered >= 2, `el patron cubre las 2 experiencias: ${JSON.stringify(detect)}`);

  const after = await expStats(dir);
  assert.ok(after.patterns >= 1, `stats().patterns >= 1 (got ${after.patterns})`);
});

// ---------------------------------------------------------------------------
// c. Recuperacion por experiencia (X' -> experiencia X): un problema parecido
//    recupera la solucion validada (apply) sin partir de cero.
// ---------------------------------------------------------------------------
test("OSMA recupera por experiencia: problema parecido -> recuerdo validado (apply) sin partir de cero", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const a = await record(dir, {
    situation: "Error autenticacion alumnos login supabase",
    action: "revisar tabla auth usuarios", reward: 1.0,
    project: "educlave", topic_key: "educlave/auth",
  });
  // No relacionada: otro proyecto y otras palabras (comparte 0 entidades).
  const b = await record(dir, {
    situation: "configuracion tema oscuro interfaz", reward: 1.0,
    project: "otra-app", topic_key: "otra-app/theme",
  });

  const rows = await search(dir, "problema acceso login alumnos", "educlave", "-", "5");
  assert.ok(rows.length >= 1, `la busqueda debe traer la experiencia A: ${JSON.stringify(rows)}`);
  assert.equal(rows[0].id, a, `la experiencia validada del mismo proyecto va primero`);
  assert.equal(rows[0].applicability, "apply", `verified + reward>0 + mismo proyecto = 'apply': ${JSON.stringify(rows[0])}`);
  assert.ok(!rows.some((r) => r.id === b), `la experiencia no relacionada no se recomienda`);
});

// ---------------------------------------------------------------------------
// d. No-aceptacion ciega: una experiencia failed NUNCA se recomienda (apply).
// ---------------------------------------------------------------------------
test("OSMA no acepta a ciegas: experiencia failed nunca es 'apply' en la busqueda", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const a = await record(dir, {
    situation: "Error autenticacion alumnos login supabase", reward: 1.0,
    project: "educlave", topic_key: "educlave/auth",
  });
  const c = await record(dir, {
    situation: "Error autenticacion alumnos login supabase token", reward: -1.0,
    project: "educlave", topic_key: "educlave/auth",
  });

  const rows = await search(dir, "problema acceso login alumnos", "educlave", "-", "5");
  const hitA = rows.find((r) => r.id === a);
  const hitC = rows.find((r) => r.id === c);
  assert.ok(hitA, `la experiencia validada sigue siendo recuperable`);
  assert.equal(hitA.applicability, "apply", `la buena sigue siendo 'apply'`);
  assert.ok(hitC, `la experiencia failed aparece (comparte entidades)`);
  assert.notEqual(hitC.applicability, "apply", `la experiencia failed NUNCA es apply: ${JSON.stringify(hitC)}`);
  assert.equal(hitC.applicability, "obsolete", `la experiencia failed es 'obsolete': ${JSON.stringify(hitC)}`);
});

// ---------------------------------------------------------------------------
// e. derived_from: el resultado explica de que experiencias nace el patron
//    (que cree y por que).
// ---------------------------------------------------------------------------
test("OSMA explica su origen: derived_from contiene los ids fuente del patron", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "Error acceso supabase rls login alumnos",
    action: "revisar policy rls user_id", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });
  const e2 = await record(dir, {
    situation: "Error acceso supabase rls login sesiones",
    action: "revisar policy rls user_id", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });

  const detect = await patternDetect(dir);
  assert.ok(detect.patterns_created >= 1, `patron creado: ${JSON.stringify(detect)}`);

  const rows = await search(dir, "error login alumnos supabase", "educlave", "-", "5");
  assert.ok(rows.length >= 1, `la busqueda recupera experiencias del patron: ${JSON.stringify(rows)}`);
  const hit = rows.find((r) => r.source_pattern != null) || rows[0];
  assert.ok(Array.isArray(hit.derived_from), `derived_from es un array: ${JSON.stringify(hit)}`);
  assert.ok(
    hit.derived_from.includes(e1) && hit.derived_from.includes(e2),
    `derived_from incluye AMBAS experiencias fuente (${e1}, ${e2}): ${JSON.stringify(hit.derived_from)}`
  );
});

// ---------------------------------------------------------------------------
// f. Reutilizacion refuerza: success sube confidence y reuso fallido sube
//    failed_retrievals.
// ---------------------------------------------------------------------------
test("OSMA refuerza con reuso: success sube confidence y reuso fallido sube failed_retrievals", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "Error acceso supabase rls login alumnos", reward: 0.0,
    project: "educlave", topic_key: "educlave/rls",
  });

  const v1 = await runBrain(dir, ["osma-experience-validate", "-"], { id: e1, reward: 1.0 });
  assert.equal(v1.ok, true, `validate fallo: ${v1.error}`);
  assert.equal((v1.data as any).validation_status, "verified");
  const confAfterValidate = (v1.data as any).confidence as number;

  const reuse1 = await runBrain(dir, ["osma-experience-reuse", "-"], { id: e1, success: true });
  assert.equal(reuse1.ok, true, `reuse success fallo: ${reuse1.error}`);
  const reuse1Data = reuse1.data as any;
  assert.ok(reuse1Data.successful_retrievals >= 1, `successful_retrievals >= 1 (got ${reuse1Data.successful_retrievals})`);
  assert.ok(
    reuse1Data.confidence > confAfterValidate,
    `la confianza sube con el reuso exitoso: ${confAfterValidate} -> ${reuse1Data.confidence}`
  );

  const stats1 = await expStats(dir);
  assert.ok(stats1.reused_successfully >= 1, `stats().reused_successfully >= 1 (got ${stats1.reused_successfully})`);

  const e2 = await record(dir, {
    situation: "configuracion tema oscuro interfaz", reward: 1.0,
    project: "otra-app", topic_key: "otra-app/theme",
  });
  const reuse2 = await runBrain(dir, ["osma-experience-reuse", "-"], { id: e2, success: false });
  assert.equal(reuse2.ok, true, `reuse fail fallo: ${reuse2.error}`);
  const reuse2Data = reuse2.data as any;
  assert.ok(reuse2Data.failed_retrievals >= 1, `failed_retrievals >= 1 (got ${reuse2Data.failed_retrievals})`);

  const stats2 = await expStats(dir);
  assert.ok(stats2.reused_failed >= 1, `stats().reused_failed >= 1 (got ${stats2.reused_failed})`);
});

// ---------------------------------------------------------------------------
// g. Validacion posterior cambia confianza: reward positivo sube, negativo baja.
// ---------------------------------------------------------------------------
test("OSMA ajusta la confianza al validar despues: reward positivo sube, reward negativo baja", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "Error acceso supabase rls login alumnos", reward: 0.0,
    project: "educlave", topic_key: "educlave/rls",
  });

  const vPos = await runBrain(dir, ["osma-experience-validate", "-"], { id: e1, reward: 1.0 });
  assert.equal(vPos.ok, true, `validate +1.0 fallo: ${vPos.error}`);
  assert.equal((vPos.data as any).validation_status, "verified");
  assert.ok((vPos.data as any).confidence > 0.4, `confianza sube tras reward 1.0 (got ${(vPos.data as any).confidence})`);

  const e2 = await record(dir, {
    situation: "configuracion tema oscuro interfaz", reward: 0.0,
    project: "otra-app", topic_key: "otra-app/theme",
  });
  const vNeg = await runBrain(dir, ["osma-experience-validate", "-"], { id: e2, reward: -1.0 });
  assert.equal(vNeg.ok, true, `validate -1.0 fallo: ${vNeg.error}`);
  assert.equal((vNeg.data as any).validation_status, "failed");
  assert.ok((vNeg.data as any).confidence < 0.4, `confianza baja tras reward -1.0 (got ${(vNeg.data as any).confidence})`);
});

// ---------------------------------------------------------------------------
// h. context_mismatch: proyecto distinto + sin solape de topico -> ARGOS verifica
//    equivalencia de contexto antes de reusar.
// ---------------------------------------------------------------------------
test("OSMA verifica el contexto: proyecto diferente y sin solape de topico -> context_mismatch", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "Error autenticacion alumnos login supabase", reward: 1.0,
    project: "educlave", topic_key: "educlave/auth",
  });
  const e2 = await record(dir, {
    situation: "Error autenticacion alumnos login sesiones supabase", reward: 1.0,
    project: "educlave", topic_key: "educlave/auth",
  });

  const detect = await patternDetect(dir);
  assert.ok(detect.patterns_created >= 1, `patron creado: ${JSON.stringify(detect)}`);

  // El query comparte UNA entidad (supabase) -> la experiencia entra al pool via
  // patron, pero el proyecto del filtro difiere y el topico no matchea >=2.
  const rows = await search(dir, "supabase configuracion tema", "otro-proyecto", "-", "5");
  assert.ok(rows.length >= 1, `la experiencia entra al pool via patron: ${JSON.stringify(rows)}`);
  assert.ok(!rows.some((r) => r.applicability === "apply"), `nada se recomienda para reuso ciego`);
  const mismatch = rows.find((r) => r.id === e2);
  assert.ok(mismatch, `la experiencia del proyecto distinto aparece en los resultados`);
  assert.equal(
    mismatch.applicability, "context_mismatch",
    `proyecto != y topico != -> context_mismatch: ${JSON.stringify(mismatch)}`
  );
});

// ---------------------------------------------------------------------------
// i. Migracion V5 idempotente: osma-migrate x2 sin error y experiences intactas.
// ---------------------------------------------------------------------------
test("OSMA migra V5 idempotente: osma-migrate x2 sin error y experiences intactas", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, {
    situation: "Error acceso supabase rls login alumnos", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });
  await record(dir, {
    situation: "Error acceso supabase rls login sesiones", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });

  const before = await expStats(dir);
  assert.equal(before.experiences, 2, `hay 2 experiencias antes de migrar`);

  const m1 = await runBrain(dir, ["osma-migrate"]);
  assert.equal(m1.ok, true, `osma-migrate (1ra) fallo: ${m1.error}`);
  assert.ok(
    (m1.data as any).experience_links_backfilled >= 1,
    `la migracion enlaza las experiencias V5: ${JSON.stringify(m1.data)}`
  );

  const m2 = await runBrain(dir, ["osma-migrate"]);
  assert.equal(m2.ok, true, `osma-migrate (2da, idempotente) fallo: ${m2.error}`);

  const after = await expStats(dir);
  assert.equal(after.experiences, 2, `las experiences no cambian tras la 2da migracion`);
});

// ---------------------------------------------------------------------------
// j. Taxonomia V5 completa (remediacion Tywin): los 6 estados se derivan del
//    reward con la misma logica en record y en validate. Un reward por estado
//    => cada contador de stats queda en exactamente 1.
// ---------------------------------------------------------------------------
test("OSMA taxonomia completa: rewards [1.0,0.5,0.2,0.0,-0.5,-1.0] -> verified, partial, hypothesis, proposal, attempted y failed (1 cada uno)", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const rewards = [1.0, 0.5, 0.2, 0.0, -0.5, -1.0];
  for (let i = 0; i < rewards.length; i++) {
    await record(dir, {
      situation: `Experiencia taxonomia caso ${i}`,
      reward: rewards[i], project: "educlave", topic_key: `educlave/taxo${i}`,
    });
  }

  const stats = await expStats(dir);
  assert.equal(stats.experiences, 6, `experiences == 6 (got ${JSON.stringify(stats)})`);
  assert.equal(stats.verified, 1, `reward 1.0 -> verified (got ${JSON.stringify(stats)})`);
  assert.equal(stats.partial, 1, `reward 0.5 -> partial (got ${JSON.stringify(stats)})`);
  assert.equal(stats.hypothesis, 1, `reward 0.2 -> hypothesis (got ${JSON.stringify(stats)})`);
  assert.equal(stats.proposal, 1, `reward 0.0 -> proposal (got ${JSON.stringify(stats)})`);
  assert.equal(stats.attempted, 1, `reward -0.5 -> attempted (got ${JSON.stringify(stats)})`);
  assert.equal(stats.failed, 1, `reward -1.0 -> failed (got ${JSON.stringify(stats)})`);
});

// ---------------------------------------------------------------------------
// k. Boundaries de reward (remediacion Tywin): 0.4 es partial (limite inferior
//    inclusive), 0.9 es verified (limite inferior inclusive) y -0.6 es
//    attempted (limite superior inclusive de attempted; no failed).
// ---------------------------------------------------------------------------
test("OSMA boundaries de reward: 0.4 partial, 0.9 verified y -0.6 attempted (limites inclusivos)", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, {
    situation: "Boundary limite inferior de partial", reward: 0.4,
    project: "educlave", topic_key: "educlave/boundary-partial",
  });
  await record(dir, {
    situation: "Boundary limite inferior de verified", reward: 0.9,
    project: "educlave", topic_key: "educlave/boundary-verified",
  });
  await record(dir, {
    situation: "Boundary limite superior de attempted", reward: -0.6,
    project: "educlave", topic_key: "educlave/boundary-attempted",
  });

  const stats = await expStats(dir);
  assert.equal(stats.experiences, 3, `experiences == 3 (got ${JSON.stringify(stats)})`);
  assert.equal(stats.partial, 1, `reward 0.4 (>= 0.4) debe ser partial: ${JSON.stringify(stats)}`);
  assert.equal(stats.verified, 1, `reward 0.9 (>= 0.9) debe ser verified: ${JSON.stringify(stats)}`);
  assert.equal(stats.attempted, 1, `reward -0.6 (> -0.6) debe ser attempted, no failed: ${JSON.stringify(stats)}`);
  assert.equal(stats.failed, 0, `-0.6 NO es failed (boundary superior inclusive): ${JSON.stringify(stats)}`);
  assert.equal(stats.hypothesis, 0, `0.4 NO es hypothesis (boundary inferior inclusive): ${JSON.stringify(stats)}`);
  assert.equal(stats.proposal, 0, `ninguna experiencia quedo en proposal: ${JSON.stringify(stats)}`);
});

// ---------------------------------------------------------------------------
// l. Validacion refuerza (remediacion Tywin): osma-experience-validate con
//    reward >= 0.4 sube successful_retrievals; reward < 0 sube
//    failed_retrievals. La respuesta incluye ambos contadores.
// ---------------------------------------------------------------------------
test("OSMA validacion refuerza: reward 0.5 sube successful_retrievals y reward -1.0 sube failed_retrievals", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "Error acceso supabase rls login alumnos", reward: 0.0,
    project: "educlave", topic_key: "educlave/rls",
  });
  const e2 = await record(dir, {
    situation: "configuracion tema oscuro interfaz", reward: 0.0,
    project: "otra-app", topic_key: "otra-app/theme",
  });

  const vPos = await runBrain(dir, ["osma-experience-validate", "-"], { id: e1, reward: 0.5 });
  assert.equal(vPos.ok, true, `validate +0.5 fallo: ${vPos.error}`);
  const pos = vPos.data as any;
  assert.equal(pos.validation_status, "partial", `reward 0.5 -> partial (got ${pos.validation_status})`);
  assert.ok(pos.successful_retrievals >= 1, `successful_retrievals >= 1 tras reward >= 0.4 (got ${pos.successful_retrievals})`);
  assert.ok(pos.confidence > 0.4, `la confianza sube tras reward 0.5 (got ${pos.confidence})`);

  const vNeg = await runBrain(dir, ["osma-experience-validate", "-"], { id: e2, reward: -1.0 });
  assert.equal(vNeg.ok, true, `validate -1.0 fallo: ${vNeg.error}`);
  const neg = vNeg.data as any;
  assert.equal(neg.validation_status, "failed", `reward -1.0 -> failed (got ${neg.validation_status})`);
  assert.ok(neg.failed_retrievals >= 1, `failed_retrievals >= 1 tras reward < 0 (got ${neg.failed_retrievals})`);

  const stats = await expStats(dir);
  assert.ok(stats.reused_successfully >= 1, `stats().reused_successfully >= 1 (got ${stats.reused_successfully})`);
  assert.ok(stats.reused_failed >= 1, `stats().reused_failed >= 1 (got ${stats.reused_failed})`);
});

// ---------------------------------------------------------------------------
// m. Supersesion contextual - regresion (remediacion Tywin): mismo proyecto
//    SOLO ya NO obsoleta. Una experiencia verified mas nueva en el mismo
//    proyecto pero con entidades NO relacionadas (comparten 0-1 entidades)
//    deja a la anterior con applicability='apply'.
// ---------------------------------------------------------------------------
test("OSMA supersesion contextual (regresion): mismo proyecto con entidades no relacionadas NO obsoleta a la anterior", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const a = await record(dir, {
    situation: "error login alumnos supabase", reward: 1.0,
    project: "educlave", topic_key: "educlave/auth",
  });
  const b = await record(dir, {
    situation: "configuracion tema oscuro", reward: 1.0,
    project: "educlave", topic_key: "educlave/theme",
  });

  const rows = await search(dir, "error login alumnos", "educlave", "-", "5");
  const hitA = rows.find((r) => r.id === a);
  assert.ok(hitA, `la experiencia A debe ser recuperable: ${JSON.stringify(rows)}`);
  assert.equal(hitA.applicability, "apply", `mismo proyecto pero entidades no relacionadas -> apply (NO obsolete): ${JSON.stringify(hitA)}`);
  assert.ok(!rows.some((r) => r.id === b), `la experiencia B (tema oscuro) no entra al pool del query de login: ${JSON.stringify(rows)}`);
});

// ---------------------------------------------------------------------------
// n. Supersesion contextual - real (remediacion Tywin): >= 2 entidades
//    compartidas en el mismo proyecto SI obsoletan a la anterior cuando la
//    nueva es verified con reward > 0.
// ---------------------------------------------------------------------------
test("OSMA supersesion contextual real: experiencia nueva verified con >=2 entidades compartidas obsoleta a la anterior", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const c = await record(dir, {
    situation: "error login alumnos supabase", reward: 1.0,
    project: "educlave", topic_key: "educlave/auth",
  });
  const d = await record(dir, {
    situation: "error login alumnos supabase token", reward: 1.0,
    project: "educlave", topic_key: "educlave/auth",
  });

  const rows = await search(dir, "error login alumnos", "educlave", "-", "5");
  const hitC = rows.find((r) => r.id === c);
  const hitD = rows.find((r) => r.id === d);
  assert.ok(hitC, `la experiencia C debe aparecer en la busqueda: ${JSON.stringify(rows)}`);
  assert.ok(hitD, `la experiencia D (nueva) debe aparecer en la busqueda: ${JSON.stringify(rows)}`);
  assert.equal(hitC.applicability, "obsolete", `C comparte >=2 entidades con la nueva verified D -> obsolete: ${JSON.stringify(hitC)}`);
  assert.equal(hitD.applicability, "apply", `D es verified + reward>0 + proyecto match -> apply: ${JSON.stringify(hitD)}`);
});

// ---------------------------------------------------------------------------
// o. Provenance semantica (remediacion Tywin): osma-patterns devuelve
//    {id, title, check_procedure, confidence, derived_from, sources[]} con la
//    situation de cada experiencia fuente.
// ---------------------------------------------------------------------------
test("OSMA provenance: osma-patterns expone derived_from con ambos ids y sources con situation de cada fuente", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "Error acceso supabase rls login alumnos",
    action: "revisar policy rls user_id", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });
  const e2 = await record(dir, {
    situation: "Error acceso supabase rls login sesiones",
    action: "revisar policy rls user_id", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });

  const detect = await patternDetect(dir);
  assert.ok(detect.patterns_created >= 1, `patron creado: ${JSON.stringify(detect)}`);

  const res = await runBrain(dir, ["osma-patterns"]);
  assert.equal(res.ok, true, `osma-patterns fallo: ${res.error}`);
  const patterns = res.data as any[];
  assert.ok(Array.isArray(patterns) && patterns.length >= 1, `osma-patterns devuelve array no vacio: ${JSON.stringify(patterns)}`);

  const pat = patterns.find((p) =>
    Array.isArray(p.derived_from) &&
    p.derived_from.includes(e1) && p.derived_from.includes(e2)
  );
  assert.ok(pat, `debe existir un patron derivado de ${e1} y ${e2}: ${JSON.stringify(patterns)}`);
  assert.ok(Array.isArray(pat.sources) && pat.sources.length === 2, `sources tiene 2 entradas: ${JSON.stringify(pat.sources)}`);
  for (const src of pat.sources) {
    assert.ok(src && typeof src.situation === "string" && src.situation.length > 0,
      `cada source tiene situation no vacia: ${JSON.stringify(src)}`);
    assert.ok([e1, e2].includes(src.id), `source.id esta entre los ids fuente: ${JSON.stringify(src)}`);
  }
});

// ---------------------------------------------------------------------------
// p. Estado intermedio (remediacion Tywin): una experiencia 'hypothesis'
//    (reward 0.2) que comparte entidades con una verified JAMAS se recomienda
//    como 'apply' en la busqueda; queda 'caution'.
// ---------------------------------------------------------------------------
test("OSMA estado intermedio: experiencia hypothesis compartiendo entidades con una verified nunca es 'apply'", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const v = await record(dir, {
    situation: "Error acceso supabase rls login alumnos", reward: 1.0,
    project: "educlave", topic_key: "educlave/rls",
  });
  const h = await record(dir, {
    situation: "Error acceso supabase rls login sesiones", reward: 0.2,
    project: "educlave", topic_key: "educlave/rls",
  });

  const rows = await search(dir, "error login alumnos supabase", "educlave", "-", "5");
  const hitV = rows.find((r) => r.id === v);
  const hitH = rows.find((r) => r.id === h);
  assert.ok(hitV, `la experiencia verified debe aparecer: ${JSON.stringify(rows)}`);
  assert.equal(hitV.applicability, "apply", `verified + reward>0 + proyecto match -> apply: ${JSON.stringify(hitV)}`);
  assert.ok(hitH, `la hypothesis aparece porque comparte entidades con el query: ${JSON.stringify(rows)}`);
  assert.notEqual(hitH.applicability, "apply", `hypothesis NUNCA es apply: ${JSON.stringify(hitH)}`);
  assert.equal(hitH.applicability, "caution", `hypothesis (estado intermedio) -> caution: ${JSON.stringify(hitH)}`);
});
