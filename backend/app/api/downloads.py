"""Download history endpoints. The actual downloader arrives in Stage 4;
this screen already works because rows are the durable source of truth."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import ensure_handler, get_db, get_queue
from app.core.database import Database
from app.core.queue import TASK_DOWNLOAD_FILE, TaskQueue
from app.models.enums import DownloadStatus
from app.models.schemas import DownloadPage
from app.repositories.downloads import DownloadsRepository

router = APIRouter(prefix="/downloads", tags=["downloads"])


@router.get("", response_model=DownloadPage)
async def list_downloads(
    status: str | None = None,
    source_id: int | None = None,
    document_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Database = Depends(get_db),
) -> Any:
    return await DownloadsRepository(db).list(
        status=status, source_id=source_id, document_id=document_id,
        page=page, page_size=page_size,
    )


@router.get("/counts")
async def download_counts(db: Database = Depends(get_db)) -> dict[str, int]:
    return await DownloadsRepository(db).counts_by_status()


@router.post("/{download_id}/retry", status_code=202)
async def retry_download(
    download_id: int,
    db: Database = Depends(get_db),
    queue: TaskQueue = Depends(get_queue),
) -> Any:
    """Re-queue a failed download. Real worker arrives in Stage 4."""
    repo = DownloadsRepository(db)
    row = await repo.get(download_id)  # 404 first
    ensure_handler(queue, TASK_DOWNLOAD_FILE)
    await repo.mark(download_id, DownloadStatus.QUEUED)
    task = queue.submit(
        TASK_DOWNLOAD_FILE,
        {"download_id": download_id, "url": row["url"], "source_id": row["source_id"]},
        dedupe_key=f"download:{download_id}",
    )
    return {"job": task.to_dict(), "download_id": download_id}
