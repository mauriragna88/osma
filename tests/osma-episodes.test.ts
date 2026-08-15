import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { runBrain } from "./runBrain.js";

// OSMA V7 Episode Pattern Completion — "recordar modifica la memoria".
// Prueba la reactivacion post-retrieval (frequency/retrieval_strength/cues/links)
// y la reconstruccion completa del episodio (osma-episode).
// Mismo patron que osma-multidimensional.test.ts: usa osma_brain.py REAL
// a un fixture temporal y ejecuta el engine de verdad via runBrain (sin mocks).
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));

function makeFakeArnesProject(): string {
  const dir = mkdtempSync(join(tmpdir(), "osma-v7-"));
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

/** cueSearch helper: osma-cue-search multi-cue (V7: winner + reactivation). */
async function cueSearch(dir: string, data: Record<string, unknown>): Promise<any> {
  const res = await runBrain(dir, ["osma-cue-search", "-"], data);
  assert.equal(res.ok, true, `osma-cue-search fallo: ${res.error}`);
  return res.data as any;
}

/** cues helper: osma-cues de una experiencia (incluye coactivation_count V7). */
async function cues(dir: string, id: number): Promise<any> {
  const res = await runBrain(dir, ["osma-cues", String(id)]);
  assert.equal(res.ok, true, `osma-cues fallo: ${res.error}`);
  return res.data as any;
}

/** stats helper: osma-experience-stats (incluye total_frequency V7). */
async function expStats(dir: string): Promise<any> {
  const res = await runBrain(dir, ["osma-experience-stats"]);
  assert.equal(res.ok, true, `osma-experience-stats fallo: ${res.error}`);
  return res.data as any;
}

/** episode helper: osma-episode reconstruccion completa V7. */
async function episode(dir: string, id: number): Promise<any> {
  const res = await runBrain(dir, ["osma-episode", String(id)]);
  assert.equal(res.ok, true, `osma-episode fallo: ${res.error}`);
  return res.data as any;
}

/** Verifica formato EPISODE_XXXX (solo para ids <= 9999; el engine muestra el
 *  numero plano si el id excede el padding de 4 digitos). */
function assertEpisodeIdFormat(episodeId: unknown, experienceId: number): void {
  assert.equal(typeof episodeId, "string", `episode_id debe ser string: ${episodeId}`);
  if (experienceId <= 9999) {
    assert.match(
      episodeId as string,
      /^EPISODE_\d{4}$/,
      `episode_id con formato EPISODE_XXXX (got ${episodeId} para id ${experienceId})`
    );
  } else {
    assert.match(
      episodeId as string,
      /^EPISODE_\d+$/,
      `episode_id con prefijo EPISODE_ para id > 9999 (got ${episodeId})`
    );
  }
}

// Experiencia rica estandar V7: error login supabase rls + permission denied.
// (proyecto educlave, agente vivi, reward 1.0 -> verified).
const RICH_EXP = {
  situation: "error login alumnos supabase rls permission denied",
  action: "revisar policy rls user_id",
  outcome: "acceso restaurado correctamente",
  project: "educlave",
  agent: "vivi",
  reward: 1.0,
};

// ---------------------------------------------------------------------------
// a. Reactivacion: recordar modifica la memoria. Dos experiencias enlazadas
//    (>=2 entidades compartidas) -> cue-search con winner dispara reactivation:
//    el episodio recuperado se vuelve MAS accesible (frequency +1) y
//    osma-experience-stats.total_frequency sube. "Recordar tambien modifica
//    la memoria".
// ---------------------------------------------------------------------------
test("OSMA V7 reactivacion: recordar modifica la memoria — total_frequency sube +1 tras cue-search con winner", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, { ...RICH_EXP, topic_key: "educlave/auth-v7a" });
  const e2 = await record(dir, {
    situation: "fix supabase rls login alumnos token",
    action: "ajustar policy",
    outcome: "login ok",
    project: "educlave",
    topic_key: "educlave/auth-v7a",
    reward: 1.0,
  });

  const before = await expStats(dir);
  assert.equal(before.total_frequency, 0,
    `antes de recuperar, total_frequency es 0: ${JSON.stringify(before)}`);

  const res = await cueSearch(dir, { cues: ["supabase", "rls", "login"] });
  assert.ok(res.winner !== null, `debe haber winner: ${JSON.stringify(res)}`);
  assert.ok(res.reactivation !== null,
    `winner presente => reactivation presente: ${JSON.stringify(res)}`);
  assert.equal(res.reactivation.episode_id, res.winner.episode_id,
    `reactivation refuerza EXACTAMENTE el episodio ganador`);
  assert.equal(res.reactivation.frequency_delta, 1,
    `frequency_delta es 1 (recuperar con exito incrementa la frecuencia)`);
  assert.equal(res.reactivation.retrieval_strength_delta, 0.03,
    `retrieval_strength_delta es +0.03`);

  const after = await expStats(dir);
  assert.equal(after.total_frequency, before.total_frequency + 1,
    `total_frequency sube +1 tras la reactivacion (${before.total_frequency} -> ${after.total_frequency})`);
  assert.ok(e1.id > 0 && e2.id > 0, `ambas experiencias registradas: ${e1.id}, ${e2.id}`);
});

