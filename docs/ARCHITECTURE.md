# Legal Knowledge Manager — Architecture (v1)

A local-first desktop application for transaction lawyers in India. It saves
legal research URLs (RBI, SEBI, MCA and others), downloads the documents found
there, organizes and renames them intelligently, OCRs scanned PDFs, summarizes
and classifies them with a local LLM, and makes everything searchable — all on
the user's own machine, using only free and open-source software. The only
network activity is fetching the user's chosen web pages and documents.

## System overview

```
┌───────────────────────────────────────────────────────────────────┐
│                        Electron desktop shell                     │
│   ┌───────────────────────────────────────────────────────────┐   │
│   │     React UI, no build step (dashboard, tables, search)   │   │
│   └───────────────────────────┬───────────────────────────────┘   │
└───────────────────────────────┼───────────────────────────────────┘
                     HTTP, 127.0.0.1:8756 only
┌───────────────────────────────▼───────────────────────────────────┐
│                     FastAPI backend (Python 3.12)                 │
│                                                                   │
│  API routers ──► Services ◄── asyncio job queue (with cancel)     │
│                   │                                               │
│   ┌───────────────┼───────────────┬─────────────┬──────────────┐  │
│   ▼               ▼               ▼             ▼              ▼  │
│ Analyzer      Downloader        OCR           AI            Search│
│ (stdlib fetch  (stdlib urllib,  (PaddleOCR,   (Ollama HTTP  (FTS5│
│  + html.parser; SHA-256,         Tesseract     API, local    bm25│
│  Playwright     retries, dedupe) fallback)     model)       rank)│
│  for JS pages)                                                   │
│   └───────────────┴───────┬───────┴─────────────┴──────────────┘  │
│                           ▼                                       │
│     Repositories (hand-written SQL, stdlib sqlite3 bridge)        │
└───────────────┬───────────────────────────┬───────────────────────┘
                ▼                           ▼
      SQLite database (WAL,          File library on disk
      FTS5 search index)             data/library/RBI/… etc.
                                            ▲
                    Ollama runs as a separate local process
                    (installed once; models pulled once)
```

The Electron main process spawns the Python backend on startup, waits for its
`/health` endpoint, and shuts it down cleanly on exit. The backend binds to
the loopback interface only and is never reachable from the network. All
state — the SQLite database, the document library, and logs — lives under a
single `data/` folder (overridable via the `LKM_HOME` environment variable),
so backing up the entire knowledge base means copying one directory.

## The processing pipeline

Everything the app does to a source flows through one pipeline, and every
step records its status in the database so the UI can show live progress and
the Review Queue can catch failures.

When the user adds a URL (single, multiple, or CSV import), a `sources` row is
created with status `pending`. The **Analyzer** then loads the page — with
Playwright's headless Chromium when the page needs JavaScript rendering,
falling back to a plain `requests` fetch for static pages — and parses it with
BeautifulSoup/lxml. It records the page title, guesses the issuing authority
and document type from URL patterns and page text (RBI, SEBI and MCA have
recognizable structures for Master Directions, circulars, notifications, press
releases and FAQs), and enumerates every downloadable file link (PDF, DOCX,
XLSX, ZIP, HTML). The raw findings are stored in `sources.analysis_json`, and
one `downloads` row is queued per file.

The **Downloader** works through the queue with bounded concurrency, a polite
delay between requests to the same site, exponential-backoff retries, and a
custom User-Agent. Each completed file is hashed with SHA-256 *before* being
committed to the library; if the hash already exists in `documents`, the
attempt is marked `skipped_duplicate` and the user is warned rather than
storing a second copy. New files produce a `documents` row holding the
original filename, source URL, size, hash, and download date.

