"""Startup recovery: re-queue work interrupted by a crash or shutdown.

The queue is in-memory (docs/ARCHITECTURE.md); durability lives in the status
columns of ``downloads``, ``ocr_results`` and ``ai_summaries``. On every
backend start this scan finds rows still marked ``queued``/``running`` and
puts them back on the queue — IF a handler for that work is registered.
Before Stages 4-6 install their handlers the scan simply reports what it
found and leaves the rows untouched, so nothing is ever lost or corrupted by
running a newer database against an older stage.
"""

from __future__ import annotations

from app.core.database import Database
from app.core.logging_config import get_logger
from app.core.queue import (
    TASK_AI_SUMMARIZE,
    TASK_DOWNLOAD_FILE,
    TASK_RUN_OCR,
    TaskQueue,
)
from app.repositories.ai import AiRepository
from app.repositories.downloads import DownloadsRepository
from app.repositories.ocr import OcrRepository

logger = get_logger("system")


async def requeue_pending(db: Database, queue: TaskQueue) -> dict[str, int]:
    """Scan durable statuses and resubmit resumable work. Returns counts."""
    resumed = {"downloads": 0, "ocr": 0, "ai": 0, "skipped": 0}

    for row in await DownloadsRepository(db).find_resumable():
        if queue.has_handler(TASK_DOWNLOAD_FILE):
            queue.submit(
                TASK_DOWNLOAD_FILE,
                {"download_id": row["id"], "url": row["url"],
                 "source_id": row["source_id"]},
                dedupe_key=f"download:{row['id']}",
            )
            resumed["downloads"] += 1
        else:
            resumed["skipped"] += 1

    for row in await OcrRepository(db).find_resumable():
        if queue.has_handler(TASK_RUN_OCR):
            queue.submit(
                TASK_RUN_OCR,
                {"ocr_id": row["id"], "document_id": row["document_id"],
                 "engine": row["engine"]},
                dedupe_key=f"ocr:{row['id']}",
            )
            resumed["ocr"] += 1
        else:
            resumed["skipped"] += 1

    for row in await AiRepository(db).find_resumable():
        if queue.has_handler(TASK_AI_SUMMARIZE):
            queue.submit(
                TASK_AI_SUMMARIZE,
                {"summary_id": row["id"], "document_id": row["document_id"],
                 "model": row["model"]},
                dedupe_key=f"ai:{row['id']}",
            )
            resumed["ai"] += 1
        else:
            resumed["skipped"] += 1

    total = resumed["downloads"] + resumed["ocr"] + resumed["ai"]
    if total:
        logger.info("Resumed %d interrupted job(s) from a previous run", total)
    if resumed["skipped"]:
        logger.info(
            "%d interrupted job(s) are waiting for a later stage's worker "
            "to be installed; they were left untouched",
            resumed["skipped"],
        )
    return resumed
