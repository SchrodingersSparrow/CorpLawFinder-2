"""Repository for ``saved_searches`` (Stage 7).

A saved search is a name, the query text, and the filters that were active
(authority, file kind, sort…) as JSON. Saving under an existing name updates
it — refining a saved search should not demand deleting it first. The list
is ordered by most recently used.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.errors import InvalidInputError, NotFoundError
from app.repositories.base import BaseRepository, row_to_dict, rows_to_dicts

_MAX_SAVED = 100


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    try:
        row["filters"] = json.loads(row.pop("filters_json", None) or "{}")
    except (TypeError, ValueError):
        row["filters"] = {}
    return row


class SavedSearchesRepository(BaseRepository):
    async def list(self) -> dict[str, Any]:
        rows = await self.db.fetch_all(
            "SELECT * FROM saved_searches"
            " ORDER BY COALESCE(last_used_at, created_at) DESC, id DESC"
            f" LIMIT {_MAX_SAVED}"
        )
        return {"items": [_decode(r) for r in rows_to_dicts(rows)]}

    async def save(
        self, name: str, query: str, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create, or update the search saved under the same name."""
        clean_name = " ".join((name or "").split())
        clean_query = (query or "").strip()
        if not clean_name:
            raise InvalidInputError("Please give the saved search a name.")
        if len(clean_name) > 60:
            raise InvalidInputError("Saved search names can be 60 characters at most.")
        if not clean_query:
            raise InvalidInputError("There is no search query to save.")
        filters_json = json.dumps(
            {k: v for k, v in (filters or {}).items() if v not in (None, "")},
            ensure_ascii=False,
        )
        await self.db.execute(
            """
            INSERT INTO saved_searches (name, query, filters_json)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                query = excluded.query,
                filters_json = excluded.filters_json,
                last_used_at = strftime('%Y-%m-%d %H:%M:%f','now')
            """,
            (clean_name, clean_query, filters_json),
        )
        # Fetch by name: on the DO UPDATE branch, lastrowid is not trustworthy.
        row = await self.db.fetch_one(
            "SELECT * FROM saved_searches WHERE name = ? COLLATE NOCASE",
            (clean_name,),
        )
        return _decode(row_to_dict(row))  # type: ignore[arg-type]

    async def touch(self, search_id: int) -> dict[str, Any]:
        """Record a use and return the search (the Search screen recalls it)."""
        result = await self.db.execute(
            "UPDATE saved_searches SET last_used_at = strftime('%Y-%m-%d %H:%M:%f','now') WHERE id = ?",
            (search_id,),
        )
        if result.rowcount == 0:
            raise NotFoundError("saved search", search_id)
        row = await self.db.fetch_one(
            "SELECT * FROM saved_searches WHERE id = ?", (search_id,)
        )
        return _decode(row_to_dict(row))  # type: ignore[arg-type]

    async def delete(self, search_id: int) -> None:
        result = await self.db.execute(
            "DELETE FROM saved_searches WHERE id = ?", (search_id,)
        )
        if result.rowcount == 0:
            raise NotFoundError("saved search", search_id)
