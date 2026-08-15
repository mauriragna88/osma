import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { runBrain } from "./runBrain.js";

// OSMA V6 Multidimensional Memory — prueba que CADA experiencia es alcanzable por
// MULTIPLES rutas de recuperacion (cues descompuestos + salience + anchors + IDF).
// Mismo patron de osma-experience.test.ts: usa osma_brain.py REAL
// temporal y ejecuta el engine de verdad via runBrain (stdin JSON, sin mocks).
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));

function makeFakeArnesProject(): string {
  const dir = mkdtempSync(join(tmpdir(), "osma-v6-"));
  mkdirSync(join(dir, ".arnes"), { recursive: true });

  return dir;
}

/** init + assert ok. Devuelve el dir listo. */
async function initProject(dir: string): Promise<string> {
  const res = await runBrain(dir, ["init"]);
  assert.equal(res.ok, true, `init fallo: ${res.error}`);
  return dir;
}

/** record helper: devuelve la respuesta completa (id + salience + cues_created). */
async function record(
  dir: string,
  data: Record<string, unknown>
): Promise<{ id: number; salience: number; cues_created: number }> {
  const res = await runBrain(dir, ["osma-experience-record", "-"], data);
  assert.equal(res.ok, true, `osma-experience-record fallo: ${res.error}`);
  const d = res.data as { id: number; salience: number; cues_created: number };
  return d;
}

/** analyze helper: V6 completo (sin id = todas; con id = una experiencia). */
async function analyze(dir: string, id?: number): Promise<any> {
  const args = id !== undefined ? ["osma-experience-analyze", String(id)] : ["osma-experience-analyze"];
  const res = await runBrain(dir, args);
  assert.equal(res.ok, true, `osma-experience-analyze fallo: ${res.error}`);
  return res.data as any;
}

/** cues helper: osma-cues de una experiencia (cues ordenados por cue_quality desc). */
async function cues(dir: string, id: number): Promise<any> {
  const res = await runBrain(dir, ["osma-cues", String(id)]);
  assert.equal(res.ok, true, `osma-cues fallo: ${res.error}`);
  return res.data as any;
}

/** cueSearch helper: osma-cue-search multi-cue. */
async function cueSearch(dir: string, data: Record<string, unknown>): Promise<any> {
  const res = await runBrain(dir, ["osma-cue-search", "-"], data);
  assert.equal(res.ok, true, `osma-cue-search fallo: ${res.error}`);
  return res.data as any;
}

/** routes helper: osma-routes de una experiencia. */
async function routes(dir: string, id: number): Promise<any> {
  const res = await runBrain(dir, ["osma-routes", String(id)]);
  assert.equal(res.ok, true, `osma-routes fallo: ${res.error}`);
  return res.data as any;
}

/** expStats helper: osma-experience-stats. */
async function expStats(dir: string): Promise<any> {
  const res = await runBrain(dir, ["osma-experience-stats"]);
  assert.equal(res.ok, true, `osma-experience-stats fallo: ${res.error}`);
  return res.data as any;
}

// Experiencia rica estandar V6: error login supabase rls + permission denied.
// (proyecto educlave, agente vivi, reward 1.0). Da cues de technology(rls/supabase),
// agent(vivi), error(permission denied), project(educlave), entities, validation...
const RICH_EXP = {
  situation: "error login alumnos supabase rls permission denied",
  action: "revisar policy rls user_id",
  outcome: "acceso restaurado correctamente",
  project: "educlave",
  agent: "vivi",
  reward: 1.0,
};

