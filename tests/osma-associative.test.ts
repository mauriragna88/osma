import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { runBrain } from "./runBrain.js";

// OSMA (associative memory engine) — prueba que la memoria REALMENTE aprende.
// Mismo patron de brain-bridge.test.ts: usa osma_brain.py (raiz) y ejecuta el engine de verdad
// temporal y ejecuta el engine de verdad via runBrain (stdin JSON, sin mocks).
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));

function makeFakeArnesProject(): string {
  const dir = mkdtempSync(join(tmpdir(), "osma-learn-"));
  mkdirSync(join(dir, ".arnes"), { recursive: true });

  return dir;
}

/** init + assert ok. Devuelve el dir listo. */
async function initProject(dir: string): Promise<string> {
  const res = await runBrain(dir, ["init"]);
  assert.equal(res.ok, true, `init fallo: ${res.error}`);
  return dir;
}

/** save helper: devuelve el id de la observacion creada. */
async function save(
  dir: string,
  agent: string,
  topic: string,
  type: string,
  content: string,
  extra: Record<string, unknown> = {}
): Promise<number> {
  const res = await runBrain(dir, ["save", "-"], {
    agent,
    topic_key: topic,
    type,
    content,
    ...extra,
  });
  assert.equal(res.ok, true, `save fallo: ${res.error}`);
  return (res.data as { id: number }).id;
}

/** get helper: fila completa de la observacion. */
async function getRow(dir: string, id: number): Promise<any> {
  const res = await runBrain(dir, ["get", String(id)]);
  assert.equal(res.ok, true, `get ${id} fallo: ${res.error}`);
  return res.data as any;
}

/** osma-stats helper. */
async function osmaStats(dir: string): Promise<any> {
  const res = await runBrain(dir, ["osma-stats"]);
  assert.equal(res.ok, true, `osma-stats fallo: ${res.error}`);
  return res.data as any;
}

// ---------------------------------------------------------------------------
// a. Co-activation learning: osma-link fortalece links y crece avg_link_weight
// ---------------------------------------------------------------------------
test("OSMA aprende co-activacion: osma-link x3 fortalece el link y crece avg_link_weight", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const a = await save(dir, "kuja", "educlave/auth", "pattern", "supabase rls login alumnos");
  const b = await save(dir, "kuja", "educlave/auth", "pattern", "supabase rls login sesiones");

  const before = await osmaStats(dir);
  assert.ok(before.links >= 1, `el link-on-write debe crear un link: ${JSON.stringify(before)}`);

  for (let i = 0; i < 3; i++) {
    const link = await runBrain(dir, ["osma-link", "-"], {
      new_id: a,
      recalled_ids: [b],
      signal: "coactivation",
    });
    assert.equal(link.ok, true, `osma-link fallo: ${link.error}`);
    assert.equal((link.data as any).linked, 1, `cada osma-link enlaza 1 par`);
  }

  const after = await osmaStats(dir);
  assert.ok(after.links >= 1, `links >= 1 tras co-activar: ${JSON.stringify(after)}`);
  assert.ok(
    after.avg_link_weight > before.avg_link_weight,
    `avg_link_weight debe crecer estrictamente: ${before.avg_link_weight} -> ${after.avg_link_weight}`
  );
});

// ---------------------------------------------------------------------------
// b. Propagation by levels: seed 1.0, segundo salto con activacion < 1.0
// ---------------------------------------------------------------------------
test("OSMA propaga activacion por niveles: seed 1.0 y memoria de 2do salto con activacion < 1.0", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const seed = await save(dir, "kuja", "educlave/auth", "pattern", "EduClave login alumnos");
  const hop = await save(dir, "kuja", "educlave/db", "discovery", "supabase rls tabla");
  const level2 = await save(dir, "kuja", "educlave/api", "discovery", "token jwt refresh");

  // cadena determinista: seed<->hop y hop<->level2 (5x success => peso ~0.75 por arista)
  for (let i = 0; i < 5; i++) {
    await runBrain(dir, ["osma-link", "-"], { new_id: seed, recalled_ids: [hop], signal: "success" });
    await runBrain(dir, ["osma-link", "-"], { new_id: hop, recalled_ids: [level2], signal: "success" });
  }

  const res = await runBrain(dir, ["osma-recall", "EduClave login", "-", "5", "-"]);
  assert.equal(res.ok, true, `osma-recall fallo: ${res.error}`);
  const rows = res.data as any[];
  assert.ok(rows.length >= 1, `el recall trae al menos el seed`);

  const byId = new Map(rows.map((r) => [Number(r.id), r]));
  for (const r of rows) {
    assert.equal(typeof r.activation, "number", `toda fila trae campo activation, fila ${r.id}`);
  }

  assert.ok(byId.has(seed), `el seed debe aparecer en el recall`);
  assert.equal(byId.get(seed).activation, 1.0, `el seed activa con 1.0`);

  assert.ok(byId.has(level2), `la memoria de 2do salto debe aparecer por propagacion`);
  const act2 = byId.get(level2).activation;
  assert.ok(act2 > 0 && act2 < 1.0, `el 2do salto decae con activacion < 1.0 (got ${act2})`);
});

