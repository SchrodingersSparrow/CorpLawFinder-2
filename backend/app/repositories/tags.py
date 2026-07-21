"""Repository for ``tags`` and ``document_tags`` (topics / keywords).

Functions ending in ``_sync`` take a raw ``sqlite3.Connection`` so they can be
composed inside a single transaction via ``Database.run`` (for example: assign
a tag AND rebuild the document's search index atomically).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.core.errors import InvalidInputError, NotFoundError
from app.models.enums import TagKind, TagOrigin
from app.repositories.base import BaseRepository, row_to_dict, rows_to_dicts


# -- sync cores (composable inside transactions) ----------------------------

def get_or_create_tag_sync(
    conn: sqlite3.Connection, name: str, kind: str = TagKind.CUSTOM
) -> dict[str, Any]:
    """Case-insensitive get-or-create; returns the tag row as a dict."""
    clean = " ".join((name or "").split())
    if not clean:
        raise InvalidInputError("Tag name cannot be empty")
    if len(clean) > 60:
        raise InvalidInputError("Tag name is too long (60 characters max)")
    if kind not in TagKind:
        raise InvalidInputError(f"Unknown tag kind {kind!r}")
    row = conn.execute(
        "SELECT * FROM tags WHERE name = ? COLLATE NOCASE", (clean,)
    ).fetchone()
    if row is not None:
        return dict(row)
    cur = conn.execute("INSERT INTO tags (name, kind) VALUES (?, ?)", (clean, kind))
    row = conn.execute("SELECT * FROM tags WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def assign_tag_sync(
    conn: sqlite3.Connection,
    document_id: int,
    tag_id: int,
    origin: str = TagOrigin.USER,
    confidence: float | None = None,
) -> None:
    if origin not in TagOrigin:
        raise InvalidInputError(f"Unknown tag origin {origin!r}")
    conn.execute(
        """
        INSERT INTO document_tags (document_id, tag_id, origin, confidence)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(document_id, tag_id)
        DO UPDATE SET origin = excluded.origin, confidence = excluded.confidence
        """,
        (document_id, tag_id, origin, confidence),
    )


def tag_names_for_document_sync(conn: sqlite3.Connection, document_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name FROM document_tags dt
        JOIN tags t ON t.id = dt.tag_id
        WHERE dt.document_id = ?
        ORDER BY t.name COLLATE NOCASE
        """,
        (document_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def tags_for_document_sync(
    conn: sqlite3.Connection, document_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.kind, dt.origin, dt.confidence
        FROM document_tags dt
        JOIN tags t ON t.id = dt.tag_id
        WHERE dt.document_id = ?
        ORDER BY t.name COLLATE NOCASE
        """,
        (document_id,),
    ).fetchall()
    return [dict(r) for r in rows]


class TagsRepository(BaseRepository):
    async def list(self, kind: str | None = None) -> list[dict[str, Any]]:
        """All tags with usage counts, for the sidebar filter and Settings."""
        conditions, params = "", []
        if kind:
            if kind not in TagKind:
                raise InvalidInputError(f"Unknown tag kind {kind!r}")
            conditions = "WHERE t.kind = ?"
            params.append(kind)
        rows = await self.db.fetch_all(
            f"""
            SELECT t.id, t.name, t.kind, t.created_at,
                   COUNT(dt.document_id) AS document_count
            FROM tags t
            LEFT JOIN document_tags dt ON dt.tag_id = t.id
            {conditions}
            GROUP BY t.id
            ORDER BY t.name COLLATE NOCASE
            """,
            params,
        )
        return rows_to_dicts(rows)

    async def get(self, tag_id: int) -> dict[str, Any]:
        row = await self.db.fetch_one("SELECT * FROM tags WHERE id = ?", (tag_id,))
        if row is None:
            raise NotFoundError("tag", tag_id)
        return row_to_dict(row)  # type: ignore[return-value]

    async def create(self, name: str, kind: str = TagKind.CUSTOM) -> dict[str, Any]:
        return await self.db.run(lambda conn: get_or_create_tag_sync(conn, name, kind))

    async def document_ids_with_tag(self, tag_id: int) -> list[int]:
        rows = await self.db.fetch_all(
            "SELECT document_id FROM document_tags WHERE tag_id = ?", (tag_id,)
        )
        return [r["document_id"] for r in rows]