// ---------------------------------------------------------------------------
// a. Multiples rutas hacia el mismo episodio: UNA experiencia rica alcanzable
//    desde rls (technology), vivi (agent) y permission denied (error) — tres
//    busquedas con un cue DISTINTO recuperan la MISMA experiencia.
// ---------------------------------------------------------------------------
test("OSMA V6 da multiples rutas: rls, vivi y permission denied recuperan la MISMA experiencia", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const exp = await record(dir, {
    ...RICH_EXP, topic_key: "educlave/auth-v6a", session_id: "ses-a", quest_id: "q-a",
  });
  await analyze(dir);

  const viaRls = await cueSearch(dir, { cues: ["rls"] });
  const viaVivi = await cueSearch(dir, { cues: ["vivi"] });
  const viaDenied = await cueSearch(dir, { cues: ["permission denied"] });

  assert.ok(viaRls.results.length >= 1, `busqueda 'rls' sin resultados: ${JSON.stringify(viaRls)}`);
  assert.ok(viaVivi.results.length >= 1, `busqueda 'vivi' sin resultados: ${JSON.stringify(viaVivi)}`);
  assert.ok(viaDenied.results.length >= 1, `busqueda 'permission denied' sin resultados: ${JSON.stringify(viaDenied)}`);

  assert.ok(viaRls.results.some((r: any) => r.experience_id === exp.id),
    `'rls' debe alcanzar la experiencia ${exp.id}: ${JSON.stringify(viaRls.results)}`);
  assert.ok(viaVivi.results.some((r: any) => r.experience_id === exp.id),
    `'vivi' debe alcanzar la experiencia ${exp.id}: ${JSON.stringify(viaVivi.results)}`);
  assert.ok(viaDenied.results.some((r: any) => r.experience_id === exp.id),
    `'permission denied' debe alcanzar la experiencia ${exp.id}: ${JSON.stringify(viaDenied.results)}`);
});

// ---------------------------------------------------------------------------
// b. cue_quality discriminativa (IDF): un cue raro y distintivo tiene MAYOR
//    calidad que un cue generico compartido. Dos experiencias con "software";
//    solo una tiene "permission denied" -> la rara pesa mas.
// ---------------------------------------------------------------------------
test("OSMA cue_quality discriminativa: 'permission denied' (raro) pesa mas que 'software' (compartido)", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "error login alumnos supabase rls permission denied software",
    project: "educlave", topic_key: "educlave/auth-v6b", reward: 1.0,
  });
  const e2 = await record(dir, {
    situation: "software configuracion tema oscuro",
    project: "educlave", topic_key: "educlave/theme-v6b", reward: 1.0,
  });
  await analyze(dir);

  const c1 = await cues(dir, e1.id);
  const c2 = await cues(dir, e2.id);

  const sw1 = c1.cues.find((c: any) => c.component_type === "entity" && c.value === "software");
  const sw2 = c2.cues.find((c: any) => c.component_type === "entity" && c.value === "software");
  const pd1 = c1.cues.find((c: any) => c.component_type === "error" && c.value === "permission denied");

  assert.ok(sw1, `e1 debe tener cue entity 'software': ${JSON.stringify(c1.cues)}`);
  assert.ok(sw2, `e2 debe tener cue entity 'software': ${JSON.stringify(c2.cues)}`);
  assert.ok(pd1, `e1 debe tener cue error 'permission denied': ${JSON.stringify(c1.cues)}`);

  assert.equal(sw1.cue_quality, sw2.cue_quality,
    `el cue compartido 'software' tiene el mismo IDF en ambas experiencias: ${sw1.cue_quality} vs ${sw2.cue_quality}`);
  assert.ok(
    pd1.cue_quality > sw1.cue_quality,
    `cue raro 'permission denied' (q=${pd1.cue_quality}) > cue generico compartido 'software' (q=${sw1.cue_quality})`
  );
});

// ---------------------------------------------------------------------------
// c. Convergencia no lineal: multi-cue hace que el episodio correcto se
//    destaque (score crece mas que linealmente: 1 cue -> N cues con boost k^2).
// ---------------------------------------------------------------------------
test("OSMA convergencia no lineal: 4 cues superan por >2x al cue unico y eligen al episodio rico", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    ...RICH_EXP, topic_key: "educlave/auth-v6c",
  });
  // Solo comparte el proyecto (educlave) con e1: nada de supabase/rls/denied/vivi.
  await record(dir, {
    situation: "configuracion tema oscuro", project: "educlave",
    topic_key: "educlave/theme-v6c", reward: 0.2,
  });
  await analyze(dir);

  const single = await cueSearch(dir, { cues: ["supabase"] });
  const multi = await cueSearch(dir, { cues: ["supabase", "rls", "permission denied", "vivi"] });

  const r1 = single.results.find((r: any) => r.experience_id === e1.id);
  const r2 = multi.results.find((r: any) => r.experience_id === e1.id);
  assert.ok(r1, `la busqueda de 1 cue debe alcanzar e1: ${JSON.stringify(single.results)}`);
  assert.ok(r2, `la busqueda multi-cue debe alcanzar e1: ${JSON.stringify(multi.results)}`);

  const score1 = r1.episode_activation_score as number;
  const score2 = r2.episode_activation_score as number;
  assert.ok(score2 > score1, `multi-cue puntua mas que unico: ${score1} -> ${score2}`);
  assert.ok(
    score2 / Math.max(score1, 0.001) > 2,
    `la convergencia multi-cue es NO LINEAL (boost k^2): ratio ${score2 / Math.max(score1, 0.001)} debe ser > 2`
  );

  assert.ok(multi.winner !== null, `multi-cue debe tener winner: ${JSON.stringify(multi)}`);
  assert.equal(multi.winner.experience_id, e1.id,
    `el episodio rico es el ganador (pattern completion): ${JSON.stringify(multi.winner)}`);
});

