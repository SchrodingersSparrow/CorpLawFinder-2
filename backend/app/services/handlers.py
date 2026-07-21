"""Wire real background-work handlers into the task queue.

Called once from the app lifespan, after the queue starts and **before**
startup resume runs (so interrupted downloads found by
:mod:`app.core.resume` actually have a handler to land on). As later stages
add OCR and AI handlers they register here too; endpoints for anything not
yet registered keep answering with their honest "arrives in Stage N" message.
"""

from __future__ import annotations

from app.core.database import Database
from app.core.queue import (
    TASK_AI_SUMMARIZE,
    TASK_ANALYZE_SOURCE,
    TASK_DOWNLOAD_FILE,
    TASK_RUN_OCR,
    TaskQueue,
)
from app.services.ai.service import AiService
from app.services.analysis.service import AnalysisService
from app.services.downloader.service import DownloaderService
from app.services.ocr.service import OcrService


def register_all(db: Database, queue: TaskQueue) -> None:
    analysis = AnalysisService(db, queue)
    ai = AiService(db, queue)
    ocr = OcrService(db, queue, summarize=ai.queue_run)
    downloader = DownloaderService(
        db, queue, analysis, classify=ocr.classify_document
    )
    queue.register(TASK_ANALYZE_SOURCE, analysis.handle)
    queue.register(TASK_DOWNLOAD_FILE, downloader.handle)
    queue.register(TASK_RUN_OCR, ocr.handle)
    queue.register(TASK_AI_SUMMARIZE, ai.handle)