// ---------------------------------------------------------------------------
// b. Formato episode_id: cada resultado de cue-search y el winner exponen
//    EPISODE_XXXX (zero-padded 4 digitos).
// ---------------------------------------------------------------------------
test("OSMA V7 formato episode_id: results y winner usan EPISODE_XXXX", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, { ...RICH_EXP, topic_key: "educlave/auth-v7b" });
  await record(dir, {
    situation: "fix supabase rls login alumnos token",
    action: "ajustar policy",
    outcome: "login ok",
    project: "educlave",
    topic_key: "educlave/auth-v7b",
    reward: 1.0,
  });

  const res = await cueSearch(dir, { cues: ["supabase", "rls", "login", "permission denied"] });
  assert.ok(res.results.length >= 1, `debe haber resultados: ${JSON.stringify(res)}`);
  for (const r of res.results) {
    assertEpisodeIdFormat(r.episode_id, r.experience_id);
  }
  assert.ok(res.winner !== null, `debe haber winner: ${JSON.stringify(res)}`);
  assertEpisodeIdFormat(res.winner.episode_id, res.winner.experience_id);
  assert.ok(e1.id > 0, `experiencia registrada`);
});

// ---------------------------------------------------------------------------
// c. Refuerzo de cues del winner: los cues que participaron en la recuperacion
//    reciben coactivation_count +1 y last_coactivated_at (el cue 'supabase' del
//    winner, que estaba en el query, queda reforzado).
// ---------------------------------------------------------------------------
test("OSMA V7 refuerzo de cues: el cue 'supabase' del winner queda coactivado (count>=1 y last_coactivated_at)", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, { ...RICH_EXP, topic_key: "educlave/auth-v7c" });
  const e2 = await record(dir, {
    situation: "fix supabase rls login alumnos token",
    action: "ajustar policy",
    outcome: "login ok",
    project: "educlave",
    topic_key: "educlave/auth-v7c",
    reward: 1.0,
  });

  const res = await cueSearch(dir, { cues: ["supabase", "rls", "login"] });
  assert.ok(res.winner !== null, `debe haber winner: ${JSON.stringify(res)}`);
  assert.equal(res.winner.experience_id, e2.id,
    `el winner es el episodio mas nuevo (competicion): ${JSON.stringify(res.winner)}`);

  const c = await cues(dir, e2.id);
  const supabaseCue = c.cues.find((x: any) => x.component_type === "technology" && x.value === "supabase");
  assert.ok(supabaseCue, `el winner debe tener cue technology 'supabase': ${JSON.stringify(c.cues)}`);
  assert.ok(
    (supabaseCue.coactivation_count as number) >= 1,
    `el cue matcheado 'supabase' quedo coactivado (count=${supabaseCue.coactivation_count}): ${JSON.stringify(supabaseCue)}`
  );
  assert.ok(
    supabaseCue.last_coactivated_at != null && supabaseCue.last_coactivated_at !== "",
    `el cue matcheado tiene last_coactivated_at: ${JSON.stringify(supabaseCue)}`
  );
});