// ---------------------------------------------------------------------------
// d. Pattern completion: la reconstruccion del winner trae summary/solution/
//    outcome/validation completos (el episodio se reconstruye entero).
// ---------------------------------------------------------------------------
test("OSMA pattern completion: winner reconstruye summary, solution, outcome y validation no vacios", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const exp = await record(dir, { ...RICH_EXP, topic_key: "educlave/auth-v6d" });
  await analyze(dir);

  const res = await cueSearch(dir, { cues: ["supabase", "rls", "permission denied", "vivi"] });

  assert.ok(res.winner !== null, `debe haber un winner (pattern completion): ${JSON.stringify(res)}`);
  assert.equal(res.winner.experience_id, exp.id, `winner es la experiencia rica: ${JSON.stringify(res.winner)}`);

  const rec = res.winner.reconstruction as Record<string, string>;
  assert.ok(typeof rec.summary === "string" && rec.summary.length > 0,
    `reconstruction.summary no vacio: ${JSON.stringify(rec)}`);
  assert.ok(typeof rec.solution === "string" && rec.solution.length > 0,
    `reconstruction.solution no vacio (action no debe faltar): ${JSON.stringify(rec)}`);
  assert.ok(typeof rec.outcome === "string" && rec.outcome.length > 0,
    `reconstruction.outcome no vacio: ${JSON.stringify(rec)}`);
  assert.ok(typeof rec.validation === "string" && rec.validation.length > 0,
    `reconstruction.validation no vacio: ${JSON.stringify(rec)}`);
});

// ---------------------------------------------------------------------------
// e. Salience funcional: refleja SIGNIFICADO (arch-decision -> alto) y NO
//    emociones; una experiencia trivial queda baja.
// ---------------------------------------------------------------------------
test("OSMA salience funcional: arch-decision + verified >= 0.6; trivial < 0.4", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const arch = await record(dir, {
    situation: "decision de arquitectura para el flujo de auth",
    project: "educlave", topic_key: "educlave/arch-decision-v6e", reward: 1.0,
  });
  const trivial = await record(dir, {
    situation: "configuracion tema", project: "educlave",
    topic_key: "educlave/theme-v6e", reward: 0.0,
  });

  assert.ok(arch.salience >= 0.6,
    `arch-decision + reward 1.0 -> salience >= 0.6 (got ${arch.salience}): ${JSON.stringify(arch)}`);
  assert.ok(trivial.salience < 0.4,
    `experiencia trivial -> salience < 0.4 (got ${trivial.salience}): ${JSON.stringify(trivial)}`);
});

