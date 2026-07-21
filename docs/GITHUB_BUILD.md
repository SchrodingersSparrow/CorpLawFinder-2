# Building the installer on GitHub (no tools on your computer)

GitHub's free build service can do everything `build-installer.ps1` does —
on their Windows machines instead of yours. Your part shrinks to: upload
the project once, wait, download one file. No Python, no Node, no terminal.

**Use a fresh copy of the project zip for uploading** — not the folder you
have been running the app from. A fresh copy is guaranteed to contain only
code: none of your downloaded documents, none of your database. (A
`.gitignore` file in the project guards against this too, but the web
uploader does not read it — the fresh copy is the real protection.)

## One-time setup (about 15 minutes)

1. **Create a GitHub account** at https://github.com → Sign up. The free
   plan is enough — it includes private repositories and 2,000 build
   minutes a month (one installer build uses roughly 15–20).

2. **Create the repository.** Click the **+** in the top-right → **New
   repository**. Name it `legal-knowledge-manager`, choose **Private**
   (important — the repository is your code's home, keep it yours), leave
   every checkbox unticked, and click **Create repository**.

3. **Upload the project.** On the new repository's page, click the
   *"uploading an existing file"* link. Extract a **fresh copy** of the
   project zip on your computer, open the extracted
   `legal-knowledge-manager` folder, select **everything inside it**
   (Ctrl+A), and drag it all onto the GitHub upload page. Wait for the
   file list to finish loading (a minute or two — it is ~150 files), type
   anything in the commit box (e.g. "initial upload"), and click **Commit
   changes**.

   *If the uploader complains about too many files*, upload in rounds:
   drag the `backend` folder first, commit; then `frontend`, commit; then
   `docs`, `.github` and the loose files, commit. Order does not matter —
   the build starts after each commit and only the last one needs to
   succeed.

   *Can't see the `.github` folder when selecting?* In Windows Explorer:
   View → Show → Hidden items. That folder contains the build recipe — the
   automation does not run without it.

## Getting your installer

4. Open the repository's **Actions** tab. A run called **Build Windows
   installer** starts by itself after every upload (you can also press
   **Run workflow** to start one manually). A yellow dot means building; a
   green tick means done — typically 10–20 minutes. The run executes all
   196 tests first and refuses to build from a failing state.

5. Click the finished run, scroll to **Artifacts**, and download
   **Legal-Knowledge-Manager-installer**. Unzip it —
   `Legal-Knowledge-Manager-Setup-1.0.0.exe` is inside.

6. Double-click the installer on any Windows 10/11 machine. The app
   installs with its own backend engine (no Python there, ever), puts a
   shortcut on the desktop, and keeps its data in
   `Documents\Legal Knowledge Manager`.

The optional helpers remain per-machine choices, exactly as in the README:
Tesseract + Poppler for OCR of scans, Ollama for local AI, and
`playwright install chromium` for MCA's JavaScript pages.

## When the code changes later

When you receive updated project files: open the repository, navigate to
the file's folder, use **Add file → Upload files**, drag the replacements
in, and commit. A fresh build starts automatically; collect the new
installer from the Actions tab as before. (Uploading a fresh full copy
over the old one works too — same result.)

## If a build fails

Open the run in the Actions tab and click the step marked with a red ✗ —
the last lines of its log say what went wrong. The step named *"API
self-check (advisory)"* is allowed to fail without stopping the build;
every other red ✗ stops it by design. Copy those last lines and share
them — that is everything needed to diagnose it.