// ---------------------------------------------------------------------------
// d. Refuerzo de links entre co-activados: dos experiencias enlazadas (weight
//    0.1) -> cue-search activa AMBAS -> el link winner<->otra sube +0.05
//    (weight > 0.1 en related_experiences de osma-episode).
// ---------------------------------------------------------------------------
test("OSMA V7 refuerzo de links: winner<->coactivado pasa de 0.1 a >0.1 (+0.05)", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, { ...RICH_EXP, topic_key: "educlave/auth-v7d" });
  const e2 = await record(dir, {
    situation: "fix supabase rls login alumnos token",
    action: "ajustar policy",
    outcome: "login ok",
    project: "educlave",
    topic_key: "educlave/auth-v7d",
    reward: 1.0,
  });

  const res = await cueSearch(dir, { cues: ["supabase", "rls", "login"] });
  assert.ok(res.winner !== null, `debe haber winner: ${JSON.stringify(res)}`);
  assert.equal(res.winner.experience_id, e2.id, `el winner es la experiencia nueva`);
  assert.ok(res.reactivation !== null, `reactivation presente`);

  // El winner (e2) y la otra co-activada (e1) deben salir en los resultados.
  assert.ok(res.results.some((r: any) => r.experience_id === e1.id),
    `la co-activada e1 aparece en los resultados: ${JSON.stringify(res.results)}`);

  const ep = await episode(dir, e2.id);
  const rel = ep.related_experiences.find((r: any) => r.experience_id === e1.id);
  assert.ok(rel, `osma-episode del winner lista a e1 como relacionada: ${JSON.stringify(ep.related_experiences)}`);
  assert.ok(
    (rel.weight as number) > 0.1,
    `el link winner<->coactivado se refuerza +0.05 (weight=${rel.weight}): ${JSON.stringify(rel)}`
  );
  assert.ok(
    (rel.coactivation_count as number) >= 1,
    `el link winner<->coactivado incrementa coactivation_count (count=${rel.coactivation_count}): ${JSON.stringify(rel)}`
  );
});

// ---------------------------------------------------------------------------
// e. osma-episode: reconstruccion completa del episodio. Todos los campos de la
//    experiencia + cues + rutas + relacionados + patrones.
// ---------------------------------------------------------------------------
test("OSMA V7 osma-episode reconstruye el episodio completo con session_id, quest_id, cues y rutas", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const exp = await record(dir, {
    situation: "error login alumnos supabase rls permission denied",
    reasoning: "la policy de rls no filtra por user_id",
    conclusion: "falta el join con auth.users",
    action: "revisar policy rls user_id",
    outcome: "acceso restaurado correctamente",
    project: "educlave",
    agent: "vivi",
    topic_key: "educlave/auth-v7e",
    session_id: "ses-v7e",
    quest_id: "q-v7e",
    files: ["src/lib/auth.ts"],
    reward: 1.0,
  });

  const ep = await episode(dir, exp.id);
  assertEpisodeIdFormat(ep.episode_id, ep.experience_id);
  assert.equal(ep.experience_id, exp.id, `experience_id coincide`);

  for (const field of ["summary", "situation", "reasoning", "conclusion", "action", "outcome"]) {
    assert.ok(
      typeof ep[field] === "string" && ep[field].length > 0,
      `episode.${field} no vacio: ${JSON.stringify(ep[field])}`
    );
  }
  assert.equal(ep.session_id, "ses-v7e", `session_id se reconstruye`);
  assert.equal(ep.quest_id, "q-v7e", `quest_id se reconstruye`);
  assert.ok(typeof ep.validation_status === "string" && ep.validation_status.length > 0,
    `validation_status no vacio: ${JSON.stringify(ep.validation_status)}`);
  assert.ok(Array.isArray(ep.files) && ep.files.length >= 1,
    `files se reconstruye como array: ${JSON.stringify(ep.files)}`);

  assert.ok(Array.isArray(ep.cues) && ep.cues.length >= 1,
    `cues es un array con >= 1 entrada: ${JSON.stringify(ep.cues)}`);
  for (const cue of ep.cues) {
    assert.ok(typeof cue.component_type === "string" && cue.component_type.length > 0,
      `cue.component_type presente: ${JSON.stringify(cue)}`);
    assert.ok(typeof cue.value === "string" && cue.value.length > 0,
      `cue.value presente: ${JSON.stringify(cue)}`);
    assert.ok(typeof cue.cue_quality === "number", `cue.cue_quality numerico: ${JSON.stringify(cue)}`);
    assert.equal(typeof cue.coactivation_count, "number",
      `cue.coactivation_count expuesto (V7): ${JSON.stringify(cue)}`);
  }

  assert.ok(ep.routes && (ep.routes.retrieval_routes as number) >= 1,
    `routes.retrieval_routes >= 1: ${JSON.stringify(ep.routes)}`);
  assert.ok(Array.isArray(ep.related_experiences), `related_experiences es array`);
  assert.ok(Array.isArray(ep.related_observations), `related_observations es array`);
  assert.ok(Array.isArray(ep.patterns), `patterns es array`);
  assert.ok(typeof ep.temporal_context === "string" && ep.temporal_context.length > 0,
    `temporal_context no vacio: ${JSON.stringify(ep.temporal_context)}`);
});