// ---------------------------------------------------------------------------
// f. Anchors para recuerdo dificil: una experiencia de ALTA saliencia con pocas
//    rutas genera anchors; la de baja saliencia NO. El alias abre ruta nueva.
//    Nota del engine: los anchors solo se generan si retrieval_routes < 3, y el
//    IDF baja de 0.3 solo con n>=10 experiencias compartiendo el valor — por eso
//    se diluyen los cues compartidos con 9 experiencias espejo de baja variedad.
// ---------------------------------------------------------------------------
test("OSMA anchors para recuerdo dificil: alta saliencia + pocas rutas -> anchors generados; baja -> ninguno; el alias abre ruta", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  // (1) experiencia de BAJA saliencia (proposal, tema trivial) -> NUNCA genera anchors.
  const low = await record(dir, {
    situation: "configuracion tema oscuro", project: "educlave",
    topic_key: "educlave/theme-v6f", reward: 0.0,
  });
  // (2) 9 experiencias espejo que comparten TODOS los cues de la alta (diluyen IDF a n=10).
  for (let i = 0; i < 9; i++) {
    await record(dir, {
      situation: "permission denied", project: "educlave",
      topic_key: "educlave/arch-decision-v6f", reward: 1.0,
    });
  }
  // (3) experiencia ALTA: arch-decision + reward 1.0 -> salience 1.0; situation minima.
  const high = await record(dir, {
    situation: "permission denied", project: "educlave", agent: "vivi",
    topic_key: "educlave/arch-decision-v6f", reward: 1.0, session_id: "ses-f",
  });
  assert.ok(high.salience >= 0.6, `la experiencia alta debe tener salience >= 0.6 (got ${high.salience})`);

  const an = await analyze(dir);
  assert.ok(an.anchors_created >= 1, `analyze debe generar >= 1 anchor: ${JSON.stringify(an)}`);

  const cHigh = await cues(dir, high.id);
  const genAnchor = cHigh.cues.find((c: any) => c.component_type === "anchor" && c.source === "generated");
  assert.ok(genAnchor, `osma-cues de la alta debe mostrar cue anchor/generated: ${JSON.stringify(cHigh.cues)}`);

  const cLow = await cues(dir, low.id);
  assert.ok(!cLow.cues.some((c: any) => c.component_type === "anchor"),
    `la experiencia de baja saliencia NO genera anchors: ${JSON.stringify(cLow.cues)}`);

  // El alias abrio una ruta nueva: buscar por "access denied" (alias de permission denied).
  const viaAlias = await cueSearch(dir, { cues: ["access denied"] });
  assert.ok(
    viaAlias.results.some((r: any) => r.experience_id === high.id),
    `el alias 'access denied' debe recuperar la experiencia ${high.id}: ${JSON.stringify(viaAlias.results)}`
  );
});

// ---------------------------------------------------------------------------
// g. Estados de cue (source): record+analyze -> todos 'extracted' con tipos
//    validos; anchor manual -> source 'manual' + component_type 'anchor'.
//    Ademas osma-routes coincide con el conteo de cues con cue_quality >= 0.3.
// ---------------------------------------------------------------------------
test("OSMA estados de cue: extracted tras record+analyze; anchor manual con source manual; osma-routes consistente", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const exp = await record(dir, {
    situation: "error login alumnos supabase rls permission denied",
    action: "revisar policy rls user_id", outcome: "acceso restaurado",
    agent: "vivi", project: "educlave", topic_key: "educlave/auth-v6g",
    session_id: "ses-g", quest_id: "q-g", files: ["src/lib/auth.ts"],
  });
  await analyze(dir);

  const c1 = await cues(dir, exp.id);
  const ALLOWED = new Set([
    "project", "agent", "entity", "technology", "validation", "quest", "session", "file",
    "problem", "error", "solution", "result", "temporal", "anchor",
    // FIX 1 (Tywin): los 17 tipos de cue del spec V6 incluyen reasoning/action/concept/pattern.
    "reasoning", "action", "concept", "pattern",
  ]);
  assert.ok(c1.cues.length >= 5, `la experiencia rica debe tener varios cues: ${JSON.stringify(c1.cues)}`);
  for (const c of c1.cues) {
    assert.ok(ALLOWED.has(c.component_type),
      `component_type valido (${c.component_type}): ${JSON.stringify(c)}`);
    assert.equal(c.source, "extracted", `tras record+analyze el cue es 'extracted': ${JSON.stringify(c)}`);
  }

  const r = await routes(dir, exp.id);
  const routeCount = c1.cues.filter((c: any) => c.cue_quality >= 0.3).length;
  assert.equal(r.retrieval_routes, routeCount,
    `osma-routes.retrieval_routes (${r.retrieval_routes}) == cues con q>=0.3 (${routeCount})`);

  const add = await runBrain(dir, ["osma-anchor-add", "-"], {
    experience_id: exp.id, anchor: "mi-ancla-personal",
  });
  assert.equal(add.ok, true, `osma-anchor-add fallo: ${JSON.stringify(add)}`);
  const addData = add.data as any;
  assert.equal(addData.ok, true, `anchor-add responde ok: ${JSON.stringify(addData)}`);
  assert.ok(addData.cue_id != null, `anchor-add devuelve cue_id: ${JSON.stringify(addData)}`);

  const c2 = await cues(dir, exp.id);
  const manual = c2.cues.find((c: any) => c.value === "mi-ancla-personal");
  assert.ok(manual, `el anchor manual aparece en osma-cues: ${JSON.stringify(c2.cues)}`);
  assert.equal(manual.component_type, "anchor", `el anchor manual tiene component_type 'anchor'`);
  assert.equal(manual.source, "manual", `el anchor manual tiene source 'manual'`);
});

