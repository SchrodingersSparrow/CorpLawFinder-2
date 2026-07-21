-- ============================================================================
-- Legal Knowledge Manager — SQLite schema, version 1
-- ----------------------------------------------------------------------------
-- Single source of truth for the database structure.
-- Every statement is idempotent (IF NOT EXISTS) so this file can be applied
-- safely on every application start. Structural changes in later versions go
-- into backend/db/migrations/ and are recorded in schema_migrations.
--
-- Conventions
--   * All timestamps are TEXT in ISO-8601 UTC, e.g. '2026-07-17 09:30:00'.
--   * doc_date is the date printed ON the legal instrument itself (the date a
--     circular / notification was issued), stored as 'YYYY-MM-DD'.
--   * JSON payloads are stored in *_json TEXT columns.
--   * Status vocabularies are enforced with CHECK constraints and mirrored in
--     backend/app/models/enums.py — keep the two in sync.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- Migration bookkeeping
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    description TEXT    NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- sources — research websites / URLs the user saves (Functional req. 1 & 2)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    url            TEXT    NOT NULL UNIQUE,
    title          TEXT,                          -- user-given or page title
    page_title     TEXT,                          -- <title> found by analyzer
    authority      TEXT,                          -- e.g. RBI, SEBI, MCA
    source_type    TEXT,                          -- e.g. Master Direction, Circular,
                                                  -- Notification, Press Release, FAQ
    notes          TEXT,
    status         TEXT    NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','analyzing','analyzed',
                                     'downloading','completed','failed')),
    pdf_count      INTEGER NOT NULL DEFAULT 0,    -- PDFs discovered on the page
    document_count INTEGER NOT NULL DEFAULT 0,    -- all downloadable files found
    analysis_json  TEXT,                          -- raw analyzer output (links,
                                                  -- detected fields, confidences)
    error_message  TEXT,
    date_added     TEXT    NOT NULL DEFAULT (datetime('now')),
    last_checked   TEXT
);

CREATE INDEX IF NOT EXISTS idx_sources_status    ON sources(status);
CREATE INDEX IF NOT EXISTS idx_sources_authority ON sources(authority);
CREATE INDEX IF NOT EXISTS idx_sources_added     ON sources(date_added);

-- ----------------------------------------------------------------------------
-- documents — one row per unique file in the library (req. 3–7)
--
-- title / authority / doc_type / doc_date / language are the CANONICAL values
-- used for filtering, naming and foldering. Detailed extraction candidates
-- (with confidence and provenance) live in document_metadata; a promotion step
-- copies the accepted value here. This keeps list/filter queries fast without
-- losing the audit trail.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id         INTEGER REFERENCES sources(id) ON DELETE SET NULL,

    -- canonical metadata
    title             TEXT,
    authority         TEXT,
    doc_type          TEXT,
    doc_date          TEXT,                       -- 'YYYY-MM-DD'
    language          TEXT,

    -- file facts
    original_filename TEXT    NOT NULL,
    stored_filename   TEXT,                       -- after smart renaming
    rel_path          TEXT,                       -- path relative to library root
    file_kind         TEXT    NOT NULL
                      CHECK (file_kind IN ('pdf','docx','xlsx','zip','html','other')),
    file_size_bytes   INTEGER,
    sha256            TEXT    NOT NULL UNIQUE,    -- duplicate detection (req. 11)
    download_url      TEXT,
    downloaded_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    page_count        INTEGER,

    -- text / OCR state (req. 6)
    is_searchable     INTEGER,                    -- 1 = native text layer present,
                                                  -- 0 = OCR required, NULL = unchecked
    ocr_status        TEXT    NOT NULL DEFAULT 'not_required'
                      CHECK (ocr_status IN ('not_required','required','queued',
                                            'running','completed','failed')),
    text_content      TEXT,                       -- natively extracted text

    status            TEXT    NOT NULL DEFAULT 'new'
                      CHECK (status IN ('new','processing','ready','failed')),
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_source     ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_documents_authority  ON documents(authority);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type   ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_doc_date   ON documents(doc_date);
CREATE INDEX IF NOT EXISTS idx_documents_ocr_status ON documents(ocr_status);
CREATE INDEX IF NOT EXISTS idx_documents_downloaded ON documents(downloaded_at);
CREATE INDEX IF NOT EXISTS idx_documents_status     ON documents(status);

