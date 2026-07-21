"""Repository for the ``sources`` table (saved research URLs)."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.core.errors import DuplicateError, InvalidInputError, NotFoundError
from app.models.enums import SourceStatus
from app.repositories.base import (
    BaseRepository,
    build_page,
    clamp_paging,
    row_to_dict,
    rows_to_dicts,
    safe_sort,
    where_sql,
)
from app.utils.urls import validate_url

_SORTS = {
    "date_added": "date_added",
    "url": "url COLLATE NOCASE",
    "title": "title COLLATE NOCASE",
    "status": "status",
    "authority": "authority COLLATE NOCASE",
}

_UPDATABLE = ("title", "notes", "authority", "source_type")


class SourcesRepository(BaseRepository):
    # -- create -------------------------------------------------------------

    async def add(
        self, url: str, title: str | None = None, notes: str | None = None
    ) -> dict[str, Any]:
        """Add one source; raises DuplicateError with the existing row if the
        URL is already saved (req. 3: never store duplicates, warn instead)."""
        clean, reason = validate_url(url)
        if clean is None:
            raise InvalidInputError(f"Cannot add this URL: {reason}", {"url": url})
        try:
            new_id = await self.db.insert(
                "INSERT INTO sources (url, title, notes) VALUES (?, ?, ?)",
                (clean, title, notes),
            )
        except sqlite3.IntegrityError:
            existing = await self.get_by_url(clean)
            raise DuplicateError(
                "This URL is already in your sources.", existing
            ) from None
        return await self.get(new_id)

    async def add_many(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Batch add (multi-line paste / CSV import). Never raises for a bad
        row; instead returns per-row buckets the UI can show as a report:
        ``{"added": [...], "duplicates": [...], "invalid": [...]}``."""
        added: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        seen_in_batch: set[str] = set()

        for entry in entries:
            raw = entry.get("url", "")
            clean, reason = validate_url(raw)
            if clean is None:
                invalid.append({"url": raw, "reason": reason})
                continue
            if clean in seen_in_batch:
                duplicates.append({"url": clean, "reason": "Repeated in this batch"})
                continue
            seen_in_batch.add(clean)
            try:
                new_id = await self.db.insert(
                    "INSERT INTO sources (url, title, notes) VALUES (?, ?, ?)",
                    (clean, entry.get("title"), entry.get("notes")),
                )
                added.append(await self.get(new_id))
            except sqlite3.IntegrityError:
                existing = await self.get_by_url(clean)
                duplicates.append(
                    {
                        "url": clean,
                        "reason": "Already saved",
                        "existing_id": existing["id"] if existing else None,
                    }
                )
        return {"added": added, "duplicates": duplicates, "invalid": invalid}

    # -- read ---------------------------------------------------------------

    async def get(self, source_id: int) -> dict[str, Any]:
        row = await self.db.fetch_one("SELECT * FROM sources WHERE id = ?", (source_id,))
        if row is None:
            raise NotFoundError("source", source_id)
        return row_to_dict(row)  # type: ignore[return-value]

    async def get_by_url(self, url: str) -> dict[str, Any] | None:
        row = await self.db.fetch_one("SELECT * FROM sources WHERE url = ?", (url,))
        return row_to_dict(row)

    async def list(
        self,
        status: str | None = None,
        authority: str | None = None,
        q: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        sort: str | None = None,
        order: str | None = None,
    ) -> dict[str, Any]:
        page, page_size, offset = clamp_paging(page, page_size)
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            if status not in SourceStatus:
                raise InvalidInputError(f"Unknown source status {status!r}")
            conditions.append("status = ?")
            params.append(status)
        if authority:
            conditions.append("authority = ? COLLATE NOCASE")
            params.append(authority)
        if q:
            conditions.append("(url LIKE ? OR title LIKE ? OR page_title LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like])
        where = where_sql(conditions)
        total = await self.db.fetch_value(
            f"SELECT COUNT(*) FROM sources {where}", params
        )
        order_by = safe_sort(sort, order, _SORTS, "date_added")
        rows = await self.db.fetch_all(
            f"SELECT * FROM sources {where} {order_by} LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        )
        return build_page(rows_to_dicts(rows), int(total or 0), page, page_size)

    # -- update / delete ----------------------------------------------------

    async def update(self, source_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        updates = {k: v for k, v in fields.items() if k in _UPDATABLE}
        if not updates:
            raise InvalidInputError(
                "Nothing to update — editable fields are: " + ", ".join(_UPDATABLE)
            )
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        result = await self.db.execute(
            f"UPDATE sources SET {set_clause} WHERE id = ?",
            [*updates.values(), source_id],
        )
        if result.rowcount == 0:
            raise NotFoundError("source", source_id)
        return await self.get(source_id)

    async def record_analysis(
        self,
        source_id: int,
        *,
        page_title: str | None,
        authority: str | None,
        source_type: str | None,
        pdf_count: int,
        document_count: int,
        analysis_json: str,
        status: SourceStatus | str,
        error_message: str | None = None,
    ) -> None:
        """Store the analyzer's findings in one write (Stage 4).

        ``authority`` and ``source_type`` only fill blanks — a value the user
        typed is never overwritten by a guess.
        """
        value = str(status)
        if value not in SourceStatus:
            raise InvalidInputError(f"Unknown source status {value!r}")
        result = await self.db.execute(
            "UPDATE sources SET"
            " page_title = ?,"
            " authority = COALESCE(NULLIF(authority, ''), ?),"
            " source_type = COALESCE(NULLIF(source_type, ''), ?),"
            " pdf_count = ?, document_count = ?, analysis_json = ?,"
            " status = ?, error_message = ?, last_checked = datetime('now')"
            " WHERE id = ?",
            (page_title, authority, source_type, pdf_count, document_count,
             analysis_json, value, error_message, source_id),
        )
        if result.rowcount == 0:
            raise NotFoundError("source", source_id)

    async def set_status(
        self,
        source_id: int,
        status: SourceStatus | str,
        error_message: str | None = None,
        touch_last_checked: bool = False,
    ) -> None:
        """Used by the Stage 4 analyzer/downloader to advance the lifecycle."""
        value = str(status)
        if value not in SourceStatus:
            raise InvalidInputError(f"Unknown source status {value!r}")
        sql = "UPDATE sources SET status = ?, error_message = ?"
        if touch_last_checked:
            sql += ", last_checked = datetime('now')"
        sql += " WHERE id = ?"
        result = await self.db.execute(sql, (value, error_message, source_id))
        if result.rowcount == 0:
            raise NotFoundError("source", source_id)

    async def delete(self, source_id: int) -> None:
        """Remove a source. Its documents stay (source_id becomes NULL)."""
        result = await self.db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        if result.rowcount == 0:
            raise NotFoundError("source", source_id)
