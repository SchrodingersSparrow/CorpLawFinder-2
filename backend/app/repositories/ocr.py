"""Repository for ``ocr_results`` (one row per OCR run, req. 6).

Stage 2 provides reads plus the durable-status plumbing (create / mark /
resume-scan) that the Stage 5 OCR service will drive.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.core.errors import InvalidInputError, NotFoundError
from app.models.enums import JobStatus, OcrEngine
from app.repositories.base import BaseRepository, row_to_dict, rows_to_dicts


def latest_ocr_text_sync(conn: sqlite3.Connection, document_id: int) -> str | None:
    """Newest completed OCR text for a document (search-index ingredient)."""
    row = conn.execute(
        """
        SELECT text_content FROM ocr_results
        WHERE document_id = ? AND status = 'completed' AND text_content IS NOT NULL
        ORDER BY finished_at DESC, id DESC LIMIT 1
        """,
        (document_id,),
    ).fetchone()
    return None if row is None else row["text_content"]


class OcrRepository(BaseRepository):
    async def list_for_document(self, document_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            """
            SELECT id, engine, status, avg_confidence, page_count,
                   duration_seconds, error_message, created_at, finished_at,
                   length(text_content) AS text_length
            FROM ocr_results WHERE document_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (document_id,),
        )
        return rows_to_dicts(rows)

    async def latest_completed_text(self, document_id: int) -> str | None:
        return await self.db.run(
            lambda conn: latest_ocr_text_sync(conn, document_id)
        )

    async def create_run(self, document_id: int, engine: str) -> int:
        """Insert a 'queued' run row (durable status the queue resumes from)."""
        if engine not in OcrEngine:
            raise InvalidInputError(f"Unknown OCR engine {engine!r}")
        return await self.db.insert(
            "INSERT INTO ocr_results (document_id, engine) VALUES (?, ?)",
            (document_id, engine),
        )

    async def mark(
        self,
        run_id: int,
        status: str,
        *,
        text_content: str | None = None,
        avg_confidence: float | None = None,
        page_count: int | None = None,
        duration_seconds: float | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in JobStatus:
            raise InvalidInputError(f"Unknown OCR status {status!r}")
        finished = status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        result = await self.db.execute(
            f"""
            UPDATE ocr_results SET status = ?,
                text_content = COALESCE(?, text_content),
                avg_confidence = COALESCE(?, avg_confidence),
                page_count = COALESCE(?, page_count),
                duration_seconds = COALESCE(?, duration_seconds),
                error_message = ?
                {", finished_at = datetime('now')" if finished else ""}
            WHERE id = ?
            """,
            (status, text_content, avg_confidence, page_count,
             duration_seconds, error_message, run_id),
        )
        if result.rowcount == 0:
            raise NotFoundError("OCR run", run_id)

    async def find_resumable(self) -> list[dict[str, Any]]:
        """Runs left 'queued'/'running' by a previous backend process."""
        rows = await self.db.fetch_all(
            "SELECT id, document_id, engine FROM ocr_results"
            " WHERE status IN ('queued','running') ORDER BY id"
        )
        return rows_to_dicts(rows)

    async def get(self, run_id: int) -> dict[str, Any]:
        row = await self.db.fetch_one(
            "SELECT * FROM ocr_results WHERE id = ?", (run_id,)
        )
        if row is None:
            raise NotFoundError("OCR run", run_id)
        return row_to_dict(row)  # type: ignore[return-value]
