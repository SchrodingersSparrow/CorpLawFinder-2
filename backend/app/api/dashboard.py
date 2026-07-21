"""Dashboard endpoint: one call returns everything the home screen shows."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_db, get_queue
from app.core.database import Database
from app.core.queue import TaskQueue
from app.models.schemas import DashboardOut
from app.repositories.dashboard import DashboardRepository
from app.repositories.downloads import DownloadsRepository

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    db: Database = Depends(get_db), queue: TaskQueue = Depends(get_queue)
) -> DashboardOut:
    repo = DashboardRepository(db)
    return DashboardOut(
        counts=await repo.counts(),
        recent_documents=await repo.recent_documents(limit=8),
        recent_sources=await repo.recent_sources(limit=5),
        active_jobs=queue.snapshot(active_only=True),
        download_counts=await DownloadsRepository(db).counts_by_status(),
    )
