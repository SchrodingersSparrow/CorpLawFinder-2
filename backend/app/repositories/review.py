"""Repository for ``review_items`` — the Human Review Queue (req. 13).

Anything the pipeline is unsure about (failed download, failed OCR, low AI
confidence) lands here instead of silently passing or blocking. Stage 2 wires
listing and resolution; Stages 4-6 create items as they hit problems.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import InvalidInputError, NotFoundError
from app.models.enums import ReviewCategory, ReviewStatus
from app.repositories.base import (
    BaseRepository,
    build_page,
    clamp_paging,
    row_to_dict,
    rows_to_dicts,
)


class ReviewRepository(BaseRepository):
    async def list(
        self,
        status: str | None = ReviewStatus.OPEN,
        category: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        page, page_size, offset = clamp_paging(page, page_size)
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            if status not in ReviewStatus:
                raise InvalidInputError(f"Unknown review status {status!r}")
            conditions.append("r.status = ?")
            params.append(status)
        if category:
            if category not in ReviewCategory:
                raise InvalidInputError(f"Unknown review category {category!r}")
            conditions.append("r.category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = await self.db.fetch_value(
            f"SELECT COUNT(*) FROM review_items r {where}", params
        )
        rows = await self.db.fetch_all(
            f"""
            SELECT r.id, r.category, r.detail, r.status, r.created_at, r.resolved_at,
                   r.document_id, d.title AS document_title,
                   r.source_id,  s.url   AS source_url
            FROM review_items r
            LEFT JOIN documents d ON d.id = r.document_id
            LEFT JOIN sources   s ON s.id = r.source_id
            {where}
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        )
        return build_page(rows_to_dicts(rows), int(total or 0), page, page_size)

    async def create(
        self,
        category: str,
        detail: str,
        document_id: int | None = None,
        source_id: int | None = None,
    ) -> dict[str, Any]:
        if category not in ReviewCategory:
            raise InvalidInputError(f"Unknown review category {category!r}")
        new_id = await self.db.insert(
            "INSERT INTO review_items (category, detail, document_id, source_id)"
            " VALUES (?, ?, ?, ?)",
            (category, detail, document_id, source_id),
        )
        return await self.get(new_id)

    async def get(self, item_id: int) -> dict[str, Any]:
        row = await self.db.fetch_one(
            "SELECT * FROM review_items WHERE id = ?", (item_id,)
        )
        if row is None:
            raise NotFoundError("review item", item_id)
        return row_to_dict(row)  # type: ignore[return-value]

    async def resolve(self, item_id: int, status: str) -> dict[str, Any]:
        """Mark an item resolved or dismissed."""
        if status not in (ReviewStatus.RESOLVED, ReviewStatus.DISMISSED):
            raise InvalidInputError(
                "Review items can only be marked 'resolved' or 'dismissed'."
            )
        result = await self.db.execute(
            "UPDATE review_items SET status = ?, resolved_at = datetime('now')"
            " WHERE id = ?",
            (status, item_id),
        )
        if result.rowcount == 0:
            raise NotFoundError("review item", item_id)
        return await self.get(item_id)

    async def open_count(self) -> int:
        value = await self.db.fetch_value(
            "SELECT COUNT(*) FROM review_items WHERE status = 'open'"
        )
        return int(value or 0)
