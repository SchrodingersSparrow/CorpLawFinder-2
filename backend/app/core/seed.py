"""Idempotent startup seeding.

Runs on every backend start (cheap: INSERT OR IGNORE only) so the app also
works if the user never ran ``scripts/init_db.py`` — deleting the database
and relaunching always yields a working, pre-seeded installation.

The *data* lives in :mod:`app.core.defaults`, which ``init_db.py`` reads too,
so there is exactly one place where default settings and topics are defined.
"""

from __future__ import annotations

import json
import sqlite3

from app.core import defaults
from app.core.database import Database


async def seed_defaults(db: Database) -> dict[str, int]:
    """Seed default settings and topic tags; returns counts actually added."""

    def job(conn: sqlite3.Connection) -> dict[str, int]:
        settings_added = 0
        for key, value in defaults.DEFAULT_SETTINGS.items():
            cur = conn.execute(
                "INSERT OR IGNORE INTO settings (key, value_json) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            settings_added += cur.rowcount
        topics_added = 0
        for name in defaults.DEFAULT_SETTINGS["topics.default"]:
            cur = conn.execute(
                "INSERT OR IGNORE INTO tags (name, kind) VALUES (?, 'topic')",
                (name,),
            )
            topics_added += cur.rowcount
        return {"settings_added": settings_added, "topics_added": topics_added}

    return await db.run(job)
