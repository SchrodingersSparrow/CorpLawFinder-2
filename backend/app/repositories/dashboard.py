"""Repository for the Dashboard screen (req. 12).

Reads the convenience views defined in schema.sql, so the numbers shown in
the UI and the numbers a curious user gets from the sqlite3 shell always
agree.
"""

from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository, row_to_dict, rows_to_dicts


class DashboardRepository(BaseRepository):
    async def counts(self) -> dict[str, Any]:
        row = await self.db.fetch_one("SELECT * FROM v_dashboard_counts")
        return row_to_dict(row) or {}

    async def recent_documents(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT * FROM v_recent_documents LIMIT ?", (max(1, int(limit)),)
        )
        return rows_to_dicts(rows)

    async def recent_sources(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT id, url, title, authority, status, pdf_count, document_count,"
            " date_added FROM sources ORDER BY date_added DESC, id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return rows_to_dicts(rows)