// ---------------------------------------------------------------------------
// c. Utility reinforcement: success sube score y successful_retrievals
// ---------------------------------------------------------------------------
test("OSMA refuerza utilidad: osma-reinforce success sube score y successful_retrievals", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const id = await save(dir, "kuja", "educlave/rls", "pattern", "RLS policy user id", { score: 4 });

  const row0 = await getRow(dir, id);
  assert.equal(row0.score, 4);
  assert.equal(row0.successful_retrievals, 0);
  assert.equal(row0.state, "active");

  const res = await runBrain(dir, ["osma-reinforce", "-"], { id, success: true });
  assert.equal(res.ok, true, `osma-reinforce fallo: ${res.error}`);
  assert.equal((res.data as any).ok, true);
  assert.equal((res.data as any).state, "active");

  const row = await getRow(dir, id);
  assert.ok(row.score >= 4, `score no debe bajar (got ${row.score})`);
  assert.ok(row.successful_retrievals >= 1, `successful_retrievals debe incrementar (got ${row.successful_retrievals})`);
  assert.equal(row.state, "active", `la memoria util sigue activa`);
});

// ---------------------------------------------------------------------------
// d. Correction weakens: fail baja confianza y marca contested
// ---------------------------------------------------------------------------
test("OSMA corrige: osma-reinforce fail baja confianza y marca la memoria contested", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const id = await save(dir, "kuja", "educlave/db", "decision", "EduClave usa firebase", { confidence: 0.7 });

  const res = await runBrain(dir, ["osma-reinforce", "-"], { id, success: false });
  assert.equal(res.ok, true, `osma-reinforce fallo: ${res.error}`);
  assert.equal((res.data as any).state, "contested");

  const row = await getRow(dir, id);
  assert.ok(row.confidence < 0.7, `la confianza debe bajar (got ${row.confidence})`);
  assert.equal(row.state, "contested");
});

// ---------------------------------------------------------------------------
// e. Decay + state transition via injected clock (+400 dias)
// ---------------------------------------------------------------------------
test("OSMA decae y transiciona con reloj inyectado: +400 dias => dormant y retrieval_strength < 0.6", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const id = await save(dir, "kuja", "educlave/sesion", "pattern", "sesion expira token refresh", {
    volatility: "dynamic",
    score: 3,
  });

  const pre = await getRow(dir, id);
  assert.equal(pre.state, "active");

  // recall fija last_retrieved_at (el decay del sueno depende de esa referencia)
  const rec = await runBrain(dir, ["osma-recall", "sesion expira", "-", "5", "-"]);
  assert.equal(rec.ok, true);
  assert.ok((rec.data as any[]).some((r) => Number(r.id) === id), `la obs debe ser recuperable`);

  // reloj inyectado: 400 dias despues del momento real
  const now = new Date(Date.now() + 400 * 24 * 60 * 60 * 1000);
  const iso = now.toISOString().replace("T", " ").slice(0, 19);
  const statsBefore = await osmaStats(dir);

  const sleep = await runBrain(dir, ["osma-sleep", "24", iso]);
  assert.equal(sleep.ok, true, `osma-sleep fallo: ${sleep.error}`);
  assert.ok((sleep.data as any).decayed >= 1, `decayed >= 1 (got ${(sleep.data as any).decayed})`);

  const row = await getRow(dir, id);
  assert.ok(row.retrieval_strength < 0.6, `retrieval_strength debe decaer (got ${row.retrieval_strength})`);
  assert.ok(["dormant", "archived"].includes(row.state), `state transiciona (got ${row.state})`);

  const statsAfter = await osmaStats(dir);
  assert.ok(
    statsAfter.active < statsBefore.active || statsAfter.archived > statsBefore.archived,
    `active bajó o archived subió: ${JSON.stringify(statsBefore)} -> ${JSON.stringify(statsAfter)}`
  );

  // FIX 2 (persistent_decay_formula): 2do sleep con el MISMO reloj inyectado -> decay estable,
  // retrieval_strength no compone (decay_base queda fijo como pico).
  const firstRetrieval = row.retrieval_strength;
  const sleep2 = await runBrain(dir, ["osma-sleep", "24", iso]);
  assert.equal(sleep2.ok, true, `2do osma-sleep fallo: ${sleep2.error}`);
  const row2 = await getRow(dir, id);
  assert.equal(
    row2.retrieval_strength,
    firstRetrieval,
    `retrieval_strength estable en 2do sleep (got ${row2.retrieval_strength}, esperado ${firstRetrieval})`
  );
});

