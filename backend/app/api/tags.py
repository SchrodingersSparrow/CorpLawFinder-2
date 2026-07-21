"""Tag management. Deleting a tag also refreshes the search index of every
document that carried it, so search never shows stale topic matches."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.core.database import Database
from app.models.schemas import TagCreate, TagOut
from app.repositories.search import rebuild_index_sync
from app.repositories.tags import TagsRepository

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
async def list_tags(
    kind: str | None = None, db: Database = Depends(get_db)
) -> Any:
    return await TagsRepository(db).list(kind=kind)


@router.post("", status_code=201, response_model=TagOut)
async def create_tag(body: TagCreate, db: Database = Depends(get_db)) -> Any:
    return await TagsRepository(db).create(body.name, body.kind)


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(tag_id: int, db: Database = Depends(get_db)) -> None:
    repo = TagsRepository(db)
    await repo.get(tag_id)  # 404 if unknown
    doc_ids = await repo.document_ids_with_tag(tag_id)

    def job(conn) -> None:
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        for doc_id in doc_ids:
            rebuild_index_sync(conn, doc_id)

    await db.run(job)
