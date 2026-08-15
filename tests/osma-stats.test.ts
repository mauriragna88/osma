import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { runBrain } from "./runBrain.js";

// OSMA Brain Summary (V4-V7) — comando osma-stats.
// Prueba el resumen del cerebro OSMA completo: experiences + cues + episodes +
// links + distribucion por validation_status + promedios. El comando es
// ADITIVO: conserva las metricas V4 (osma_stats) y agrega el summary V5-V7
// (_osma_stats). Mismo patron que osma-multidimensional.test.ts: copia
// osma_brain.py REAL y ejecuta el engine de verdad
// via runBrain (stdin JSON, sin mocks).
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));

function makeFakeArnesProject(): string {
  const dir = mkdtempSync(join(tmpdir(), "osma-stats-"));
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
async function record(dir: string, data: Record<string, unknown>): Promise<{ id: number }> {
  const res = await runBrain(dir, ["osma-experience-record", "-"], data);
  assert.equal(res.ok, true, `osma-experience-record fallo: ${res.error}`);
  return res.data as { id: number };
}

/** osma-stats helper: summary V4-V7 del cerebro OSMA. */
async function osmaStats(dir: string): Promise<any> {
  const res = await runBrain(dir, ["osma-stats"]);
  assert.equal(res.ok, true, `osma-stats fallo: ${res.error}`);
  return res.data as any;
}

/** runPython: ejecuta un script python real contra el fixture (para construir
 *  DBs con tablas legacy incompletas que el init normal no crea). */
function runPython(
  cwd: string,
  args: string[]
): Promise<{ ok: boolean; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn("python", args, { cwd, windowsHide: true });
    let stderr = "";
    child.stderr.on("data", (d) => (stderr += d));
    child.stdin.end();
    child.on("close", (code) => resolve({ ok: code === 0, stderr }));
  });
}

// ---------------------------------------------------------------------------
// a. Summary con datos: 2 experiencias con status DISTINTOS (verified + failed)
//    y algunos cues (record descompone automaticamente). El resumen debe
//    reportar total_experiences >= 2, los status seedeados en la distribucion,
//    episodios EPISODE_XXXX y promedios numericos.
// ---------------------------------------------------------------------------
test("OSMA stats: summary V4-V7 con experiences de status distintos, cues y episodios", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const e1 = await record(dir, {
    situation: "error login alumnos supabase rls permission denied",
    action: "revisar policy rls user_id",
    outcome: "acceso restaurado correctamente",
    project: "educlave",
    topic_key: "educlave/auth-stats-a",
    reward: 1.0, // -> verified
  });
  const e2 = await record(dir, {
    situation: "error login alumnos supabase rls token invalido",
    action: "intento de fix",
    outcome: "el problema persiste",
    project: "educlave",
    topic_key: "educlave/auth-stats-a",
    reward: -1.0, // -> failed
  });
  assert.ok(e1.id > 0 && e2.id > 0, `ambas experiencias registradas: ${e1.id}, ${e2.id}`);

  const s = await osmaStats(dir);

  // totales
  assert.ok(s.total_experiences >= 2, `total_experiences >= 2: ${JSON.stringify(s)}`);
  assert.ok(s.total_cues >= 1, `total_cues >= 1 (record descompone cues): ${JSON.stringify(s)}`);
  assert.equal(typeof s.total_links, "number", `total_links es numero: ${JSON.stringify(s.total_links)}`);
  assert.equal(typeof s.total_episodes, "number", `total_episodes es numero`);
  assert.equal(s.total_episodes, s.total_experiences,
    `total_episodes == total_experiences (cada experiencia es un episodio)`);

  // episodios: count + ids EPISODE_XXXX
  assert.equal(s.episodes.count, s.total_experiences, `episodes.count == total_experiences`);
  assert.equal(s.episodes.ids.length, s.total_experiences, `episodes.ids lista un id por experiencia`);
  for (const id of s.episodes.ids) {
    assert.match(id, /^EPISODE_\d{4}$/, `episode id con formato EPISODE_XXXX (got ${id})`);
  }

  // distribucion por status: los status seedeados estan presentes
  assert.ok(
    s.status_distribution.verified >= 1,
    `status_distribution.verified >= 1 (seeded verified): ${JSON.stringify(s.status_distribution)}`
  );
  assert.ok(
    s.status_distribution.failed >= 1,
    `status_distribution.failed >= 1 (seeded failed): ${JSON.stringify(s.status_distribution)}`
  );

  // promedios numericos
  for (const k of ["avg_confidence", "avg_importance", "avg_retrieval_strength", "avg_cues_per_experience"]) {
    assert.equal(typeof s[k], "number", `${k} es numero: ${JSON.stringify(s[k])}`);
  }

  // compat V4 preservada (osma_associative depende de estas keys)
  assert.equal(typeof s.links, "number", `metricas V4 preservadas (links): ${JSON.stringify(s)}`);
  assert.equal(typeof s.avg_link_weight, "number", `metricas V4 preservadas (avg_link_weight)`);
});

