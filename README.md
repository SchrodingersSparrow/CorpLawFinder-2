# Legal Knowledge Manager

A private, offline-first desktop app for transaction lawyers. Save research
URLs from RBI, SEBI, MCA and other authorities; the app downloads the
documents, names and files them sensibly, OCRs scanned PDFs, summarizes and
classifies them with a local AI model, and gives you fast full-text search
over your whole library. Everything runs on your own computer using only free
and open-source software — no cloud services, no subscriptions, no data
leaving your machine except to fetch the pages you ask for.

**Current status: version 1.0 — all 8 stages complete.** The app is
feature-complete: sources → analysis → polite downloading with duplicate
detection → smart naming and filing → text extraction and OCR → local-AI
summaries, metadata and topics → full-text search with saved searches — and
now a **one-click Windows installer** so it can be put on any machine
without installing Python or Node. See `docs/ARCHITECTURE.md` for the
design, `docs/API.md` for the endpoints, and `docs/BUILDING.md` for
building the installer.

## Stage 8 — the installer

Two ways to run the finished app:

- **From source** (as you have been): `npm start` in `frontend/` — nothing
  changed, everything still works the same way.
- **As an installed Windows app**: build the installer once — either on a
  machine that has Python and Node (`.\build-installer.ps1`, see
  `docs/BUILDING.md`) or **on GitHub's servers with nothing installed
  locally at all** (`docs/GITHUB_BUILD.md`) — then double-click
  `Legal-Knowledge-Manager-Setup-1.0.0.exe` on any Windows 10/11 machine.
  The installed app bundles its own backend engine (no Python needed), gets
  a desktop shortcut with the app's docket-file icon, and keeps your entire
  knowledge base in **Documents\Legal Knowledge Manager** — one visible
  folder that you back up by copying it. Uninstalling never touches it.

The optional helpers stay optional and external, exactly as before:
Tesseract + Poppler for OCR of scans, Ollama for the local AI, Playwright's
browser for MCA. The app detects what's present and tells you the exact
install step when something is missing.

## Try Stage 7 — smarter search (nothing to install)

No new installs — just run the app and open Search. What's new:

- **Type less** — `mast` already finds *Master Direction* while you type.
- **Phrases** — `"master direction"` matches those words in that order.
- **Either/or** — `fema OR odi` finds documents about either.
- **Exclude** — `kyc -draft` hides drafts; works with phrases too:
  `-"draft circular"`.
- **Sort** — relevance (default), newest first, or oldest first.
- **Saved searches** — set up a search you run every week (say
  `"master direction" kyc`, RBI only, newest first), press **Save this
  search**, name it — and from then on it's one click, filters and all.
  Saving the same name again updates it.

## Try Stage 6 — the local AI assistant (setup ~15 minutes, one time)

1. Install **Ollama** from https://ollama.com (free; this is the program
   that runs AI models privately on your own machine — no document text
   ever leaves your computer).
2. Pull a model, once, in a terminal:

   ```
   ollama pull qwen2.5:7b-instruct
   ```

   That model wants ~8 GB of RAM. On a lighter machine pull
   `qwen2.5:3b-instruct` instead — the app automatically uses whichever is
   installed.

Then start the app and download something (or press **Summarise** on any
existing document that has text). Watch the Dashboard; when the job
finishes, the document's preview shows the summary, the blank metadata
fields are filled in (your own edits are never overwritten), topic tags
appear, and searching for words from the summary finds the document.
Prefer to keep AI out of the loop? Settings → Local AI → turn off "Use the
local AI model" or just the automatic summarising.

## Try Stage 5 — OCR for scanned documents (setup ~10 minutes)

Text extraction for normal PDFs needs only a package refresh:

```
pip install -r backend/requirements.txt
```

OCR of *scanned* PDFs needs two free Windows programs, installed once:

1. **Tesseract** (the OCR engine): run the installer from
   https://github.com/UB-Mannheim/tesseract/wiki — the default location is
   fine; the app finds it automatically.
2. **Poppler** (renders PDF pages to images): download the latest "Release"
   zip from https://github.com/oschwartz10612/poppler-windows/releases ,
   unzip somewhere permanent (e.g. `C:\poppler`), and paste the path to its
   `Library\bin` folder into **Settings → OCR → Poppler location**.

That's it. Start the app, download something scanned (or press **Run OCR**
on any document marked *required*), and watch the Dashboard — when the job
finishes, the scan is full-text searchable like everything else. Optional:
PaddleOCR usually reads Indian regulatory scans a little better
(`pip install paddlepaddle paddleocr`); the app uses it when present and
falls back to Tesseract when not — no configuration needed.

