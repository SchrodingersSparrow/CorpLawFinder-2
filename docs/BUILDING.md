# Building the Windows installer

This turns the project into a single `Legal-Knowledge-Manager-Setup-1.0.0.exe`
that anyone can double-click on Windows 10/11 — **the machines that run the
installer need no Python and no Node.js**; everything is bundled.

Only the machine that *builds* the installer needs the developer tools.

## The short version (one script)

On a Windows machine with [Python 3.12+](https://python.org) (ticked "Add
python.exe to PATH") and [Node.js LTS](https://nodejs.org) installed, open
PowerShell in the project folder and run:

```powershell
.\build-installer.ps1
```

The script installs the build tools, runs both test suites (it refuses to
package a failing build), freezes the backend, builds the app, and prints
the installer's path — `frontend\dist-installer\Legal-Knowledge-Manager-Setup-1.0.0.exe`.

## What the steps do (the manual version)

1. **Freeze the backend** — PyInstaller bundles Python, the backend code and
   the database schema into a self-contained folder:

   ```powershell
   pip install -r backend/requirements.txt
   pip install pyinstaller
   python backend/scripts/freeze_backend.py
   ```

   Output: `backend-dist\lkm-backend\` with `lkm-backend.exe` inside. You can
   sanity-test it alone: `backend-dist\lkm-backend\lkm-backend.exe --port 8901`
   then open http://127.0.0.1:8901/api/health (Ctrl+C to stop).

2. **Build the desktop app and installer** — electron-builder packages the
   interface, drops the frozen backend into the app's resources, and wraps
   everything in a one-click NSIS installer:

   ```powershell
   cd frontend
   npm install
   npm run dist
   ```

## What the installed app does differently

- It starts `lkm-backend.exe` from its own resources — Python is never
  involved on the user's machine.
- The knowledge base lives in **`Documents\Legal Knowledge Manager`**
  (database, downloaded library, logs) — visible, and backed up by copying
  that one folder. Uninstalling the app never deletes it.
- The desktop shortcut and Start-menu entry use the generated icon
  (`frontend/build/icon.ico` — regenerate any time with
  `python frontend/build/make_icon.py`).

## What is *not* inside the installer (by design)

The optional external programs stay external, exactly like the from-source
setup: **Tesseract** and **Poppler** for OCR of scanned PDFs, **Ollama** for
the local AI, and Playwright's Chromium for JavaScript-built pages (MCA).
The app detects what's present and its error messages carry the install
steps. PaddleOCR is excluded from the frozen backend on purpose (its stack
is ~1 GB); the packaged app uses Tesseract, which is what the fallback
logic does anyway.

## Troubleshooting the build

- *PowerShell refuses to run the script* — run
  `Set-ExecutionPolicy -Scope Process Bypass` first, or execute the manual
  steps above.
- *Antivirus flags the freeze step* — PyInstaller output is a common false
  positive; add the project folder to the exclusions and re-run.
- *`electron-builder` cannot find the backend* — the `extraResources` path
  expects `backend-dist\lkm-backend` to exist; run the freeze step first.
- *Building for another machine* — build on the oldest Windows you intend
  to support; PyInstaller output is not guaranteed backwards-compatible.
