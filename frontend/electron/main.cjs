/* Electron main process.
 *
 * What happens on launch:
 *   1. If a backend is already answering on port 8756 (e.g. started by hand
 *      during development), reuse it. Otherwise spawn
 *      `python backend/scripts/run_backend.py` and wait for /api/health.
 *   2. Open the window straight from renderer/index.html — this project has
 *      no build step, what is on disk is what runs.
 *   3. Provide three privileged services over IPC: open a document's file,
 *      reveal it in the file manager, open links in the browser.
 *   4. On quit, stop the backend again — but only if we started it.
 *
 * Backend process management itself lives in backend.cjs, which has no
 * Electron imports so it can be tested with plain Node (tests/backend.test.mjs).
 */

const { app, BrowserWindow, Menu, dialog, ipcMain, nativeTheme, shell } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const { DEFAULT_PORT, spawnBackend, waitForHealth, stopBackend } = require("./backend.cjs");

const PROJECT_ROOT = path.join(__dirname, "..", "..");
const BACKEND_URL = process.env.LKM_BACKEND_URL || `http://127.0.0.1:${DEFAULT_PORT}`;
const OPEN_DEVTOOLS = process.argv.includes("--dev");

let backendChild = null;   // set only when WE spawned the backend
let backendLog = [];       // last output lines, shown in the failure dialog
let libraryRoot = null;    // absolute path reported by /api/health

/* ---------------------------------------------------------------- backend */

async function fetchHealth(timeoutMs = 1500) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const response = await fetch(`${BACKEND_URL}/api/health`, { signal: controller.signal });
    clearTimeout(timer);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

async function ensureBackend() {
  let health = await fetchHealth();
  if (health) {
    libraryRoot = health.library_root || null;
    return { ok: true, external: true };
  }

  // Packaged app (Stage 8): spawn the frozen backend that ships in
  // resources/backend — no Python needed. The knowledge base lives in a
  // visible, backup-friendly place: Documents\Legal Knowledge Manager.
  const packagedOptions = {};
  if (app.isPackaged) {
    packagedOptions.command = path.join(
      process.resourcesPath, "backend",
      process.platform === "win32" ? "lkm-backend.exe" : "lkm-backend",
    );
    packagedOptions.env = {
      LKM_HOME: path.join(app.getPath("documents"), "Legal Knowledge Manager"),
    };
    if (!fs.existsSync(packagedOptions.command)) {
      return {
        ok: false,
        friendly:
          "A part of the app is missing (the backend engine was not found " +
          "inside the installation). Re-running the installer should fix it.",
      };
    }
  }

  try {
    backendChild = spawnBackend(PROJECT_ROOT, {
      ...packagedOptions,
      onLog: (line) => {
        process.stdout.write(`[backend] ${line}`);
        backendLog = backendLog.concat(line.split("\n")).slice(-30);
      },
    });
  } catch (err) {
    return { ok: false, friendly: err.friendly || String(err) };
  }

  try {
    await waitForHealth(BACKEND_URL, { timeoutMs: 60000 });
  } catch {
    return { ok: false };
  }
  health = await fetchHealth();
  if (health) libraryRoot = health.library_root || null;
  return { ok: true, external: false };
}

function shutdownBackend() {
  if (backendChild) {
    stopBackend(backendChild);
    backendChild = null;
  }
}

/* ----------------------------------------------------------------- window */

function createWindow() {
  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 980,
    minHeight: 620,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#141b22" : "#eef1ea",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  win.once("ready-to-show", () => win.show());

  // Any window.open / target=_blank goes to the user's real browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://") || url.startsWith("https://")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });

  win.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
  if (OPEN_DEVTOOLS) win.webContents.openDevTools({ mode: "detach" });
}

/* -------------------------------------------------------------------- ipc */

/** Ask the backend where document {id}'s file lives, inside the library only. */
async function resolveDocumentPath(documentId) {
  if (!Number.isInteger(documentId) || documentId < 1) {
    return { error: "That document id is not valid." };
  }
  const response = await fetch(`${BACKEND_URL}/api/documents/${documentId}`).catch(() => null);
  if (!response || !response.ok) {
    return { error: "The document was not found." };
  }
  const doc = await response.json();
  if (!libraryRoot || !doc.rel_path) {
    return { error: "The file's location is not known." };
  }
  const absolute = path.resolve(libraryRoot, doc.rel_path);
  const relative = path.relative(path.resolve(libraryRoot), absolute);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    return { error: "That path is outside the library." };
  }
  if (!fs.existsSync(absolute)) {
    return { error: "The file is not on disk (was it moved or deleted?)." };
  }
  return { absolute };
}

ipcMain.handle("lkm:open-document", async (_event, documentId) => {
  const found = await resolveDocumentPath(documentId);
  if (found.error) return { ok: false, reason: found.error };
  const problem = await shell.openPath(found.absolute);
  return problem ? { ok: false, reason: problem } : { ok: true };
});

ipcMain.handle("lkm:reveal-document", async (_event, documentId) => {
  const found = await resolveDocumentPath(documentId);
  if (found.error) return { ok: false, reason: found.error };
  shell.showItemInFolder(found.absolute);
  return { ok: true };
});

ipcMain.handle("lkm:open-external", async (_event, url) => {
  if (typeof url === "string" && (url.startsWith("http://") || url.startsWith("https://"))) {
    await shell.openExternal(url);
    return { ok: true };
  }
  return { ok: false };
});

/* -------------------------------------------------------------- lifecycle */

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const [win] = BrowserWindow.getAllWindows();
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);

    const backend = await ensureBackend();
    if (!backend.ok) {
      const tail = backendLog.filter(Boolean).slice(-8).join("\n");
      const checklist = app.isPackaged
        ? "The app's backend engine did not start.\n\n" +
          "Restarting the computer or re-running the installer usually " +
          "fixes this. If it keeps happening, the details below say why:"
        : "The app's Python backend did not start.\n\n" +
          "Checklist:\n" +
          "  1. Python 3.12+ is installed (python.org — tick \u201cAdd python.exe to PATH\u201d).\n" +
          "  2. Backend packages are installed:\n" +
          "         pip install -r backend/requirements.txt\n" +
          "  3. Try it by hand to see the real error:\n" +
          "         python backend/scripts/run_backend.py\n";
      dialog.showErrorBox(
        "Could not start the backend",
        (backend.friendly ? backend.friendly + "\n\n" : "") + checklist +
          (tail ? `\nLast backend output:\n${tail}` : "")
      );
      app.quit();
      return;
    }

    createWindow();
  });

  // Single-window desktop utility: closing the window quits, on every OS.
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", shutdownBackend);
  process.on("exit", shutdownBackend);
}