Post-download processing then runs per document. Text extraction (pdfplumber,
with pypdf as backup) determines whether a PDF has a real text layer: pages
averaging fewer than a configurable number of extractable characters are
treated as scanned images, and the document is flagged `OCR required`, with
one-click (or automatic) OCR available. OCR renders pages to images
(pdf2image + Poppler) and runs PaddleOCR, falling back to Tesseract if Paddle
is unavailable or fails; the original PDF is never modified, and the OCR text
is stored in `ocr_results`. **Metadata extraction** runs regex/rule patterns
first (circular numbers like `RBI/2025-26/34`, notification numbers, dates in
Indian formats such as `12.03.2025` or `March 12, 2025` normalized to ISO),
then asks the local LLM to fill gaps; every candidate value carries a
confidence score and provenance (`pattern`, `ai`, or `user`) in
`document_metadata`, and accepted values are promoted onto the `documents` row
itself. The **AI service** calls Ollama's local HTTP API with a
strictly-JSON-output prompt to produce the one-line summary, the ~150-word
summary, topics, keywords, and issuing authority; low-confidence outputs land
in the Review Queue instead of being silently trusted. Finally the document is
renamed using the configurable template
(`{authority} - {doc_type} - {title} - {date}` by default, sanitized for
Windows-safe filenames and length limits), filed into the library according to
the folder rule (by authority, e.g. `RBI/`, or by topic, e.g. `FEMA/`), and
its flattened text is written to `search_index`, which triggers mirror into
the FTS5 table.

If any step fails after retries — a download error, an OCR crash, unusable
metadata, or an AI answer below the confidence threshold — a `review_items`
row is created so a human can fix it from the Review Queue screen. Nothing
fails silently.

## Data design

The schema (single source of truth: `backend/db/schema.sql`) has eleven core
tables plus the search pair. `sources` holds saved URLs and analysis results;
`documents` is one row per unique file and carries the *canonical* metadata
(title, authority, doc_type, doc_date, language) used for filtering, naming
and foldering; `document_metadata` keeps every extraction candidate with
confidence and provenance so the canonical values are auditable and editable;
`downloads` is the download attempt queue and audit trail; `ocr_results` and
`ai_summaries` store processing outputs per run; `tags` and `document_tags`
implement AI-suggested, user-editable topic classification; `review_items`
powers the human review queue; `logs` mirrors structured logs for the in-app
viewer; and `settings` holds all user-editable configuration as JSON values
seeded from `app/core/defaults.py`. Foreign keys are enforced everywhere, with
`ON DELETE CASCADE` for child records and `ON DELETE SET NULL` where history
should survive (a document outlives its source).

Search uses the external-content FTS5 pattern: a plain `search_index` table
holds the flattened searchable text per document (title, authority, doc type,
native text plus OCR text, AI summary, tag names), and triggers keep the
`search_fts` virtual table in sync. This gives bm25 ranking and `snippet()`
highlighting while keeping the index trivially rebuildable and inspectable.
Filters (authority, date range, topic, document type) combine an FTS `MATCH`
with ordinary indexed `WHERE` clauses on `documents`.

## Key design decisions

**Hand-written SQL over a standard-library SQLite bridge, no ORM.** FTS5
queries, triggers and ranking functions are awkward through an ORM, and a
single-user desktop app gains nothing from ORM abstraction. Stage 2 went one
step further than planned: instead of the `aiosqlite` package, the bridge in
`core/database.py` is ~150 lines of standard library — a dedicated worker
thread owns the single WAL-mode connection and asyncio callers await queued
jobs, with `db.run(fn)` executing a whole function as one atomic
transaction (commit on return, rollback on exception). Repositories get
real multi-statement transactions (e.g. "insert document + rebuild its
search index"), and two packages disappeared from the install. All SQL lives
in the repository layer; services never touch the database directly.

**Configuration is a frozen stdlib dataclass** (`core/config.py`) reading a
few `LKM_*` environment variables or a `.env` file — `pydantic-settings` was
dropped for the same reason: fewer packages to install, nothing lost.

**Web work is standard-library too; the browser is the exception.** Stage 4
dropped `requests`, `beautifulsoup4` and `lxml`: pages are fetched with
`urllib`, parsed with `html.parser`, and files are streamed to disk (hashing
SHA-256 as they arrive) by a small hand-written downloader with retry/backoff
that distinguishes transient failures (retried) from permanent ones (reported
immediately). This keeps the entire analysis and download pipeline testable
with nothing installed — the test suite runs it against a loopback fixture
server. The one genuine dependency is Playwright's headless Chromium, lazily
imported and used only when a page is detected to be a JavaScript shell (the
MCA V3 portal); when it isn't installed the app says exactly what to run.
Login-walled pages (parts of MCA now) are detected — password forms, login
URLs, 401/403 — and reported honestly on the source and in the Review Queue:
the app reads public pages only and never automates a login.

**The installer bundles the engine, not the ecosystem.** Stage 8 freezes
the backend with PyInstaller into a self-contained ``lkm-backend`` folder
(schema and migrations ship inside; ``defaults.py`` detects frozen mode and
resolves paths bundle-relative) which electron-builder places in the app's
resources; the packaged shell spawns that executable instead of Python and
points ``LKM_HOME`` at ``Documents\Legal Knowledge Manager`` so the user's
data lives in one visible, backup-by-copy folder that uninstalling never
touches. Heavy optional pieces stay external by design — Tesseract, Poppler,
Ollama, Playwright's Chromium — detected at runtime with exact install
guidance, and PaddleOCR's ~1 GB stack is excluded from the freeze (the
Tesseract fallback carries packaged installs). Everything about packaging
that can be verified off-Windows is under test: frozen-path resolution, the
generated multi-size icon's binary structure, spec/data completeness,
version agreement between backend and frontend, and the packaged spawn code
path; the installer itself is built on Windows by ``build-installer.ps1``,
which runs both test suites before it will package anything.

