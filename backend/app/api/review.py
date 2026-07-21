"""Review queue endpoints (requirement 14: nothing fails silently —
everything that needs a human lands here)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.core.database import Database
from app.models.schemas import ReviewPage, ReviewResolve
from app.repositories.review import ReviewRepository

router = APIRouter(prefix="/review", tags=["review"])


@router.get("", response_model=ReviewPage)
async def list_review_items(
    status: str | None = "open",
    category: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Database = Depends(get_db),
) -> Any:
    # status="" or status=all → no status filter (show everything)
    effective = None if status in ("", "all", None) else status
    return await ReviewRepository(db).list(
        status=effective, category=category, page=page, page_size=page_size
    )


@router.post("/{item_id}/resolve")
async def resolve_review_item(
    item_id: int, body: ReviewResolve, db: Database = Depends(get_db)
) -> Any:
    return await ReviewRepository(db).resolve(item_id, body.status)