// ---------------------------------------------------------------------------
// h. No-aceptacion ciega en cue-search: una experiencia failed que comparte
//    TODAS las entidades con una verified NUNCA es winner ni aplicable.
// ---------------------------------------------------------------------------
test("OSMA no acepta a ciegas en cue-search: la experiencia failed nunca es winner ni 'apply'", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const ok = await record(dir, { ...RICH_EXP, topic_key: "educlave/auth-v6h" });
  const failed = await record(dir, {
    ...RICH_EXP, topic_key: "educlave/auth-v6h", reward: -1.0,
  });
  await analyze(dir);

  const res = await cueSearch(dir, { cues: ["supabase", "rls", "permission denied", "vivi"] });
  const rOk = res.results.find((r: any) => r.experience_id === ok.id);
  const rFail = res.results.find((r: any) => r.experience_id === failed.id);

  assert.ok(rOk, `la experiencia verified debe estar en los resultados: ${JSON.stringify(res.results)}`);
  assert.ok(rFail, `la experiencia failed comparte entidades y aparece: ${JSON.stringify(res.results)}`);
  assert.equal(rOk.applicability, "apply", `la verified es 'apply'`);
  assert.notEqual(rFail.applicability, "apply", `la failed NUNCA es 'apply': ${JSON.stringify(rFail)}`);

  assert.ok(res.winner !== null, `debe haber winner`);
  assert.equal(res.winner.experience_id, ok.id,
    `el winner es la verified, NO la failed: ${JSON.stringify(res.winner)}`);
});

// ---------------------------------------------------------------------------
// i. osma-migrate V6 idempotente: dos migraciones sin error, sin cues nuevos y
//    stats estables (el schema_version meta queda en '6' — el response conserva
//    '4' byte-identico por compat V4, y stats sigue funcionando).
// ---------------------------------------------------------------------------
test("OSMA migra V6 idempotente: osma-migrate x2 sin error y total_cues/experiences estables", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, {
    situation: "error login alumnos supabase rls", project: "educlave",
    topic_key: "educlave/auth-v6i", reward: 1.0,
  });
  await record(dir, {
    situation: "error login alumnos supabase token", project: "educlave",
    topic_key: "educlave/auth-v6i", reward: 1.0,
  });

  const before = await expStats(dir);
  assert.equal(before.experiences, 2, `hay 2 experiencias antes de migrar`);

  const m1 = await runBrain(dir, ["osma-migrate"]);
  assert.equal(m1.ok, true, `osma-migrate (1ra) fallo: ${m1.error}`);
  assert.ok((m1.data as any).experiences_analyzed >= 2,
    `la 1ra migracion analiza las experiencias: ${JSON.stringify(m1.data)}`);

  const after1 = await expStats(dir);
  // FIX 2 (Tywin): record ya hace la descomposicion COMPLETA, asi que la migracion
  // (analyze idempotente) ya no agrega cues — no debe PERDER ninguno (>=).
  assert.ok(after1.total_cues >= before.total_cues,
    `la migracion V6 mantiene los cues descompuestos (idempotente): ${before.total_cues} -> ${after1.total_cues}`);
  assert.ok(typeof after1.total_anchors === "number", `stats expone total_anchors: ${JSON.stringify(after1)}`);
  assert.ok(typeof after1.avg_salience === "number", `stats expone avg_salience: ${JSON.stringify(after1)}`);
  assert.ok(typeof after1.avg_retrieval_routes === "number", `stats expone avg_retrieval_routes: ${JSON.stringify(after1)}`);

  const m2 = await runBrain(dir, ["osma-migrate"]);
  assert.equal(m2.ok, true, `osma-migrate (2da, idempotente) fallo: ${m2.error}`);

  const after2 = await expStats(dir);
  assert.equal(after2.experiences, 2, `las experiences no cambian tras la 2da migracion`);
  assert.equal(after2.total_cues, after1.total_cues,
    `la 2da migracion NO crea cues nuevos (idempotente): ${after1.total_cues} -> ${after2.total_cues}`);
  assert.equal(after2.avg_retrieval_routes, after1.avg_retrieval_routes,
    `avg_retrieval_routes estable tras la 2da migracion`);
  assert.equal(after2.total_anchors, after1.total_anchors, `total_anchors estable tras la 2da migracion`);
});
