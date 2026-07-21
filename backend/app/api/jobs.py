"""Background job endpoints — the live view onto the in-memory task queue.

Durable state lives in the downloads / ocr_results / ai_summaries tables;
this is the "what is happening right now" companion to those histories.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_queue
from app.core.errors import NotFoundError
from app.core.queue import TaskQueue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    active: bool = False, queue: TaskQueue = Depends(get_queue)
) -> list[dict[str, Any]]:
    return queue.snapshot(active_only=active)


@router.get("/{job_id}")
async def get_job(job_id: str, queue: TaskQueue = Depends(get_queue)) -> Any:
    task = queue.get(job_id)
    if task is None:
        raise NotFoundError("job", job_id)
    return task.to_dict()


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, queue: TaskQueue = Depends(get_queue)) -> Any:
    task = queue.cancel(job_id)
    if task is None:
        raise NotFoundError("job", job_id)
    return task.to_dict()
