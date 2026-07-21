-- Stage 7: saved searches — a named query plus its filters, recallable with
-- one click from the Search screen. IF NOT EXISTS keeps this idempotent for
-- fresh databases whose base schema already includes the table.

CREATE TABLE IF NOT EXISTS saved_searches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    query        TEXT    NOT NULL,
    filters_json TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);
