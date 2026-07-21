"""Repository for the ``downloads`` table — every download attempt, ever.

A ``documents`` row is only created on success; failed attempts still leave a
row here so the Review Queue and Logs can show exactly what went wrong.
Stage 2 provides reads plus the durable-status plumbing the Stage 4
downloader will drive (create → running → succeeded/failed, with resume).
"""

from __future__ import annotations

from typing import Any

from app.core.errors import InvalidInputError, NotFoundError
from app.models.enums import DownloadStatus
from app.repositories.base import (
    BaseRepository,
    build_page,
    clamp_paging,
    row_to_dict,
    rows_to_dicts,
)


class DownloadsRepository(BaseRepository):
    async def list(
        self,
        status: str | None = None,
        source_id: int | None = None,
        document_id: int | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        page, page_size, offset = clamp_paging(page, page_size)
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            if status not in DownloadStatus:
                raise InvalidInputError(f"Unknown download status {status!r}")
            conditions.append("dl.status = ?")
            params.append(status)
        if source_id is not None:
            conditions.append("dl.source_id = ?")
            params.append(source_id)
        if document_id is not None:
            conditions.append("dl.document_id = ?")
            params.append(document_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = await self.db.fetch_value(
            f"SELECT COUNT(*) FROM downloads dl {where}", params
        )
        rows = await self.db.fetch_all(
            f"""
            SELECT dl.*, s.url AS source_url, d.stored_filename
            FROM downloads dl
            LEFT JOIN sources s   ON s.id = dl.source_id
            LEFT JOIN documents d ON d.id = dl.document_id
            {where}
            ORDER BY dl.queued_at DESC, dl.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        )
        return build_page(rows_to_dicts(rows), int(total or 0), page, page_size)

    async def for_source(self, source_id: int) -> list[dict[str, Any]]:
        """Every attempt for one source, unpaged (Stage 4 fan-out & rollup —
        the paged ``list`` clamps at 200 rows, which a large page can exceed)."""
        return rows_to_dicts(await self.db.fetch_all(
            "SELECT id, url, status FROM downloads WHERE source_id = ?"
            " ORDER BY id",
            (source_id,),
        ))

    async def get(self, download_id: int) -> dict[str, Any]:
        row = await self.db.fetch_one(
            "SELECT * FROM downloads WHERE id = ?", (download_id,)
        )
        if row is None:
            raise NotFoundError("download", download_id)
        return row_to_dict(row)  # type: ignore[return-value]

    async def create(self, url: str, source_id: int | None = None) -> int:
        """Insert a 'queued' attempt row (durable status for the queue)."""
        return await self.db.insert(
            "INSERT INTO downloads (url, source_id) VALUES (?, ?)", (url, source_id)
        )

    async def mark(
        self,
        download_id: int,
        status: str,
        *,
        http_status: int | None = None,
        error_message: str | None = None,
        document_id: int | None = None,
        bump_attempts: bool = False,
        started: bool = False,
    ) -> None:
        if status not in DownloadStatus:
            raise InvalidInputError(f"Unknown download status {status!r}")
        finished = status in (
            DownloadStatus.SUCCEEDED,
            DownloadStatus.FAILED,
            DownloadStatus.SKIPPED_DUPLICATE,
            DownloadStatus.CANCELLED,
        )
        sql = (
            "UPDATE downloads SET status = ?,"
            " http_status = COALESCE(?, http_status),"
            " error_message = ?,"
            " document_id = COALESCE(?, document_id)"
        )
        if bump_attempts:
            sql += ", attempts = attempts + 1"
        if started:
            sql += ", started_at = datetime('now')"
        if finished:
            sql += ", finished_at = datetime('now')"
        sql += " WHERE id = ?"
        result = await self.db.execute(
            sql, (status, http_status, error_message, document_id, download_id)
        )
        if result.rowcount == 0:
            raise NotFoundError("download", download_id)

    async def find_resumable(self) -> list[dict[str, Any]]:
        """Attempts left 'queued'/'running' by a previous backend process."""
        rows = await self.db.fetch_all(
            "SELECT id, url, source_id FROM downloads"
            " WHERE status IN ('queued','running') ORDER BY id"
        )
        return rows_to_dicts(rows)

    async def counts_by_status(self) -> dict[str, int]:
        rows = await self.db.fetch_all(
            "SELECT status, COUNT(*) AS n FROM downloads GROUP BY status"
        )
        return {row["status"]: row["n"] for row in rows}
