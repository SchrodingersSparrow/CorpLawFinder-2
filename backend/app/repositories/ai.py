"""Repository for ``ai_summaries`` (local-LLM output, req. 8).

Stage 2 provides reads plus the durable-status plumbing the Stage 6 Ollama
service will drive.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.core.errors import InvalidInputError, NotFoundError
from app.models.enums import JobStatus
from app.repositories.base import BaseRepository, loads, row_to_dict, rows_to_dicts


def latest_summary_sync(
    conn: sqlite3.Connection, document_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM ai_summaries
        WHERE document_id = ? AND status = 'completed'
        ORDER BY finished_at DESC, id DESC LIMIT 1
        """,
        (document_id,),
    ).fetchone()
    return None if row is None else dict(row)


def _decode(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row["topics"] = loads(row.pop("topics_json", None), [])
    row["keywords"] = loads(row.pop("keywords_json", None), [])
    return row


class AiRepository(BaseRepository):
    async def latest_completed(self, document_id: int) -> dict[str, Any] | None:
        row = await self.db.run(lambda conn: latest_summary_sync(conn, document_id))
        return _decode(row)

    async def list_for_document(self, document_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT * FROM ai_summaries WHERE document_id = ?"
            " ORDER BY created_at DESC, id DESC",
            (document_id,),
        )
        return [_decode(r) for r in rows_to_dicts(rows)]  # type: ignore[misc]

    async def create_run(self, document_id: int, model: str) -> int:
        return await self.db.insert(
            "INSERT INTO ai_summaries (document_id, model) VALUES (?, ?)",
            (document_id, model),
        )

    async def mark(
        self,
        run_id: int,
        status: str,
        *,
        one_line_summary: str | None = None,
        detailed_summary: str | None = None,
        topics_json: str | None = None,
        keywords_json: str | None = None,
        authority: str | None = None,
        confidence: float | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in JobStatus:
            raise InvalidInputError(f"Unknown AI run status {status!r}")
        finished = status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        result = await self.db.execute(
            f"""
            UPDATE ai_summaries SET status = ?,
                one_line_summary = COALESCE(?, one_line_summary),
                detailed_summary = COALESCE(?, detailed_summary),
                topics_json     = COALESCE(?, topics_json),
                keywords_json   = COALESCE(?, keywords_json),
                authority       = COALESCE(?, authority),
                confidence      = COALESCE(?, confidence),
                error_message   = ?
                {", finished_at = datetime('now')" if finished else ""}
            WHERE id = ?
            """,
            (status, one_line_summary, detailed_summary, topics_json, keywords_json,
             authority, confidence, error_message, run_id),
        )
        if result.rowcount == 0:
            raise NotFoundError("AI run", run_id)

    async def find_resumable(self) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT id, document_id, model FROM ai_summaries"
            " WHERE status IN ('queued','running') ORDER BY id"
        )
        return rows_to_dicts(rows)

    async def get(self, run_id: int) -> dict[str, Any]:
        row = await self.db.fetch_one(
            "SELECT * FROM ai_summaries WHERE id = ?", (run_id,)
        )
        if row is None:
            raise NotFoundError("AI run", run_id)
        return _decode(row_to_dict(row))  # type: ignore[return-value]