-- Keep updated_at fresh. recursive_triggers is OFF by default in SQLite, so
-- this trigger cannot re-fire itself; the WHEN guard is extra insurance.
CREATE TRIGGER IF NOT EXISTS trg_documents_touch
AFTER UPDATE ON documents
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE documents SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- ----------------------------------------------------------------------------
-- downloads — every download ATTEMPT (queue + audit trail, req. 3 & 14)
-- A document row is only created on success; failed attempts still leave a
-- downloads row so the Review Queue and logs can show what went wrong.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS downloads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER REFERENCES sources(id)   ON DELETE SET NULL,
    document_id   INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    url           TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','succeeded','failed',
                                    'skipped_duplicate','cancelled')),
    http_status   INTEGER,
    attempts      INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    queued_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at    TEXT,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_downloads_status   ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_downloads_source   ON downloads(source_id);
CREATE INDEX IF NOT EXISTS idx_downloads_document ON downloads(document_id);
CREATE INDEX IF NOT EXISTS idx_downloads_queued   ON downloads(queued_at);

-- ----------------------------------------------------------------------------
-- document_metadata — extraction candidates with confidence (req. 7)
-- field examples: title, authority, doc_type, doc_date, circular_no,
-- notification_no, language, page_count
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_metadata (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    field       TEXT    NOT NULL,
    value       TEXT,
    confidence  REAL    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    extractor   TEXT    NOT NULL DEFAULT 'pattern'
                CHECK (extractor IN ('pattern','ai','user')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (document_id, field)
);

CREATE INDEX IF NOT EXISTS idx_metadata_document ON document_metadata(document_id);

-- ----------------------------------------------------------------------------
-- ocr_results — one row per OCR run (req. 6). Original PDF is never modified;
-- OCR text is stored here and folded into the search index.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ocr_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    engine           TEXT    NOT NULL CHECK (engine IN ('paddleocr','tesseract')),
    status           TEXT    NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued','running','completed','failed','cancelled')),
    text_content     TEXT,
    avg_confidence   REAL,
    page_count       INTEGER,
    duration_seconds REAL,
    error_message    TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_ocr_document ON ocr_results(document_id);
CREATE INDEX IF NOT EXISTS idx_ocr_status   ON ocr_results(status);

-- ----------------------------------------------------------------------------
-- ai_summaries — output of the local LLM (req. 8)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_summaries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    model            TEXT    NOT NULL,             -- e.g. 'qwen2.5:7b-instruct'
    one_line_summary TEXT,
    detailed_summary TEXT,                         -- ~150 words
    topics_json      TEXT,                         -- JSON array of topic strings
    keywords_json    TEXT,                         -- JSON array of keyword strings
    authority        TEXT,                         -- authority as read by the model
    confidence       REAL,                         -- model self-reported, 0..1
    status           TEXT    NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued','running','completed','failed','cancelled')),
    error_message    TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_summaries_document ON ai_summaries(document_id);
CREATE INDEX IF NOT EXISTS idx_summaries_status   ON ai_summaries(status);

-- ----------------------------------------------------------------------------
-- tags + document_tags — topics / keywords, AI-suggested and user-edited
-- (req. 8 & 9). origin records who applied the tag; users may remove AI tags
-- and add their own.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    kind       TEXT    NOT NULL DEFAULT 'topic'
               CHECK (kind IN ('topic','keyword','custom')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS document_tags (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)      ON DELETE CASCADE,
    origin      TEXT    NOT NULL DEFAULT 'ai' CHECK (origin IN ('ai','user')),
    confidence  REAL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (document_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_doctags_tag ON document_tags(tag_id);

-- ----------------------------------------------------------------------------
-- saved_searches — reusable searches: the query plus its filters (Stage 7)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS saved_searches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    query        TEXT    NOT NULL,
    filters_json TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);

-- ----------------------------------------------------------------------------
-- review_items — Human Review Queue (req. 13)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    source_id   INTEGER REFERENCES sources(id)   ON DELETE CASCADE,
    category    TEXT    NOT NULL
                CHECK (category IN ('download_failure','ocr_failure',
                                    'metadata_failure','low_ai_confidence','other')),
    detail      TEXT,
    status      TEXT    NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','resolved','dismissed')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_review_status   ON review_items(status);
CREATE INDEX IF NOT EXISTS idx_review_category ON review_items(category);

-- ----------------------------------------------------------------------------
-- logs — structured application log mirror (req. 14). File logs also exist;
-- this table powers the in-app log viewer.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL DEFAULT (datetime('now')),
    level        TEXT NOT NULL CHECK (level IN ('DEBUG','INFO','WARNING','ERROR')),
    category     TEXT NOT NULL
                 CHECK (category IN ('system','analysis','download','ocr','ai','search')),
    message      TEXT NOT NULL,
    context_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_logs_ts       ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_category ON logs(category);
CREATE INDEX IF NOT EXISTS idx_logs_level    ON logs(level);

-- ----------------------------------------------------------------------------
-- settings — user-editable configuration, JSON-encoded values.
-- Seeded by scripts/init_db.py from app/core/defaults.py (naming template,
-- folder rules, OCR engine, Ollama model, known authorities, default topics…).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- Search (req. 10): external-content FTS5.
--
-- search_index is a plain table holding, per document, the flattened text that
-- should be searchable (title + metadata + native text + OCR text + AI summary
-- + tags). The SearchIndexer service rebuilds a document's row whenever any of
-- those inputs change. Triggers below mirror search_index into the FTS5 table,
-- so ranking (bm25) and snippet() work while the plain table stays easy to
-- rebuild and inspect.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS search_index (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    title       TEXT,
    authority   TEXT,
    doc_type    TEXT,
    body        TEXT,   -- native text + OCR text, concatenated
    summary     TEXT,   -- one-line + detailed AI summary
    tags        TEXT,   -- space-separated tag names
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    title, authority, doc_type, body, summary, tags,
    content='search_index',
    content_rowid='document_id',
    tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS trg_search_index_ai
AFTER INSERT ON search_index
BEGIN
    INSERT INTO search_fts(rowid, title, authority, doc_type, body, summary, tags)
    VALUES (NEW.document_id, NEW.title, NEW.authority, NEW.doc_type,
            NEW.body, NEW.summary, NEW.tags);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_index_ad
AFTER DELETE ON search_index
BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, authority, doc_type,
                           body, summary, tags)
    VALUES ('delete', OLD.document_id, OLD.title, OLD.authority, OLD.doc_type,
            OLD.body, OLD.summary, OLD.tags);
END;

CREATE TRIGGER IF NOT EXISTS trg_search_index_au
AFTER UPDATE ON search_index
BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, authority, doc_type,
                           body, summary, tags)
    VALUES ('delete', OLD.document_id, OLD.title, OLD.authority, OLD.doc_type,
            OLD.body, OLD.summary, OLD.tags);
    INSERT INTO search_fts(rowid, title, authority, doc_type, body, summary, tags)
    VALUES (NEW.document_id, NEW.title, NEW.authority, NEW.doc_type,
            NEW.body, NEW.summary, NEW.tags);
END;

-- ----------------------------------------------------------------------------
-- Convenience views for the Dashboard (req. 12) and Review Queue (req. 13)
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_dashboard_counts AS
SELECT
    (SELECT COUNT(*) FROM sources)                                  AS total_sources,
    (SELECT COUNT(*) FROM documents)                                AS total_documents,
    (SELECT COUNT(*) FROM documents
      WHERE ocr_status IN ('required','queued','running'))          AS documents_needing_ocr,
    (SELECT COUNT(*) FROM documents
      WHERE date(downloaded_at) = date('now','localtime'))          AS downloaded_today,
    (SELECT COUNT(*) FROM review_items WHERE status = 'open')       AS open_review_items;

CREATE VIEW IF NOT EXISTS v_recent_documents AS
SELECT d.id, d.title, d.authority, d.doc_type, d.doc_date, d.file_kind,
       d.is_searchable, d.ocr_status, d.downloaded_at, s.url AS source_url
FROM documents d
LEFT JOIN sources s ON s.id = d.source_id
ORDER BY d.downloaded_at DESC;

CREATE VIEW IF NOT EXISTS v_open_review_items AS
SELECT r.id, r.category, r.detail, r.created_at,
       r.document_id, d.title AS document_title,
       r.source_id,  s.url   AS source_url
FROM review_items r
LEFT JOIN documents d ON d.id = r.document_id
LEFT JOIN sources   s ON s.id = r.source_id
WHERE r.status = 'open'
ORDER BY r.created_at DESC;
