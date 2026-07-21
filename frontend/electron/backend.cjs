/* Backend process management for the Electron shell.
 *
 * Kept free of any Electron imports so the whole file can be unit- and
 * integration-tested with plain Node (see frontend/tests/backend.test.mjs,
 * which runs it against a stub Python server).
 */

"use strict";

const { spawn, spawnSync } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");

const DEFAULT_PORT = 8756;

/** Find a working Python 3 interpreter, Windows launcher included. */
function findPython() {
  if (process.env.LKM_PYTHON) {
    return { command: process.env.LKM_PYTHON, args: [] };
  }
  const candidates = process.platform === "win32"
    ? [{ command: "py", args: ["-3"] }, { command: "python", args: [] }, { command: "python3", args: [] }]
    : [{ command: "python3", args: [] }, { command: "python", args: [] }];

  for (const candidate of candidates) {
    try {
      const probe = spawnSync(candidate.command, [...candidate.args, "--version"], {
        timeout: 5000,
        windowsHide: true,
      });
      if (probe.status === 0) {
        const banner = String(probe.stdout || "") + String(probe.stderr || "");
        if (banner.includes("Python 3")) return candidate;
      }
    } catch {
      /* try the next one */
    }
  }
  return null;
}

/** Start the FastAPI backend as a child process. Returns the ChildProcess.
 *
 * Two modes:
 *  - from source (default): find Python and run backend/scripts/run_backend.py;
 *  - packaged (opts.command): spawn the frozen lkm-backend executable that
 *    ships inside the installed app — no Python involved. `opts.env` adds
 *    environment variables (the shell passes LKM_HOME this way).
 */
function spawnBackend(projectRoot, { python, port = DEFAULT_PORT, onLog, command, args = [], env = {} } = {}) {
  let executable;
  let executableArgs;
  let cwd;

  if (command) {
    executable = command;
    executableArgs = [...args, "--port", String(port)];
    cwd = path.dirname(command);
  } else {
    const interpreter = python || findPython();
    if (!interpreter) {
      const error = new Error("python_not_found");
      error.friendly =
        "Python 3 was not found on this computer. Install it from python.org " +
        "(tick “Add python.exe to PATH” during setup), then start the app again.";
      throw error;
    }
    const script = path.join(projectRoot, "backend", "scripts", "run_backend.py");
    executable = interpreter.command;
    executableArgs = [...interpreter.args, script, "--port", String(port)];
    cwd = projectRoot;
  }

  const child = spawn(executable, executableArgs, {
    cwd,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PYTHONUNBUFFERED: "1", ...env },
  });

  if (onLog) {
    child.stdout.on("data", (chunk) => onLog(String(chunk)));
    child.stderr.on("data", (chunk) => onLog(String(chunk)));
  }
  return child;
}

/** Poll GET {baseUrl}/api/health until it answers 200 or the timeout passes. */
function waitForHealth(baseUrl, { timeoutMs = 60000, intervalMs = 400 } = {}) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(baseUrl + "/api/health", (response) => {
        response.resume();
        if (response.statusCode === 200) {
          resolve(true);
        } else {
          retry();
        }
      });
      request.on("error", retry);
      request.setTimeout(2000, () => request.destroy(new Error("timeout")));
    };
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error("backend_start_timeout"));
      } else {
        setTimeout(attempt, intervalMs);
      }
    };
    attempt();
  });
}

/** Stop the backend child process, taking the whole tree down on Windows. */
function stopBackend(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    try {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
        windowsHide: true, timeout: 10000,
      });
    } catch {
      child.kill();
    }
  } else {
    child.kill("SIGTERM");
  }
}

module.exports = { DEFAULT_PORT, findPython, spawnBackend, waitForHealth, stopBackend };
