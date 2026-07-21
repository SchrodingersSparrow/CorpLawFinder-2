"""Activity log endpoints (requirement 14: every action visible)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.core.database import Database
from app.models.schemas import LogsResponse
from app.repositories.logs import LogsRepository

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("", response_model=LogsResponse)
async def list_logs(
    category: str | None = None,
    level: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    db: Database = Depends(get_db),
) -> Any:
    """Newest first. Pass the returned ``next_before_id`` back in to page
    further into history (cursor pagination stays fast at any table size)."""
    return await LogsRepository(db).list(
        category=category, level=level, limit=limit, before_id=before_id
    )
