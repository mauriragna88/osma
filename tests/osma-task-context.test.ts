import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { runBrain } from "./runBrain.js";

// OSMA TASK_CONTEXT — paquete compatible con la seccion #10 del protocolo.
// Cubre:
//   1) shape del paquete (version, project, FACTS, RELEVANT_FILES,
//      RELATED_DATABASE, KNOWN_DECISIONS, KNOWN_RISKS, STALE_INFORMATION,
//      CONFIDENCE, MISSING_INFORMATION, SEARCH_REQUIRED, stats)
//   2) emision explicita de MEMORY MISS cuando la query no tiene cobertura
//   3) seccion SEARCH_REQUIRED con NO_FULL_REPOSITORY_SCAN=true
//   4) render_task_context() devuelve texto con [TASK_CONTEXT] y secciones
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));

function makeProject(): string {
  const dir = mkdtempSync(join(tmpdir(), "osma-task-context-"));
  mkdirSync(join(dir, ".arnes"), { recursive: true });
  return dir;
}

async function init(dir: string) {
  const res = await runBrain(dir, ["init"]);
  assert.equal(res.ok, true, `init fallo: ${res.error}`);
}

async function saveObs(dir: string, args: Record<string, unknown>) {
  const res = await runBrain(dir, ["save", "-"], args);
  assert.equal(res.ok, true, `save fallo: ${res.error}`);
  return res.data as { ok: boolean; id: number };
}

test("task-context: shape completo del paquete", async () => {
  const dir = makeProject();
  await init(dir);

  await saveObs(dir, {
    agent: "tywin",
    topic_key: "src/auth/login.ts",
    type: "decision",
    content: "Auth flow: supabase rls + zod, no raw sql",
    score: 4,
    confidence: 0.85,
    source: "tywin",
    tags: ["database", "security", "decision"],
  });
  await saveObs(dir, {
    agent: "vivi",
    topic_key: "migrations/014_add_rls.sql",
    type: "pattern",
    content: "RLS policy pattern: enable + force + check via auth.uid()",
    score: 4,
    confidence: 0.8,
    source: "vivi",
    tags: ["database", "migration", "rls"],
  });

  const res = await runBrain(dir, [
    "task-context", "supabase rls login seguridad", "osma", "-", "1500", "test-quest",
  ]);
  assert.equal(res.ok, true, `task-context fallo: ${res.error}`);
  const pkg = res.data as Record<string, unknown>;

  // shape minimo exigido por la seccion #10
  assert.equal(pkg.version, "1.0");
  assert.equal(pkg.project, "osma");
  assert.equal(pkg.query, "supabase rls login seguridad");
  assert.equal(pkg.quest_id, "test-quest");
  for (const k of [
    "FACTS", "RELEVANT_FILES", "RELATED_DATABASE", "KNOWN_DECISIONS",
    "KNOWN_RISKS", "STALE_INFORMATION", "CONFIDENCE",
    "MISSING_INFORMATION", "SEARCH_REQUIRED", "stats",
  ]) {
    assert.ok(k in pkg, `falta seccion ${k}`);
  }
  assert.equal(typeof pkg.CONFIDENCE, "number");
  const stats = pkg.stats as Record<string, unknown>;
  assert.equal(typeof stats.direct_hits, "number");
  assert.equal(typeof stats.estimated_tokens, "number");

  // FACTS / RELEVANT_FILES poblados
  const facts = pkg.FACTS as Array<Record<string, unknown>>;
  assert.ok(facts.length >= 1, "FACTS vacio");
  assert.ok(typeof facts[0].memory_id === "number");
  assert.ok(typeof facts[0].confidence === "number");

  const files = pkg.RELEVANT_FILES as Array<Record<string, unknown>>;
  assert.ok(files.length >= 1, "RELEVANT_FILES vacio");
  assert.ok(typeof files[0].path === "string");

  // CONOCIMIENTOS: KNOWN_DECISIONS debe incluir la memory de decision
  const decisions = pkg.KNOWN_DECISIONS as Array<Record<string, unknown>>;
  assert.ok(decisions.length >= 1, "KNOWN_DECISIONS vacio");
});

test("task-context: MEMORY MISS explicito cuando no hay cobertura", async () => {
  const dir = makeProject();
  await init(dir);

  const res = await runBrain(dir, [
    "task-context", "zzz-nonsense-token-no-existe", "osma", "-", "1500", "q",
  ]);
  assert.equal(res.ok, true);
  const pkg = res.data as Record<string, unknown>;
  const missing = pkg.MISSING_INFORMATION as Array<Record<string, unknown>>;
  assert.ok(missing.length >= 1, "deberia reportar MISSING");
  const hasMiss = missing.some((m) => m.type === "MEMORY_MISS");
  assert.ok(hasMiss, "no se emitio MEMORY_MISS");
  const search = pkg.SEARCH_REQUIRED as Array<Record<string, unknown>>;
  if (search.length > 0) {
    for (const s of search) {
      assert.equal(s.NO_FULL_REPOSITORY_SCAN, true, "search_required sin NO_FULL_REPOSITORY_SCAN");
    }
  }
});

test("task-context: render produce texto con [TASK_CONTEXT] y secciones", async () => {
  const dir = makeProject();
  await init(dir);
  await saveObs(dir, {
    agent: "tywin",
    topic_key: "src/foo.ts",
    type: "decision",
    content: "Use zod for runtime validation",
    score: 4,
    confidence: 0.8,
  });

  const res = await runBrain(dir, [
    "task-context", "zod validation", "osma", "-", "1500", "q", "render",
  ]);
  assert.equal(res.ok, true, `task-context render fallo: ${res.error}`);
  const text = String(res.data);
  assert.ok(text.includes("[TASK_CONTEXT]"), "falta header [TASK_CONTEXT]");
  assert.ok(text.includes("FACTS:"), "falta seccion FACTS en render");
  assert.ok(/Confidence:\s+0\.\d{2,3}/.test(text), "falta Confidence numerica en render");
});
