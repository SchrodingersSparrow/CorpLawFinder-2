"""Source (URL) management endpoints.

Covers requirement 1 (add URLs one at a time, in bulk, or via CSV upload)
plus the analyze/download buttons, which are fully wired but answer with the
honest "arrives in Stage 4" message until the Playwright worker lands.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Query, UploadFile

from app.api.deps import ensure_handler, get_db, get_queue
from app.core.database import Database
from app.core.errors import InvalidInputError
from app.core.queue import TASK_ANALYZE_SOURCE, TASK_DOWNLOAD_FILE, TaskQueue
from app.models.enums import SourceStatus
from app.models.schemas import (
    BatchAddResult,
    SourceBatchCreate,
    SourceCreate,
    SourcePage,
    SourceUpdate,
)
from app.repositories.logs import log_event
from app.repositories.sources import SourcesRepository
from app.utils.urls import validate_url

router = APIRouter(prefix="/sources", tags=["sources"])

_CSV_MAX_BYTES = 5 * 1024 * 1024  # 5 MB is thousands of URLs; anything bigger
# is almost certainly the wrong file picked by mistake.


async def _apply_extras(
    repo: SourcesRepository, row: dict[str, Any], extras: dict[str, Any]
) -> dict[str, Any]:
    """add() stores url/title/notes; authority & source_type ride via update()."""
    fields = {k: v for k, v in extras.items() if v is not None}
    if fields:
        return await repo.update(row["id"], fields)
    return row


@router.post("", status_code=201)
async def add_source(
    body: SourceCreate, db: Database = Depends(get_db)
) -> dict[str, Any]:
    repo = SourcesRepository(db)
    row = await repo.add(body.url, title=body.title, notes=body.notes)
    row = await _apply_extras(
        repo, row, {"authority": body.authority, "source_type": body.source_type}
    )
    await log_event(db, "system", "INFO", "Source added", url=row["url"])
    return row


@router.post("/batch", response_model=BatchAddResult)
async def add_sources_batch(
    body: SourceBatchCreate, db: Database = Depends(get_db)
) -> Any:
    repo = SourcesRepository(db)
    result = await repo.add_many([s.model_dump() for s in body.sources])
    # Apply authority/source_type per added row, matched by the CLEANED URL
    # (positional zip would drift as soon as one row was skipped, and the
    # repository normalises URLs, e.g. adds https://).
    by_url: dict[str, SourceCreate] = {}
    for s in body.sources:
        clean, _ = validate_url(s.url)
        if clean:
            by_url.setdefault(clean, s)
    patched: list[dict[str, Any]] = []
    for row in result["added"]:
        src = by_url.get(row["url"])
        if src is not None:
            row = await _apply_extras(
                repo, row, {"authority": src.authority, "source_type": src.source_type}
            )
        patched.append(row)
    result["added"] = patched
    await log_event(
        db,
        "system",
        "INFO",
        "Batch source add",
        added=len(result["added"]),
        duplicates=len(result["duplicates"]),
        invalid=len(result["invalid"]),
    )
    return result


@router.post("/import-csv", response_model=BatchAddResult)
async def import_csv(
    file: UploadFile, db: Database = Depends(get_db)
) -> Any:
    """CSV upload. First column is the URL; optional ``title``/``notes``
    columns are honoured when a header row is present."""
    raw = await file.read()
    if len(raw) > _CSV_MAX_BYTES:
        raise InvalidInputError(
            "That file is larger than 5 MB. A URL list should be far smaller — "
            "please check the right file was selected."
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise InvalidInputError("The CSV file appears to be empty.")

    header = [c.strip().lower() for c in rows[0]]
    entries: list[dict[str, Any]] = []
    if "url" in header:
        url_i = header.index("url")
        title_i = header.index("title") if "title" in header else None
        notes_i = header.index("notes") if "notes" in header else None
        data_rows = rows[1:]
    else:
        url_i, title_i, notes_i = 0, None, None
        data_rows = rows
    for r in data_rows:
        if url_i >= len(r):
            continue
        entries.append(
            {
                "url": r[url_i].strip(),
                "title": r[title_i].strip() if title_i is not None and title_i < len(r) and r[title_i].strip() else None,
                "notes": r[notes_i].strip() if notes_i is not None and notes_i < len(r) and r[notes_i].strip() else None,
            }
        )
    if not entries:
        raise InvalidInputError("No URLs were found in the CSV file.")

    result = await SourcesRepository(db).add_many(entries)
    await log_event(
        db,
        "system",
        "INFO",
        "CSV import",
        filename=file.filename,
        added=len(result["added"]),
        duplicates=len(result["duplicates"]),
        invalid=len(result["invalid"]),
    )
    return result


@router.get("", response_model=SourcePage)
async def list_sources(
    status: str | None = None,
    authority: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    sort: str | None = None,
    order: str | None = None,
    db: Database = Depends(get_db),
) -> Any:
    return await SourcesRepository(db).list(
        status=status, authority=authority, q=q,
        page=page, page_size=page_size, sort=sort, order=order,
    )


@router.get("/{source_id}")
async def get_source(source_id: int, db: Database = Depends(get_db)) -> Any:
    return await SourcesRepository(db).get(source_id)


@router.patch("/{source_id}")
async def update_source(
    source_id: int, body: SourceUpdate, db: Database = Depends(get_db)
) -> Any:
    return await SourcesRepository(db).update(source_id, body.changed_fields())


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: int, db: Database = Depends(get_db)) -> None:
    await SourcesRepository(db).delete(source_id)
    await log_event(db, "system", "INFO", "Source deleted", source_id=source_id)


@router.post("/{source_id}/analyze", status_code=202)
async def analyze_source(
    source_id: int,
    db: Database = Depends(get_db),
    queue: TaskQueue = Depends(get_queue),
) -> Any:
    """Queue website analysis. Real worker arrives in Stage 4."""
    repo = SourcesRepository(db)
    row = await repo.get(source_id)  # 404 before the feature check
    ensure_handler(queue, TASK_ANALYZE_SOURCE)
    task = queue.submit(
        TASK_ANALYZE_SOURCE,
        {"source_id": source_id, "url": row["url"]},
        dedupe_key=f"analyze:{source_id}",
    )
    await repo.set_status(source_id, SourceStatus.ANALYZING)
    return {"job": task.to_dict(), "source_id": source_id}


@router.post("/{source_id}/download", status_code=202)
async def download_source(
    source_id: int,
    db: Database = Depends(get_db),
    queue: TaskQueue = Depends(get_queue),
) -> Any:
    """Queue downloading of a source's documents. Real worker arrives in Stage 4."""
    repo = SourcesRepository(db)
    row = await repo.get(source_id)
    ensure_handler(queue, TASK_DOWNLOAD_FILE)
    task = queue.submit(
        TASK_DOWNLOAD_FILE,
        {"source_id": source_id, "url": row["url"]},
        dedupe_key=f"download-source:{source_id}",
    )
    await repo.set_status(source_id, SourceStatus.DOWNLOADING)
    return {"job": task.to_dict(), "source_id": source_id}