// ---------------------------------------------------------------------------
// f. Consolidation: dedup + pending + finalize
// ---------------------------------------------------------------------------
test("OSMA consolida duplicados: osma-sleep dedup, consolidation pending con ambos ids, finalize done", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const f1 = await save(dir, "kuja", "educlave/auth", "bugfix", "error rls login alumnos supabase");
  const f2 = await save(dir, "kuja", "educlave/auth", "bugfix", "error rls login alumnos tabla");

  const sleep = await runBrain(dir, ["osma-sleep", "24", "-"]);
  assert.equal(sleep.ok, true, `osma-sleep fallo: ${sleep.error}`);
  assert.ok((sleep.data as any).deduped >= 1, `deduped >= 1 (got ${(sleep.data as any).deduped})`);

  const cons = await runBrain(dir, ["osma-consolidations"]);
  assert.equal(cons.ok, true, `osma-consolidations fallo: ${cons.error}`);
  const pending = (cons.data as any[]).filter((c) => c.status === "pending");
  assert.ok(pending.length >= 1, `hay al menos una consolidacion pendiente`);

  const target = pending.find((c) => {
    try {
      const ids = JSON.parse(c.source_ids);
      return Array.isArray(ids) && ids.includes(f1) && ids.includes(f2);
    } catch {
      return false;
    }
  });
  assert.ok(target, `la consolidacion agrupa AMBAS observaciones (source_ids)`);
  assert.equal(target.status, "pending");

  const fin = await runBrain(dir, ["osma-consolidation-finalize", "-"], {
    id: target.id,
    summary: "Arquitectura auth EduClave: Supabase + RLS",
  });
  assert.equal(fin.ok, true, `finalize fallo: ${fin.error}`);
  assert.equal((fin.data as any).ok, true);
  assert.equal((fin.data as any).status, "done");
});

// ---------------------------------------------------------------------------
// g. Contradiction resolution: firebase vs supabase -> superseded
// ---------------------------------------------------------------------------
test("OSMA detecta y resuelve contradicciones: firebase vs supabase, perdedor superseded", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  const x = await save(dir, "kuja", "educlave/db", "decision", "EduClave usa firebase", { confidence: 0.8 });
  const y = await save(dir, "kuja", "educlave/db", "decision", "EduClave migro a supabase", { confidence: 0.8 });

  const sleep = await runBrain(dir, ["osma-sleep", "24", "-"]);
  assert.equal(sleep.ok, true, `osma-sleep fallo: ${sleep.error}`);
  assert.ok(
    (sleep.data as any).contradictions_detected >= 1,
    `contradictions_detected >= 1 (got ${(sleep.data as any).contradictions_detected})`
  );

  const cons = await runBrain(dir, ["osma-contradictions"]);
  assert.equal(cons.ok, true, `osma-contradictions fallo: ${cons.error}`);
  const open = (cons.data as any[]).filter((c) => c.status === "open");
  assert.ok(open.length >= 1, `hay una contradiccion abierta`);
  const cid = open[0].id;

  // resolver: el ganador es la migracion (supabase)
  const resolve = await runBrain(dir, ["osma-contradiction-resolve", "-"], {
    id: cid,
    winner_id: y,
    evidence: "migracion a supabase verificada en smoke test",
  });
  assert.equal(resolve.ok, true, `resolve fallo: ${resolve.error}`);
  assert.equal((resolve.data as any).ok, true);

  const loser = await getRow(dir, x);
  assert.ok(["contested", "superseded"].includes(loser.state), `el perdedor queda marcado (got ${loser.state})`);
  assert.equal(loser.state, "superseded");
});

