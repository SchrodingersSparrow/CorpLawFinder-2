# Backend API reference (Stage 2)

Base URL: `http://127.0.0.1:8756` — loopback only, never reachable from the
network. Interactive, always-current documentation lives at `/docs` while the
backend is running; this file is the quick human-readable map.

All endpoints are under `/api`. Errors always use one envelope:

```json
{ "error": { "code": "duplicate", "message": "This URL is already in your sources.", "detail": { } } }
```

Codes: `not_found` (404), `duplicate` (409), `conflict` (409),
`feature_not_available` (409 — the worker for that button arrives in a later
stage; the message says which), `invalid_input` (422), `internal_error` (500).

Paged responses share one shape:
`{ "items": [...], "total": n, "page": 1, "page_size": 25, "pages": n }`.

## System

| Method & path | What it does |
|---|---|
| GET `/api/health` | Liveness: app version, schema version, database path |
| GET `/api/capabilities` | What is installed on this machine, per feature (the UI shows badges from this) |

## Sources (saved URLs)

| Method & path | What it does |
|---|---|
| POST `/api/sources` | Add one URL (`https://` added if missing); 409 with the existing row if already saved |
| POST `/api/sources/batch` | Add many; returns `added` / `duplicates` / `invalid` buckets, never fails a whole batch for one bad row |
| POST `/api/sources/import-csv` | Multipart CSV upload; first column = URL, or use a header row with `url`,`title`,`notes` columns |
| GET `/api/sources` | List; filters `status`, `authority`, `q`; paging + `sort`/`order` |
| GET `/api/sources/{id}` | One source |
| PATCH `/api/sources/{id}` | Edit `title`, `notes`, `authority`, `source_type` |
| DELETE `/api/sources/{id}` | Remove the source (its documents remain, unlinked) |
| POST `/api/sources/{id}/analyze` | Analyse the page: find document links, detect login walls, guess authority (**live since Stage 4**) |
| POST `/api/sources/{id}/download` | Download every file found on the source, with dedupe and smart naming (**live since Stage 4**) |

## Documents

| Method & path | What it does |
|---|---|
| GET `/api/documents` | List; filters `authority`, `doc_type`, `file_kind`, `ocr_status`, `status`, `topic`, `source_id`, `q`, `date_from`, `date_to`; paging + sorting |
| GET `/api/documents/facets` | Distinct authorities / doc types / file kinds for filter dropdowns |
| GET `/api/documents/{id}` | Full detail: metadata audit trail, tags, AI summaries, OCR runs, download history |
| GET `/api/documents/{id}/text` | Extracted text (native, else latest OCR) — separate call because it can be large |
| GET `/api/documents/{id}/file` | The stored file itself (for preview / open) |
| PATCH `/api/documents/{id}` | Correct `title`, `authority`, `doc_type`, `doc_date`, `language`; audited as a user edit, search index refreshed |
| DELETE `/api/documents/{id}?delete_file=true` | Remove document (and by default its file; empty folders pruned) |
| POST `/api/documents/{id}/tags` | Attach a tag by name (created on first use) |
| DELETE `/api/documents/{id}/tags/{tag_id}` | Detach a tag |
| POST `/api/documents/{id}/ocr` | Run OCR on a scanned document; text becomes searchable (**live since Stage 5**) |
| POST `/api/documents/{id}/summarize` | Summarise + extract metadata + classify topics with the local model (**live since Stage 6**) |

## Search

| Method & path | What it does |
|---|---|
| GET `/api/search?q=...` | Full-text search (FTS5, bm25-ranked) across titles, document text, OCR text, summaries and tags, with `[...]`-highlighted snippets; same filters as the documents list, plus `sort` = `relevance` (default) / `newest` / `oldest`. Query language: `"quoted phrases"`, `OR`, `-exclusions`, `*` prefixes; the last word auto-matches as a prefix while typing |
| GET `/api/saved-searches` | Saved searches, most recently used first |
| POST `/api/saved-searches` | Save a search — `{name, query, filters}`; the same name updates in place |
| POST `/api/saved-searches/{id}/use` | Recall a saved search (bumps its recency) |
| DELETE `/api/saved-searches/{id}` | Delete a saved search |
| POST `/api/search/rebuild` | Maintenance: rebuild the whole index from the documents table |

## Downloads, tags, review queue

| Method & path | What it does |
|---|---|
| GET `/api/downloads` | Download history; filters `status`, `source_id`, `document_id` |
| GET `/api/downloads/counts` | Counts by status for the dashboard |
| POST `/api/downloads/{id}/retry` | Re-queue a failed download (**live since Stage 4**) |
| GET `/api/tags` | All tags with per-tag document counts; filter by `kind` |
| POST `/api/tags` | Create a tag |
| DELETE `/api/tags/{id}` | Delete a tag everywhere (affected documents are re-indexed) |
| GET `/api/review` | Review queue; `status=open` (default) / `resolved` / `dismissed` / `all` |
| POST `/api/review/{id}/resolve` | Body `{"status": "resolved"}` or `"dismissed"` |

## Settings, dashboard, logs, jobs

| Method & path | What it does |
|---|---|
| GET `/api/settings` | Every setting: current values, the defaults, and which keys are overridden |
| PUT `/api/settings` | Body `{"values": {"download.max_concurrency": 5}}`; unknown keys and wrong types are rejected with a clear message |
| DELETE `/api/settings/{key}` | Reset one key to its default |
| POST `/api/settings/reset` | Reset everything to defaults |
| GET `/api/dashboard` | One call: counts, 8 most recent documents, 5 most recent sources, live jobs, download status counts |
| GET `/api/logs` | Activity log, newest first; filters `category`, `level`; cursor paging via `before_id` / returned `next_before_id` |
| GET `/api/jobs` | Background jobs (add `?active=true` for only queued/running) |
| GET `/api/jobs/{id}` | One job |
| POST `/api/jobs/{id}/cancel` | Cancel a queued job instantly or ask a running one to stop |

## Notes for later stages

The Stage 3 UI polls `/api/jobs?active=true` and `/api/dashboard` for live
status. Stages 4–6 register queue handlers (`analyze_source`,
`download_file`, `run_ocr`, `ai_summarize`); the moment a handler is
registered, the corresponding buttons above stop answering
`feature_not_available` and start doing real work — no API changes needed.
On startup the backend re-queues any work a crash left in `queued`/`running`
(durable statuses live in `downloads`, `ocr_results`, `ai_summaries`).