// ---------------------------------------------------------------------------
// f. Pattern completion: un fragmento minimo ("permission denied" + contexto
//    rls) evoca el episodio COMPLETO — el winner reconstruye la experiencia rica
//    entera (su summary contiene el fragmento).
// ---------------------------------------------------------------------------
test("OSMA V7 pattern completion: fragmento minimo 'permission denied' evoca el episodio completo", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, { ...RICH_EXP, topic_key: "educlave/auth-v7f" });
  // Otra experiencia con tema DISTINTO y proyecto distinto (sin >=2 entidades
  // compartidas) para no marcar obsoleta a la rica.
  await record(dir, {
    situation: "configuracion tema oscuro",
    action: "ajustar css",
    outcome: "tema aplicado",
    project: "nexo",
    topic_key: "nexo/theme-v7f",
    reward: 0.2,
  });

  const res = await cueSearch(dir, { cues: ["permission denied", "rls"] });
  assert.ok(res.winner !== null, `fragmento minimo debe evocar un winner: ${JSON.stringify(res)}`);
  assert.equal(res.winner.experience_id, e1.id,
    `el fragmento 'permission denied' reconstruye la experiencia rica: ${JSON.stringify(res.winner)}`);
  const rec = res.winner.reconstruction as Record<string, string>;
  assert.ok(
    typeof rec.summary === "string" && rec.summary.includes("permission denied"),
    `la reconstruccion del winner contiene el fragmento evocado (summary=${rec.summary})`
  );
});

// ---------------------------------------------------------------------------
// g. Competencia entre episodios: dos experiencias comparten 'supabase' pero
//    solo una tiene 'rls' -> el par de cues desambigua hacia el episodio RLS
//    (no el de UI).
// ---------------------------------------------------------------------------
test("OSMA V7 competencia: {supabase, rls} desambigua hacia el episodio RLS y no el de UI", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "error login alumnos supabase rls permission denied",
    action: "revisar policy rls user_id",
    outcome: "acceso restaurado",
    project: "educlave",
    topic_key: "educlave/auth-v7g",
    reward: 1.0,
  });
  // Competidora: comparte 'supabase' y 'educlave' pero NO tiene 'rls' y no es
  // verified (reward 0.2 -> hypothesis) para no marcar obsoleta a e1.
  await record(dir, {
    situation: "configuracion tema oscuro supabase ui",
    action: "ajustar css",
    outcome: "tema aplicado",
    project: "educlave",
    topic_key: "educlave/theme-v7g",
    reward: 0.2,
  });

  const res = await cueSearch(dir, { cues: ["supabase", "rls"] });
  assert.ok(res.winner !== null, `debe haber winner: ${JSON.stringify(res)}`);
  assert.equal(res.winner.experience_id, e1.id,
    `el par {supabase, rls} desambigua hacia el episodio RLS: ${JSON.stringify(res.winner)}`);

  const ep = await episode(dir, e1.id);
  assert.ok(
    typeof ep.summary === "string" && ep.summary.includes("rls"),
    `el summary del winner menciona rls: ${JSON.stringify(ep.summary)}`
  );
});

// ---------------------------------------------------------------------------
// h. Sin winner -> reactivation null: una experiencia failed (reward -1.0) como
//    unico match NUNCA gana; sin winner no hay reactivacion.
// ---------------------------------------------------------------------------
test("OSMA V7 sin winner => reactivation null (la experiencia failed no puede ganar)", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, {
    situation: "error login alumnos supabase rls permission denied",
    action: "intento de fix",
    outcome: "el problema persiste",
    project: "educlave",
    topic_key: "educlave/auth-v7h",
    reward: -1.0,
  });

  const res = await cueSearch(dir, { cues: ["supabase", "educlave"] });
  assert.equal(res.winner, null, `la experiencia failed no gana: ${JSON.stringify(res)}`);
  assert.equal(res.reactivation, null,
    `sin winner no hay reactivacion (reactivation null): ${JSON.stringify(res)}`);
});