**User input never reaches FTS5 as syntax.** Stage 7 gave search a small
query language — quoted phrases, ``OR``, ``-exclusions``, automatic prefix
matching on the word being typed — implemented as a pure tokenizer that
emits every term inside double quotes with interior quotes stripped, so no
input can inject FTS5 operators; the test suite fires hostile strings at a
live index to hold that line. Sort orders come from a validated whitelist
map, never from the request string. Saved searches arrived via the schema's
first real migration (``db/migrations/002``), exercising the numbered
``schema_migrations`` machinery that future stages will rely on: fresh
installs get the table from the base schema, existing databases catch up on
next launch, and both paths are covered by a test that upgrades a genuinely
old database file.

**The model advises; it never overrules.** Stage 6 asks the local Ollama
model for one strict-JSON answer per document (summary, metadata, topics,
keywords, self-reported confidence) through a ~100-line standard-library
client. Every extracted field is stored as a ``document_metadata`` candidate
with confidence and ``extractor='ai'`` — the audit trail — but canonical
document fields are filled **only where blank**: a value the user typed or
the analyzer derived is never overwritten. Topics become removable tags;
summaries live in ``ai_summaries`` and reach search via the same
index-rebuild merging OCR uses; confidence below the configured threshold
files a Review Queue item rather than being trusted silently. The model is
chosen by what is actually installed (falling back to the small model), and
every failure — Ollama not running, model not pulled — carries the exact
command to fix it.

**Native text first; OCR text lives beside the document, not inside it.**
Stage 5 reads Word, Excel and HTML files with the standard library (they are
ZIP/XML underneath) and PDFs via pdfplumber with a pypdf fallback; a page-count
-scaled character threshold decides searchable vs scanned. OCR output is
stored in ``ocr_results`` — the document's own ``text_content`` stays reserved
for native text — and the search index merges the newest completed OCR text
in at rebuild time, so re-running OCR can only improve search, never corrupt
the original extraction. The heavy pieces (pdf2image/Poppler, Tesseract,
PaddleOCR) are imported lazily, auto-detected in their usual Windows homes,
and injectable in tests, which is how the whole pipeline stays verifiable on
a machine with none of them installed.

**The desktop interface has no build step.** Stage 3 replaced the planned
Vite + Tailwind toolchain with plain ES modules served straight from disk:
React 18.3.1's prebuilt browser bundles, a 40-line hyperscript helper
(`renderer/js/h.js`) instead of JSX, and a hand-written two-theme CSS design
system (`renderer/styles/`). The user's install is `npm install` and
`npm start` — nothing compiles, and every file that ships is the file that
runs, which also makes the whole interface verifiable line by line. Backend
process management lives in `electron/backend.cjs` with no Electron imports,
so the spawn/health-poll/shutdown logic is covered by plain `node --test`
integration tests against a stub server. The renderer is sandboxed
(`contextIsolation`, no Node access); a four-function preload bridge is the
only privileged surface, and file paths are always resolved in the main
process from a document id — the page can never name an arbitrary path.

**One durable status model instead of a job table.** The asyncio task queue is
in-memory, as the spec requires, but every unit of work (`downloads`,
`ocr_results`, `ai_summaries`) persists its status in its own table. On
startup the backend re-queues anything left in `queued` or `running`, so a
crash or forced shutdown loses no work. Long-running tasks hold a cancellation
token checked between pages/chunks, so the UI's Cancel button works.

