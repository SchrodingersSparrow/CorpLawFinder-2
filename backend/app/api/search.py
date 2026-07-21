"""Full-text search across titles, extracted text, OCR text, summaries and
tags (FTS5 + bm25 ranking), with the same filters as the Documents screen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.core.database import Database
from app.models.schemas import SearchResponse
from app.repositories.search import SearchRepository
from app.repositories.settings import SettingsRepository

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=1, max_length=500),
    authority: str | None = None,
    doc_type: str | None = None,
    file_kind: str | None = None,
    topic: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    sort: str = Query(default="relevance", pattern="^(relevance|newest|oldest)$"),
    db: Database = Depends(get_db),
) -> Any:
    snippet_tokens = await SettingsRepository(db).get_value("search.snippet_tokens")
    return await SearchRepository(db).search(
        q,
        authority=authority, doc_type=doc_type, file_kind=file_kind,
        topic=topic, date_from=date_from, date_to=date_to,
        page=page, page_size=page_size,
        snippet_tokens=int(snippet_tokens or 40),
        sort=sort,
    )


@router.post("/search/rebuild")
async def rebuild_search_index(db: Database = Depends(get_db)) -> dict[str, int]:
    """Maintenance: re-derive the whole index from the documents table."""
    count = await SearchRepository(db).rebuild_all()
    return {"documents_indexed": count}
