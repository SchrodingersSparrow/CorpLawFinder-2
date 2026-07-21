#!/usr/bin/env python3
"""Initialise the Legal Knowledge Manager database.

Standard-library only, so it works immediately after installing Python —
before any ``pip install``. Idempotent: safe to run repeatedly.

What it does
    1. Verifies the bundled SQLite supports FTS5 full-text search.
    2. Creates the runtime folders (data/db, data/library, data/logs).
    3. Applies backend/db/schema.sql and records schema version 1.
    4. Seeds default settings and the default legal-topic tags.
    5. Optionally runs an end-to-end self-test (--selftest): inserts a fake
       document, confirms it is findable via full-text search, then removes
       it and confirms the search index cleaned itself up.

Usage (from the project root)
    python backend/scripts/init_db.py
    python backend/scripts/init_db.py --selftest
    python backend/scripts/init_db.py --reset          # delete DB and rebuild
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

# Make `app.*` importable when run as a plain script from anywhere.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core import defaults  # noqa: E402  (import after sys.path tweak)

OK = "[ok]"
FAIL = "[!!]"


def check_fts5() -> bool:
    """Return True if this Python's SQLite build includes FTS5."""
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE fts_check USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False


def ensure_directories() -> None:
    for path in (defaults.DB_DIR, defaults.LIBRARY_ROOT, defaults.LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def apply_schema(conn: sqlite3.Connection) -> None:
    sql = defaults.SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, description) VALUES (?, ?)",
        (defaults.SCHEMA_VERSION, "base schema"),
    )
    conn.commit()


def seed_settings(conn: sqlite3.Connection) -> int:
    """Insert default settings that are not already present. Returns count added."""
    added = 0
    for key, value in defaults.DEFAULT_SETTINGS.items():
        cur = conn.execute(
            "INSERT OR IGNORE INTO settings (key, value_json) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        added += cur.rowcount
    conn.commit()
    return added


def seed_topic_tags(conn: sqlite3.Connection) -> int:
    """Seed the default legal topics as 'topic' tags. Returns count added."""
    topics = defaults.DEFAULT_SETTINGS["topics.default"]
    added = 0
    for name in topics:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tags (name, kind) VALUES (?, 'topic')",
            (name,),
        )
        added += cur.rowcount
    conn.commit()
    return added


def run_selftest(conn: sqlite3.Connection) -> None:
    """Insert -> search -> delete a throwaway document; raise on any mismatch."""
    marker = f"selftest-{uuid.uuid4().hex}"
    # FTS5 gives characters like '-' special meaning in query syntax; wrapping
    # the term in double quotes searches it as a literal phrase. The Stage 7
    # search service applies the same escaping to user queries.
    fts_query = f'"{marker}"'

    doc_id = conn.execute(
        "INSERT INTO documents (original_filename, file_kind, sha256) VALUES (?, 'pdf', ?)",
        ("selftest.pdf", marker),
    ).lastrowid

    conn.execute(
        """
        INSERT INTO search_index (document_id, title, authority, doc_type, body, summary, tags)
        VALUES (?, ?, 'RBI', 'Master Direction', ?, '', 'KYC')
        """,
        (
            doc_id,
            "Master Direction on KYC (self-test)",
            f"Know Your Customer obligations for regulated entities {marker}",
        ),
    )
    conn.commit()

    row = conn.execute(
        "SELECT rowid FROM search_fts WHERE search_fts MATCH ? ORDER BY bm25(search_fts)",
        (fts_query,),
    ).fetchone()
    if row is None or row[0] != doc_id:
        raise RuntimeError("FTS5 search did not return the inserted test document")

    # Deleting the document must cascade to search_index, whose trigger must
    # clean the FTS mirror.
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()

    leftover = conn.execute(
        "SELECT COUNT(*) FROM search_fts WHERE search_fts MATCH ?", (fts_query,)
    ).fetchone()[0]
    if leftover != 0:
        raise RuntimeError("FTS index was not cleaned up after document deletion")


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'search_fts_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise the LKM database.")
    parser.add_argument("--reset", action="store_true",
                        help="DELETE the existing database and rebuild from scratch")
    parser.add_argument("--selftest", action="store_true",
                        help="run an end-to-end insert/search/delete check")
    args = parser.parse_args()

    print("Legal Knowledge Manager — database initialiser")
    print(f"  project root : {defaults.PROJECT_ROOT}")
    print(f"  database     : {defaults.DB_PATH}")

    if not check_fts5():
        print(f"{FAIL} This Python's SQLite build lacks FTS5 full-text search.")
        print("     Install Python 3.12 from python.org (its SQLite includes FTS5).")
        return 1
    print(f"{OK} SQLite FTS5 available")

    if args.reset and defaults.DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(defaults.DB_PATH) + suffix)
            if p.exists():
                p.unlink()
        print(f"{OK} Existing database removed (--reset)")

    ensure_directories()
    print(f"{OK} Runtime folders ready under {defaults.APP_HOME}")

    try:
        conn = sqlite3.connect(defaults.DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")

        apply_schema(conn)
        print(f"{OK} Schema applied (version {defaults.SCHEMA_VERSION})")

        n_settings = seed_settings(conn)
        n_topics = seed_topic_tags(conn)
        print(f"{OK} Seeded {n_settings} setting(s), {n_topics} topic tag(s) "
              "(0 means already present)")

        if args.selftest:
            run_selftest(conn)
            print(f"{OK} Self-test passed: insert -> FTS5 search -> cascade cleanup")

        names = table_names(conn)
        print(f"{OK} {len(names)} tables/views present:")
        print("     " + ", ".join(names))
        conn.close()
    except Exception as exc:  # pragma: no cover - CLI top level
        print(f"{FAIL} {type(exc).__name__}: {exc}")
        return 1

    print("Done. The database is ready for Stage 2 (backend API).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