**Graceful degradation everywhere.** The app must stay useful even when the
heavy optional pieces are missing: without Ollama running, AI features show a
clear "local AI not available" state and everything else works; without
PaddleOCR, Tesseract is used; without Playwright browsers installed, the
analyzer falls back to static fetching. This matters for the "easy to
operate" goal — a half-installed app should degrade, not break.

**Canonical columns + candidate table for metadata.** Filtering and naming
need fast, single-valued fields, but extraction is uncertain. Promoting
accepted values onto `documents` while keeping every candidate (with
confidence and extractor provenance) in `document_metadata` gives both speed
and an audit trail, and makes "edit this value" trivial.

**Loopback-only, offline-first.** The backend binds to 127.0.0.1 and the app
performs no network calls except fetching the URLs and files the user asked
for. No telemetry, no cloud APIs, no accounts.

## Error handling and operability

Retries use exponential backoff with a capped attempt count recorded per
download. User-facing errors are translated into plain language ("The RBI
server did not respond — will retry", not a stack trace), while full detail
goes to the rotating file log and the `logs` table. The downloader deliberately
rate-limits itself per site to be a polite citizen of government servers.
Progress events stream to the UI so long operations show real progress bars,
and every long task is cancellable.

## Windows notes (expanded per stage)

Python 3.12 from python.org includes SQLite with FTS5 — verified automatically
by `init_db.py`. Stage 4 adds one optional install: `playwright install
chromium` (~150 MB, one time) fetches the headless browser used only for
JavaScript-built pages such as the MCA V3 portal — RBI and SEBI work without
it. OCR needs two free external programs on Windows, installed once: Tesseract
(UB Mannheim build, https://github.com/UB-Mannheim/tesseract/wiki — the app
auto-detects its default install path) and Poppler
(https://github.com/oschwartz10612/poppler-windows/releases — point Settings →
OCR → 'Poppler location' at its Library\bin folder). PaddleOCR is optional
(`pip install paddlepaddle paddleocr`); the engine is chosen by actual
availability at queue time, falling back to Tesseract automatically.
Local AI needs Ollama (https://ollama.com) plus one pulled model:
`ollama pull qwen2.5:7b-instruct` (~8 GB RAM) or `qwen2.5:3b-instruct` for
lighter machines — the app uses whichever exists. Documents are processed
entirely on the user's machine; nothing is sent to any cloud service.
Stage 6 requires installing Ollama and pulling one model; `qwen2.5:7b-instruct`
is the default and wants roughly 8 GB of free RAM, with `qwen2.5:3b-instruct`
configured as the fallback for lighter machines. Stage 8 wraps everything with
Electron Builder into a normal Windows installer.

## Module responsibilities

`backend/app/api` holds the FastAPI routers (thin: validate, call service,
shape response). `backend/app/services` contains the five service packages —
`analysis`, `downloader`, `ocr`, `ai`, `search` — plus shared orchestration.
`backend/app/repositories` is the only layer that writes SQL.
`backend/app/models` holds Pydantic schemas and the status enums that mirror
the schema's CHECK constraints. `backend/app/core` provides configuration,
the database wrapper, logging, and (Stage 2) the task queue.
`backend/app/utils` collects small pure helpers such as filename
sanitization and Indian date parsing. The frontend lives entirely under
`frontend/`, and everything runtime-generated stays under `data/`.

## Stage roadmap

| Stage | Delivers | Status |
|------:|----------|--------|
| 1 | Architecture, folder structure, database schema, runnable initializer | **Done** |
| 2 | FastAPI backend: routers, repositories, task queue, settings API | **Done** |
| 3 | Electron + React desktop UI: all screens, dark/light, resizable panels | **Done** |
| 4 | Analyzer + downloader: dedupe, retries, smart naming, login detection | **Done** |
| 5 | Text extraction, scanned-PDF detection, PaddleOCR/Tesseract OCR | **Done** |
| 6 | Local AI: summaries, metadata assist, topic classification, review gate | **Done** |
| 7 | Query language, sort, saved searches, first schema migration | **Done** |
| 8 | PyInstaller-frozen backend + electron-builder NSIS installer | **Done** |