// ---------------------------------------------------------------------------
// b. Empty DB: el summary devuelve ceros y arrays vacios SIN crashear.
// ---------------------------------------------------------------------------
test("OSMA stats: DB vacia devuelve ceros y arrays vacios sin crashear", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());

  const s = await osmaStats(dir);

  assert.equal(s.total_experiences, 0, `empty DB: total_experiences 0 (got ${s.total_experiences})`);
  assert.equal(s.total_cues, 0, `empty DB: total_cues 0 (got ${s.total_cues})`);
  assert.equal(s.total_links, 0, `empty DB: total_links 0 (got ${s.total_links})`);
  assert.equal(s.total_episodes, 0, `empty DB: total_episodes 0 (got ${s.total_episodes})`);
  assert.equal(s.episodes.count, 0, `empty DB: episodes.count 0 (got ${s.episodes.count})`);
  assert.deepEqual(s.episodes.ids, [], `empty DB: episodes.ids [] (got ${JSON.stringify(s.episodes.ids)})`);
  assert.equal(s.status_distribution.verified, 0,
    `empty DB: status_distribution.verified 0 (got ${s.status_distribution.verified})`);
  assert.equal(s.status_distribution.failed, 0,
    `empty DB: status_distribution.failed 0 (got ${s.status_distribution.failed})`);
  assert.equal(s.avg_confidence, 0, `empty DB: avg_confidence 0 (got ${s.avg_confidence})`);
  assert.equal(s.avg_importance, 0, `empty DB: avg_importance 0 (got ${s.avg_importance})`);
  assert.equal(s.avg_retrieval_strength, 0,
    `empty DB: avg_retrieval_strength 0 (got ${s.avg_retrieval_strength})`);
  assert.equal(s.avg_cues_per_experience, 0,
    `empty DB: avg_cues_per_experience 0 (got ${s.avg_cues_per_experience})`);
});

// ---------------------------------------------------------------------------
// c. Missing-table defense (FIX Tywin): una tabla legacy faltante/incompleta
//    NO debe crashear osma-stats. Se construye una DB con observation_links
//    presente pero SIN la columna 'weight' (simula una tabla legacy degenerada
//    que sobrevive al CREATE TABLE IF NOT EXISTS del __init__). El summary debe
//    devolver defaults vacios en TODOS los grupos (V4 y V5-V7).
// ---------------------------------------------------------------------------
test("OSMA stats: tabla legacy incompleta (observation_links sin weight) no crashea y devuelve ceros", { timeout: 30000 }, async () => {
  const dir = makeFakeArnesProject();
  const script = join(dir, "make_broken_db.py");
  writeFileSync(
    script,
    [
      "import sqlite3, sys",
      "conn = sqlite3.connect(sys.argv[1])",
      // observation_links existe pero SIN weight -> SELECT weight crashearia
      "conn.execute('CREATE TABLE observation_links (id INTEGER PRIMARY KEY)')",
      "conn.commit()",
      "conn.close()",
      "",
    ].join("\n")
  );
  const brokenDb = join(dir, ".arnes", "arnes.db");
  const made = await runPython(dir, [script, brokenDb]);
  assert.equal(made.ok, true, `crear DB rota fallo: ${made.stderr}`);

  // osma-stats debe responder ok (sin exception) y con defaults vacios
  const s = await osmaStats(dir);

  // grupo legacy V4 (los SELECTs que antes crasheaban sin guarda)
  assert.equal(s.links, 0, `observation_links incompleta -> links 0 (got ${s.links})`);
  assert.equal(s.avg_link_weight, 0, `observation_links incompleta -> avg_link_weight 0 (got ${s.avg_link_weight})`);
  assert.equal(s.active, 0, `active 0 (got ${s.active})`);
  assert.equal(s.archived, 0, `archived 0 (got ${s.archived})`);
  assert.equal(s.contradictions_open, 0, `contradictions_open 0 (got ${s.contradictions_open})`);
  assert.equal(s.consolidations_pending, 0, `consolidations_pending 0 (got ${s.consolidations_pending})`);

  // grupo V5-V7
  assert.equal(s.total_experiences, 0, `total_experiences 0 (got ${s.total_experiences})`);
  assert.equal(s.total_cues, 0, `total_cues 0 (got ${s.total_cues})`);
  assert.equal(s.total_links, 0, `total_links 0 (got ${s.total_links})`);
  assert.equal(s.episodes.count, 0, `episodes.count 0 (got ${s.episodes.count})`);
  assert.deepEqual(s.episodes.ids, [], `episodes.ids [] (got ${JSON.stringify(s.episodes.ids)})`);
  assert.equal(s.avg_confidence, 0, `avg_confidence 0 (got ${s.avg_confidence})`);
  assert.equal(s.avg_retrieval_strength, 0, `avg_retrieval_strength 0 (got ${s.avg_retrieval_strength})`);
});
