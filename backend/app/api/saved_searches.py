"""Saved searches (Stage 7): keep a search — query plus active filters —
under a name and recall it with one click from the Search screen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_db
from app.core.database import Database
from app.repositories.saved_searches import SavedSearchesRepository

router = APIRouter(prefix="/saved-searches", tags=["saved-searches"])


class SavedSearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    query: str = Field(min_length=1, max_length=500)
    filters: dict[str, Any] | None = None


@router.get("")
async def list_saved(db: Database = Depends(get_db)) -> Any:
    return await SavedSearchesRepository(db).list()


@router.post("", status_code=201)
async def save(body: SavedSearchIn, db: Database = Depends(get_db)) -> Any:
    """Save the current search; saving under an existing name updates it."""
    return await SavedSearchesRepository(db).save(
        body.name, body.query, body.filters
    )


@router.post("/{search_id}/use")
async def use(search_id: int, db: Database = Depends(get_db)) -> Any:
    """Record a use and return the search so the screen can apply it."""
    return await SavedSearchesRepository(db).touch(search_id)


@router.delete("/{search_id}", status_code=204)
async def delete(search_id: int, db: Database = Depends(get_db)) -> None:
    await SavedSearchesRepository(db).delete(search_id)