## Try Stage 4 — analyse and download (takes five minutes)

One-time setup (in addition to Stages 2–3): refresh the backend packages,
then install the headless browser engine the MCA portal needs:

```
pip install -r backend/requirements.txt
playwright install chromium
```

(The second command downloads ~150 MB once. RBI and SEBI work without it;
it is only used for pages that build themselves with JavaScript, such as
the MCA V3 portal — the app decides automatically.)

Then `npm start` from the `frontend` folder as before. Add a source — for
example an RBI notifications page or a SEBI circulars listing — and press
**Analyse**. The app reads the page, reports how many files it found, and
starts downloading them (automatic by default; switch off in Settings →
Website analysis). Watch the Downloads screen fill in, then find the files
under Documents — named like `RBI - Master Direction - … - 2026-07-15.pdf`
and filed under `data/library/RBI/`. Downloading the same source twice wastes
nothing: files already fetched are skipped, and identical files reached from
different links are marked duplicates.

**A note on MCA:** parts of the MCA site now sit behind a login. The app
reads public pages only — when it meets a login wall it says so plainly on
the source (and in the Review Queue) instead of failing mysteriously. For
logged-in documents, download the file in your browser as usual; later
stages add importing your own files into the library.

## Try Stage 3 — the desktop app (takes ten minutes)

Two one-time installs, then two commands.

1. **Python setup** (if not done in Stage 2): install Python 3.12 from
   https://python.org (tick "Add python.exe to PATH"), then from the project
   folder run `pip install -r backend/requirements.txt`.
2. **Node.js**: install the LTS version from https://nodejs.org (keep
   clicking Next). Node is what runs the desktop window.

Then, in a terminal:

```
cd path\to\legal-knowledge-manager\frontend
npm install
npm start
```

`npm install` runs once and downloads the interface's parts (Electron, React,
fonts) — after that the app is fully offline. `npm start` opens the window;
the Python backend is started and stopped for you automatically. Add your
first source with the button on the Dashboard, try the search screen, and
switch between the light and dark themes in Settings → Preferences.

## Try Stage 2 (takes five minutes)

You need Python 3.12 (see Stage 1 below). From the project folder, install
the backend packages once, then run the self-check:

```
pip install -r backend/requirements.txt
python backend/scripts/dev_check.py
```

The self-check starts the real backend against a throwaway database, walks
every endpoint group end-to-end, prints an `[ok]` line per check, and ends
with `ALL CHECKS PASSED`. Your real `data/` folder is never touched.

Then start the backend for real:

```
python backend/scripts/run_backend.py
```

and open http://127.0.0.1:8756/docs in your browser — interactive
documentation where every endpoint can be tried with the "Try it out"
button. Add a source, list it, search — this same API is what the desktop
interface talks to. Buttons whose workers arrive later
(analyze/download → Stage 4, OCR → Stage 5, AI summaries → Stage 6) answer
with a clear message saying so instead of failing confusingly.

## Try Stage 1 (takes two minutes)

You need Python 3.12 installed (free, from https://python.org — on Windows,
tick "Add python.exe to PATH" during install). Nothing else is required yet.

Open a terminal (on Windows: press Start, type `cmd`, press Enter), go to
this folder, and run:

```
cd path\to\legal-knowledge-manager
python backend/scripts/init_db.py --selftest
```

You should see a series of `[ok]` lines ending with a self-test pass and a
list of the database tables. That confirms your machine can run the storage
and search engine at the heart of the app. Run it again any time — it is safe
to repeat, and `--reset` rebuilds the database from scratch if you ever want
a clean start.

## What gets stored where

All of your data lives in one folder, `data/`, next to the app:
`data/db` holds the SQLite database, `data/library` holds your downloaded
documents (organized into folders like `RBI/` or by topic — configurable),
and `data/logs` holds diagnostic logs. Back up your entire knowledge base by
copying that one folder.

## Technology (all free and open source)

Electron + React for the desktop interface (deliberately with no build step —
plain JavaScript and hand-written CSS, so what is on disk is what runs);
Python 3.12 with
FastAPI for the backend; Playwright for reading websites; SQLite with FTS5
for storage and full-text search; PaddleOCR with Tesseract fallback for
scanned documents; pdfplumber/pypdf for PDF text; and Ollama running a local
open-weight model (Qwen or Llama) for summaries and classification. Later
stages document the one-time installs each piece needs on Windows.

## Building the rest

The project is being generated in stages, each producing complete runnable
code: Stage 2 added the backend API, Stage 3 the desktop interface, and next
Stage 4 brings website analysis and downloading, Stage 5 OCR, Stage 6 local
AI, Stage 7 search refinements, and Stage 8 the one-click Windows installer.
