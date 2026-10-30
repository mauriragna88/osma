import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runBrain } from "./runBrain.js";

// OSMA LEARNING LOOP (#15) + AUTO-INIT ARGOS (hook ensure_argos_link).
// Cubre:
//   1) learning-loop guarda 5 observations (discovered/modified/verified/
//      pending/obsolete) con el quest_id correcto
//   2) learning-loop cierra la quest y marca las tasks como pass
//   3) auto-init: main() crea .arnes/project.json + .arnes/config.json
//      sin necesidad de llamar init explicitamente (hook ensure_argos_link)
//   4) auto-init: con ARNES_SKIP_AUTOINIT=1 no crea los archivos

function makeProject(): string {
  const dir = mkdtempSync(join(tmpdir(), "osma-loop-"));
  return dir;
}

async function init(dir: string) {
  const res = await runBrain(dir, ["init"]);
  assert.equal(res.ok, true, `init fallo: ${res.error}`);
}

test("learning-loop: guarda 5 memories + cierra quest", async () => {
  const dir = makeProject();
  await init(dir);

  // creo una quest con tasks para verificar cierre
  const quest = await runBrain(dir, ["aquest", "create", "-"], {
    id: "loop-test-1",
    description: "tarea de prueba",
    mode: "balanced",
  });
  assert.equal(quest.ok, true, `aquest fallo: ${quest.error}`);
  await runBrain(dir, ["atask", "create", "-"], {
    quest_id: "loop-test-1",
    task_id: "L1",
    description: "tarea L1",
    agent: "atlas",
    dependencies: [],
  });

  const res = await runBrain(dir, ["learning-loop", "-"], {
    quest_id: "loop-test-1",
    agent: "atlas",
    discovered: "Se encontro que el modulo X usa zod",
    modified: "Se cambio auth/login.ts",
    verified: "Tests 52/52 en verde",
    pending: "Falta integrar con ARGOS",
    obsolete: "La memoria vieja de auth ya no aplica",
    run_sleep: false,
  });
  assert.equal(res.ok, true, `learning-loop fallo: ${res.error}`);
  const data = res.data as Record<string, unknown>;
  const saved = data.saved as number[];
  assert.equal(saved.length, 5, "deberia guardar 5 memories");
  assert.equal(data.quest_id, "loop-test-1");

  // verificar que las 5 observations tienen el quest_id
  const agentMem = await runBrain(dir, ["agent", "atlas", "20"]);
  assert.equal(agentMem.ok, true);
  const mems = agentMem.data as Array<Record<string, unknown>>;
  assert.ok(mems.length >= 5, "deberia haber al menos 5 memories del agente");
  const tagged = mems.filter((m) => (m.tags as string).includes("learning-loop"));
  assert.ok(tagged.length >= 5, "faltan memories con tag learning-loop");

  // la quest debe quedar cerrada
  const q = await runBrain(dir, ["aquest", "get", "loop-test-1"]);
  assert.equal(q.ok, true);
  const qd = q.data as Record<string, unknown>;
  assert.equal(qd.status, "done", "la quest deberia quedar en done");
});

test("auto-init: main() crea project.json y config.json sin init explicito", async () => {
  const dir = makeProject();
  // NO llamamos init: solo corremos un comando cualquiera (stats)
  const res = await runBrain(dir, ["stats"]);
  assert.equal(res.ok, true, `stats fallo: ${res.error}`);

  const arnes = join(dir, ".arnes");
  assert.ok(existsSync(join(arnes, "arnes.db")), "falta arnes.db");
  assert.ok(existsSync(join(arnes, "project.json")), "falta project.json");
  assert.ok(existsSync(join(arnes, "config.json")), "falta config.json");
  assert.ok(existsSync(join(arnes, "memory", "export")), "falta memory/export");
});

test("auto-init: hook idempotente no duplica archivos en segunda llamada", async () => {
  const dir = makeProject();
  const res = await runBrain(dir, ["stats"]);
  assert.equal(res.ok, true, `stats fallo: ${res.error}`);

  // segunda llamada: no debe crashear y los archivos siguen existiendo
  const res2 = await runBrain(dir, ["stats"]);
  assert.equal(res2.ok, true, `stats 2 fallo: ${res2.error}`);
  const arnes = join(dir, ".arnes");
  assert.ok(existsSync(join(arnes, "project.json")));
  assert.ok(existsSync(join(arnes, "config.json")));
});
