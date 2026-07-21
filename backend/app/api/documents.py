"""Document endpoints: browsing, detail, text, the file itself, manual
corrections, tagging, deletion — plus OCR/summarise buttons that answer with
the honest "arrives in Stage 5/6" message until those workers land."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.api.deps import ensure_handler, get_app_config, get_db, get_queue
from app.core.config import AppConfig
from app.core.database import Database
from app.core.errors import NotFoundError
from app.core.queue import TASK_AI_SUMMARIZE, TASK_RUN_OCR, TaskQueue
from app.models.schemas import (
    DocumentPage,
    DocumentTextOut,
    DocumentUpdate,
    TagAssign,
)
from app.repositories.ai import AiRepository
from app.repositories.documents import DocumentsRepository
from app.repositories.downloads import DownloadsRepository
from app.repositories.logs import log_event
from app.repositories.metadata import MetadataRepository
from app.repositories.ocr import OcrRepository
from app.utils.files import delete_library_file, media_type_for, resolve_in_library

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentPage)
async def list_documents(
    authority: str | None = None,
    doc_type: str | None = None,
    file_kind: str | None = None,
    ocr_status: str | None = None,
    status: str | None = None,
    topic: str | None = None,
    source_id: int | None = None,
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    sort: str | None = None,
    order: str | None = None,
    db: Database = Depends(get_db),
) -> Any:
    return await DocumentsRepository(db).list(
        authority=authority, doc_type=doc_type, file_kind=file_kind,
        ocr_status=ocr_status, status=status, topic=topic, source_id=source_id,
        q=q, date_from=date_from, date_to=date_to,
        page=page, page_size=page_size, sort=sort, order=order,
    )


@router.get("/facets")
async def document_facets(db: Database = Depends(get_db)) -> Any:
    """Distinct authorities / doc types / file kinds for the filter dropdowns."""
    return await DocumentsRepository(db).distinct_values()


@router.get("/{document_id}")
async def get_document(document_id: int, db: Database = Depends(get_db)) -> Any:
    """Everything the detail panel needs in one round trip."""
    docs = DocumentsRepository(db)
    doc = await docs.get(document_id)
    doc["metadata"] = await MetadataRepository(db).list_for_document(document_id)
    doc["tags"] = await docs.tags_for(document_id)
    doc["summaries"] = await AiRepository(db).list_for_document(document_id)
    doc["ocr_runs"] = await OcrRepository(db).list_for_document(document_id)
    downloads_page = await DownloadsRepository(db).list(document_id=document_id)
    doc["downloads"] = downloads_page["items"]
    return doc


@router.get("/{document_id}/text", response_model=DocumentTextOut)
async def get_document_text(
    document_id: int, db: Database = Depends(get_db)
) -> Any:
    """Extracted text (native if present, else the latest completed OCR).
    Kept out of the detail response because it can be megabytes."""
    raw = await DocumentsRepository(db).get_text(document_id)
    if raw.get("native_text"):
        kind, text = "native", raw["native_text"]
    elif raw.get("ocr_text"):
        kind, text = "ocr", raw["ocr_text"]
    else:
        kind, text = "none", None
    return DocumentTextOut(
        document_id=document_id, kind=kind, text=text, length=len(text or "")
    )


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: int,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_app_config),
) -> FileResponse:
    doc = await DocumentsRepository(db).get(document_id)
    rel_path = doc.get("rel_path")
    path = resolve_in_library(config.library_root, rel_path) if rel_path else None
    if path is None or not path.is_file():
        raise NotFoundError("file for document", document_id)
    return FileResponse(
        path,
        media_type=media_type_for(path.name),
        filename=doc.get("stored_filename") or path.name,
    )


@router.patch("/{document_id}")
async def update_document(
    document_id: int, body: DocumentUpdate, db: Database = Depends(get_db)
) -> Any:
    """Manual corrections. Audited into document_metadata as extractor=user
    with confidence 1.0, and the search index is refreshed atomically."""
    updated = await DocumentsRepository(db).update_canonical(
        document_id, body.changed_fields()
    )
    await log_event(
        db, "system", "INFO", "Document corrected",
        document_id=document_id, fields=sorted(body.changed_fields()),
    )
    return updated


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    delete_file: bool = Query(default=True),
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_app_config),
) -> None:
    deleted = await DocumentsRepository(db).delete(document_id)
    removed = False
    if delete_file and deleted.get("rel_path"):
        removed = delete_library_file(config.library_root, deleted["rel_path"])
    await log_event(
        db, "system", "INFO", "Document deleted",
        document_id=document_id, file_removed=removed,
    )


@router.post("/{document_id}/tags")
async def add_document_tag(
    document_id: int, body: TagAssign, db: Database = Depends(get_db)
) -> Any:
    """Attach a tag by name (created on first use); returns the new tag list."""
    return await DocumentsRepository(db).add_tag(document_id, body.name)


@router.delete("/{document_id}/tags/{tag_id}")
async def remove_document_tag(
    document_id: int, tag_id: int, db: Database = Depends(get_db)
) -> Any:
    return await DocumentsRepository(db).remove_tag(document_id, tag_id)


@router.post("/{document_id}/ocr", status_code=202)
async def run_ocr(
    document_id: int,
    db: Database = Depends(get_db),
    queue: TaskQueue = Depends(get_queue),
) -> Any:
    """Queue OCR for a scanned document. Real worker arrives in Stage 5."""
    await DocumentsRepository(db).get(document_id)  # 404 first
    ensure_handler(queue, TASK_RUN_OCR)
    task = queue.submit(
        TASK_RUN_OCR,
        {"document_id": document_id},
        dedupe_key=f"ocr-doc:{document_id}",
    )
    return {"job": task.to_dict(), "document_id": document_id}


@router.post("/{document_id}/summarize", status_code=202)
async def summarize(
    document_id: int,
    db: Database = Depends(get_db),
    queue: TaskQueue = Depends(get_queue),
) -> Any:
    """Queue AI summarising. Real worker arrives in Stage 6."""
    await DocumentsRepository(db).get(document_id)
    ensure_handler(queue, TASK_AI_SUMMARIZE)
    task = queue.submit(
        TASK_AI_SUMMARIZE,
        {"document_id": document_id},
        dedupe_key=f"ai-doc:{document_id}",
    )
    return {"job": task.to_dict(), "document_id": document_id}