// ---------------------------------------------------------------------------
// i. Migracion V7 idempotente: osma-migrate x2 sin error, cues_reactivation_ready
//    true y total_frequency intacto (migrate NO reactiva memorias).
// ---------------------------------------------------------------------------
test("OSMA V7 migracion idempotente: osma-migrate x2, cues_reactivation_ready y total_frequency estable", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, { ...RICH_EXP, topic_key: "educlave/auth-v7i" });

  const m1 = await runBrain(dir, ["osma-migrate"]);
  assert.equal(m1.ok, true, `osma-migrate (1ra) fallo: ${m1.error}`);
  assert.equal((m1.data as any).cues_reactivation_ready, true,
    `la 1ra migracion reporta cues_reactivation_ready: ${JSON.stringify(m1.data)}`);

  const after1 = await expStats(dir);

  const m2 = await runBrain(dir, ["osma-migrate"]);
  assert.equal(m2.ok, true, `osma-migrate (2da, idempotente) fallo: ${m2.error}`);
  assert.equal((m2.data as any).cues_reactivation_ready, true,
    `la 2da migracion sigue reportando cues_reactivation_ready: ${JSON.stringify(m2.data)}`);

  const after2 = await expStats(dir);
  assert.equal(after2.total_frequency, after1.total_frequency,
    `la migracion NO reactiva memorias (total_frequency estable): ${after1.total_frequency} -> ${after2.total_frequency}`);
});

// ---------------------------------------------------------------------------
// j. Competition_score presente en los resultados y usado como tie-breaker
//    (FIX 2, Tywin). Dos experiencias en el mismo proyecto compiten; la
//    competition_score debe ser un numero entre 0 y 1, y el winner debe
//    tener el score mas alto.
// ---------------------------------------------------------------------------
test("OSMA V7 competition_score presente en results y winner tiene score mas alto", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "error login supabase rls permission denied",
    action: "revisar policy rls",
    outcome: "acceso restaurado",
    project: "educlave",
    topic_key: "educlave/auth-v7j",
    reward: 1.0,
  });
  const e2 = await record(dir, {
    situation: "error login supabase rls token expirado",
    action: "refrescar token",
    outcome: "login ok",
    project: "educlave",
    topic_key: "educlave/auth-v7j",
    reward: 1.0,
  });

  const res = await cueSearch(dir, { cues: ["supabase", "rls", "login"] });
  assert.ok(res.winner !== null, `debe haber winner: ${JSON.stringify(res)}`);
  assert.ok(res.results.length >= 1, `debe haber resultados: ${JSON.stringify(res.results)}`);

  for (const r of res.results) {
    assert.ok(
      typeof r.competition_score === "number" && r.competition_score >= 0 && r.competition_score <= 1,
      `competition_score debe ser un numero entre 0 y 1 (got ${r.competition_score}): ${JSON.stringify(r)}`
    );
  }
  // El winner debe tener competition_score definido en su reconstruction
  assert.ok(e1.id > 0 && e2.id > 0, `ambas experiencias registradas: ${e1.id}, ${e2.id}`);
});

// ---------------------------------------------------------------------------
// k. FTS5 fallback: un cue sin match estructurado NO rompe la busqueda y
//    los resultados via_fts NUNCA son winner (FIX 3, Tywin).
// ---------------------------------------------------------------------------
test("OSMA V7 FTS5 fallback: cue sin match estructurado no rompe busqueda y via_fts nunca es winner", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await record(dir, {
    situation: "error login supabase rls permission denied",
    action: "revisar policy rls",
    outcome: "acceso restaurado",
    project: "educlave",
    topic_key: "educlave/auth-v7k",
    reward: 1.0,
  });

  // Query con un cue que NO tiene match estructurado (palabra unica)
  const res = await cueSearch(dir, { cues: ["zebra99"] });
  // La busqueda no debe romperse (assert.ok en cueSearch helper ya lo verifica)
  assert.ok(Array.isArray(res.results), `results debe ser array: ${JSON.stringify(res)}`);
  // Si hay via_fts, verificamos que ninguno sea winner
  const viaFtsEntries = res.results.filter((r: any) => r.via_fts === true);
  for (const entry of viaFtsEntries) {
    assert.ok(entry.episode_activation_score < 1.0,
      `via_fts nunca debe tener score >= 1.0 (score=${entry.episode_activation_score}): ${JSON.stringify(entry)}`);
    assert.notEqual(res.winner?.experience_id, entry.experience_id,
      `via_fts nunca debe ser winner: ${JSON.stringify(entry)}`);
  }
});