// ---------------------------------------------------------------------------
// h. No-contamination guard: osma-context respeta el presupuesto de tokens
// ---------------------------------------------------------------------------
test("OSMA respeta el presupuesto de tokens: osma-context empaqueta <= max_tokens", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  // suficiente contenido para que el paquete bruto exceda el presupuesto y el trim actue
  for (let i = 0; i < 10; i++) {
    await save(dir, "kuja", "educlave/auth", "pattern", `regla login alumnos supabase sesion ${i}`, { score: 4 });
  }

  const maxTokens = 200;
  const res = await runBrain(dir, ["osma-context", "login", "-", "-", String(maxTokens)]);
  assert.equal(res.ok, true, `osma-context fallo: ${res.error}`);
  const pkg = res.data as any;
  assert.ok(pkg && typeof pkg === "object", `osma-context devuelve un paquete`);
  assert.ok(Array.isArray(pkg.direct), `el paquete tiene direct[]`);
  assert.ok(Array.isArray(pkg.associations), `el paquete tiene associations[]`);

  const tokens = JSON.stringify(pkg).length / 4;
  assert.ok(tokens <= maxTokens, `presupuesto respetado: ${tokens.toFixed(2)} <= ${maxTokens}`);
});

// ---------------------------------------------------------------------------
// i. Migration safety: osma-migrate es idempotente
// ---------------------------------------------------------------------------
test("OSMA migra de forma idempotente: osma-migrate x2 sin error y schema_version 4", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  await save(dir, "kuja", "educlave/auth", "pattern", "supabase rls login alumnos");
  await save(dir, "kuja", "educlave/auth", "pattern", "supabase rls login sesiones");

  const m1 = await runBrain(dir, ["osma-migrate"]);
  assert.equal(m1.ok, true, `osma-migrate (1ra) fallo: ${m1.error}`);
  assert.equal((m1.data as any).schema_version, "4");

  const m2 = await runBrain(dir, ["osma-migrate"]);
  assert.equal(m2.ok, true, `osma-migrate (2da, idempotente) fallo: ${m2.error}`);
  assert.equal((m2.data as any).schema_version, "4", `el schema_version se mantiene en 4`);
});

// ---------------------------------------------------------------------------
// j. Stale-link decay via injected clock: weight *= 0.995 tras un periodo (24h)
// ---------------------------------------------------------------------------
test("OSMA debilita links inactivos con reloj inyectado: weight *= 0.995 tras un periodo", { timeout: 30000 }, async () => {
  const dir = await initProject(makeFakeArnesProject());
  // dos obs que comparten >=2 entidades -> auto-link crea el par con peso 0.1
  const a = await save(dir, "kuja", "educlave/auth", "pattern", "supabase rls login alumnos");
  const b = await save(dir, "kuja", "educlave/api", "pattern", "supabase rls refresh token");

  const before = await osmaStats(dir);
  assert.equal(before.links, 1, `auto-link debe crear exactamente 1 link: ${JSON.stringify(before)}`);
  assert.equal(before.avg_link_weight, 0.1, `link nuevo pesa 0.1 (got ${before.avg_link_weight})`);

  // reloj inyectado: 25 horas despues del momento real -> 1 periodo (hours=24) -> * 0.995
  const now = new Date(Date.now() + 25 * 60 * 60 * 1000);
  const iso = now.toISOString().replace("T", " ").slice(0, 19);

  const sleep = await runBrain(dir, ["osma-sleep", "24", iso]);
  assert.equal(sleep.ok, true, `osma-sleep fallo: ${sleep.error}`);
  assert.ok(
    (sleep.data as any).links_weakened >= 1,
    `links_weakened >= 1 (got ${(sleep.data as any).links_weakened})`
  );

  const after = await osmaStats(dir);
  assert.ok(
    after.avg_link_weight < before.avg_link_weight,
    `avg_link_weight debe bajar estrictamente: ${before.avg_link_weight} -> ${after.avg_link_weight}`
  );
  assert.ok(
    Math.abs(after.avg_link_weight - 0.0995) < 0.0001,
    `esperado ~0.0995 tras un periodo (got ${after.avg_link_weight})`
  );
});
