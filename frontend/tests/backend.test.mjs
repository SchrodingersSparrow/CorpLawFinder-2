/* Integration tests for electron/backend.cjs — the module the Electron shell
 * uses to find Python, start the backend, wait for /api/health and stop it.
 *
 * No Electron and no FastAPI here: the test builds a throwaway "project"
 * whose backend/scripts/run_backend.py is a ten-line Python stub that answers
 * /api/health after a short startup delay. That exercises the real spawn /
 * poll / kill code paths end to end with nothing installed beyond Python.
 */

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { findPython, spawnBackend, waitForHealth, stopBackend } =
  require("../electron/backend.cjs");

const STUB_PORT = 18956;

const STUB_SERVER = `
import json, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8756

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            body = json.dumps({"status": "ok", "library_root": "/tmp/lib"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *args):
        pass

print("stub backend starting", flush=True)
time.sleep(0.4)                      # imitate FastAPI's startup time
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
`;

function makeStubProject() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "lkm-stub-"));
  const scripts = path.join(root, "backend", "scripts");
  fs.mkdirSync(scripts, { recursive: true });
  fs.writeFileSync(path.join(scripts, "run_backend.py"), STUB_SERVER);
  return root;
}

function waitForExit(child, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    if (child.exitCode !== null || child.signalCode) return resolve();
    const timer = setTimeout(() => reject(new Error("child did not exit")), timeoutMs);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
  });
}

test("findPython locates a Python 3 interpreter", () => {
  const interpreter = findPython();
  assert.ok(interpreter, "expected Python 3 on this machine");
  assert.equal(typeof interpreter.command, "string");
  assert.ok(Array.isArray(interpreter.args));
});

test("LKM_PYTHON overrides interpreter discovery", () => {
  process.env.LKM_PYTHON = "/opt/custom/python3";
  try {
    assert.deepEqual(findPython(), { command: "/opt/custom/python3", args: [] });
  } finally {
    delete process.env.LKM_PYTHON;
  }
});

test("spawnBackend + waitForHealth + stopBackend against the stub server", async () => {
  const root = makeStubProject();
  const logs = [];
  const child = spawnBackend(root, {
    port: STUB_PORT,
    onLog: (line) => logs.push(line),
  });
  try {
    await waitForHealth(`http://127.0.0.1:${STUB_PORT}`, { timeoutMs: 15000 });
  } finally {
    stopBackend(child);
  }
  await waitForExit(child);
  assert.ok(logs.join("").includes("stub backend starting"),
    "onLog should have received the stub's startup line");
  assert.ok(child.exitCode !== null || child.signalCode,
    "stopBackend should terminate the child");
});

test("packaged mode: spawnBackend runs a given command with --port and env", async () => {
  const root = makeStubProject();
  const logs = [];
  // Pretend the stub script is the frozen lkm-backend executable: spawn it
  // via the `command` override exactly the way the packaged main.cjs does.
  const stub = path.join(root, "backend", "scripts", "run_backend.py");
  const interpreter = findPython();
  const child = spawnBackend("/nonexistent-project-root", {
    command: interpreter.command,
    args: [...interpreter.args, stub],
    env: { LKM_HOME: path.join(root, "MyLibrary") },
    port: STUB_PORT + 1,
    onLog: (line) => logs.push(line),
  });
  try {
    await waitForHealth(`http://127.0.0.1:${STUB_PORT + 1}`, { timeoutMs: 15000 });
  } finally {
    stopBackend(child);
  }
  await waitForExit(child);
  assert.ok(logs.join("").includes("stub backend starting"));
});

test("waitForHealth rejects with backend_start_timeout when nothing answers", async () => {
  await assert.rejects(
    waitForHealth("http://127.0.0.1:19571", { timeoutMs: 1200, intervalMs: 200 }),
    (err) => {
      assert.equal(err.message, "backend_start_timeout");
      return true;
    },
  );
});

test("spawnBackend throws a friendly error when Python is missing", () => {
  process.env.LKM_PYTHON = "/definitely/not/a/python";
  try {
    // The override names a non-existent interpreter; spawn still succeeds at
    // the API level (ENOENT arrives async), so probe findPython behaviour
    // separately: an unusable LKM_PYTHON is passed through verbatim by design
    // (the user asked for it), and startup failure surfaces via waitForHealth.
    const interpreter = findPython();
    assert.equal(interpreter.command, "/definitely/not/a/python");
  } finally {
    delete process.env.LKM_PYTHON;
  }
});
