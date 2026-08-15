// Minimal runBrain bridge for OSMA's own tests (self-contained).
// OSMA is a standalone repo: its tests spawn osma_brain.py directly (no
// dependency on the ARGOS harness bridge). Mirrors pi/extensions/argos-brain.ts.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));

export function resolveBrainPath() {
  const local = join(REPO_ROOT, "osma_brain.py");
  if (existsSync(local)) return local;
  throw new Error("OSMA: no se encontro osma_brain.py en la raiz del repo");
}

// runBrain(cwd, args, stdinJson): cwd es el *proyecto* (con .arnes/arnes.db);
// el brain se resuelve desde la raiz de OSMA (ubicacion de este helper).
export async function runBrain(cwd, args, stdinJson, timeoutMs = 8000) {
  const brain = resolveBrainPath();
  const db = join(cwd, ".arnes", "arnes.db");
  const child = spawn("python", [brain, db, ...args], { cwd, windowsHide: true });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (d) => (stdout += d));
  child.stderr.on("data", (d) => (stderr += d));
  if (stdinJson !== undefined) child.stdin.write(JSON.stringify(stdinJson));
  child.stdin.end();
  const timedOut = Symbol("runBrain-timeout");
  const code = await new Promise((resolveCode) => {
    const timer = setTimeout(() => { child.kill(); resolveCode(timedOut); }, timeoutMs);
    child.on("close", (c) => { clearTimeout(timer); resolveCode(c ?? 0); });
    child.on("error", () => { clearTimeout(timer); resolveCode(-2); });
  });
  child.stdout.destroy(); child.stderr.destroy(); child.stdin.destroy();
  if (code === timedOut) return { ok: false, data: null, error: "timeout" };
  if (code !== 0) return { ok: false, data: null, error: stderr.trim() || `exit ${code}` };
  const trimmed = stdout.trim();
  if (!trimmed) return { ok: true, data: null };
  try { return { ok: true, data: JSON.parse(trimmed) }; }
  catch { return { ok: true, data: trimmed }; }
}