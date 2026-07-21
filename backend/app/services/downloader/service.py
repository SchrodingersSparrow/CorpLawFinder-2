"""Document downloading service (Stage 4) — the TASK_DOWNLOAD_FILE handler.

Two payload shapes reach this handler (both existed since Stage 2):

* ``{"source_id": …, "url": <page url>}`` — the Download button / auto
  download: fan out over every file the analyzer found on that source,
  politely (one at a time, with a delay between requests to the same site).
* ``{"download_id": …, "url": <file url>, "source_id": …}`` — one specific
  attempt: the Retry button and startup crash-recovery.

For each file: stream to a temp name while hashing → if the SHA-256 is
already in the library, mark ``skipped_duplicate`` (req. 11) → otherwise
guess metadata (title from the link text, date via the Indian-format parser,
document type from known types), build the stored filename from the user's
naming template (req. 4), file it into the authority folder (req. 5), and
create the document row. Failures land in the Review Queue with the reason.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app.core.database import Database
from app.core.errors import DuplicateError
from app.core.queue import TaskContext, TaskCancelled, TaskQueue
from app.models.enums import DownloadStatus, FileKind, SourceStatus
from app.repositories.documents import DocumentsRepository
from app.repositories.downloads import DownloadsRepository
from app.repositories.logs import log_event
from app.repositories.review import ReviewRepository
from app.repositories.settings import SettingsRepository
from app.repositories.sources import SourcesRepository
from app.services.analysis.parser import guess_doc_type, link_extension
from app.services.analysis.service import AnalysisService
from app.services.downloader import http
from app.utils.dates import extract_date
from app.utils.files import human_size
from app.utils.naming import render_filename, sanitize_component, unique_path

_EXTENSION_KINDS = {
    "pdf": FileKind.PDF, "docx": FileKind.DOCX, "xlsx": FileKind.XLSX,
    "zip": FileKind.ZIP, "html": FileKind.HTML, "htm": FileKind.HTML,
}
_CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/zip": "zip",
    "text/html": "html",
}


class DownloaderService:
    def __init__(
        self,
        db: Database,
        queue: TaskQueue,
        analysis: AnalysisService,
        *,
        library_root: Path | None = None,
        classify: Any = None,  # async (document_id) -> None; Stage 5 text check
    ) -> None:
        self.db = db
        self.queue = queue
        self.analysis = analysis
        self._library_root = library_root
        self._classify = classify

    @property
    def library_root(self) -> Path:
        if self._library_root is None:
            from app.core.config import get_config

            self._library_root = get_config().library_root
        return self._library_root

    # -- queue handler -------------------------------------------------------

    async def handle(self, payload: dict[str, Any], ctx: TaskContext) -> None:
        if "download_id" in payload:
            await self.download_one(
                int(payload["download_id"]),
                str(payload["url"]),
                payload.get("source_id"),
                ctx,
            )
            if payload.get("source_id") is not None:
                await self._rollup_source(int(payload["source_id"]))
        else:
            await self.download_source(int(payload["source_id"]), ctx)

    # -- whole-source flow ---------------------------------------------------

    async def download_source(self, source_id: int, ctx: TaskContext) -> None:
        sources = SourcesRepository(self.db)
        source = await sources.get(source_id)

        links = _links_of(source)
        if not links:
            # Never analysed (or found nothing last time) — analyse first.
            await self.analysis.analyze(source_id, source["url"], ctx)
            source = await sources.get(source_id)
            if source["status"] == str(SourceStatus.FAILED):
                return  # analysis recorded why
            links = _links_of(source)
            if not links:
                return  # analyzer recorded "no downloadable files" on the row

        settings = (await SettingsRepository(self.db).get_all())["values"]
        polite_delay = float(settings.get("download.polite_delay_seconds", 1.5))

        await sources.set_status(source_id, SourceStatus.DOWNLOADING)

        downloads = DownloadsRepository(self.db)
        done_urls = {
            row["url"] for row in await downloads.for_source(source_id)
            if row["status"] in (str(DownloadStatus.SUCCEEDED),
                                 str(DownloadStatus.SKIPPED_DUPLICATE))
        }

        pending = [link for link in links if link["url"] not in done_urls]
        for index, link in enumerate(pending):
            ctx.raise_if_cancelled()
            if index:
                await ctx.sleep(polite_delay)
            download_id = await downloads.create(link["url"], source_id=source_id)
            await self.download_one(
                download_id, link["url"], source_id, ctx,
                source=source, link_text=link.get("text", ""), settings=settings,
            )

        await self._rollup_source(source_id)

    async def _rollup_source(self, source_id: int) -> None:
        """Set the source's final status from its download rows."""
        rows = await DownloadsRepository(self.db).for_source(source_id)
        if any(r["status"] in (str(DownloadStatus.QUEUED), str(DownloadStatus.RUNNING))
               for r in rows):
            return  # someone is still working — a later rollup will finish this
        succeeded = sum(
            1 for r in rows
            if r["status"] in (str(DownloadStatus.SUCCEEDED),
                               str(DownloadStatus.SKIPPED_DUPLICATE))
        )
        failed = sum(1 for r in rows if r["status"] == str(DownloadStatus.FAILED))
        sources = SourcesRepository(self.db)
        if failed == 0:
            await sources.set_status(source_id, SourceStatus.COMPLETED)
        elif succeeded == 0:
            await sources.set_status(
                source_id, SourceStatus.FAILED,
                error_message=f"All {failed} download(s) failed — see the Downloads screen.",
            )
        else:
            await sources.set_status(
                source_id, SourceStatus.COMPLETED,
                error_message=(
                    f"{failed} of {succeeded + failed} files failed — "
                    "see the Downloads screen."
                ),
            )

    # -- one file ------------------------------------------------------------

    async def download_one(
        self,
        download_id: int,
        url: str,
        source_id: int | None,
        ctx: TaskContext,
        *,
        source: dict[str, Any] | None = None,
        link_text: str = "",
        settings: dict[str, Any] | None = None,
    ) -> str:
        downloads = DownloadsRepository(self.db)
        if settings is None:
            settings = (await SettingsRepository(self.db).get_all())["values"]
        if source is None and source_id is not None:
            source = await SourcesRepository(self.db).get(source_id)
        if not link_text and source is not None:
            link_text = _link_text_from_analysis(source, url)

        await downloads.mark(
            download_id, DownloadStatus.RUNNING, started=True, bump_attempts=True
        )

        incoming = self.library_root / ".incoming"
        temp_file = incoming / f"dl-{download_id}.part"
        try:
            outcome = await asyncio.to_thread(
                http.download_to_file,
                url,
                temp_file,
                user_agent=str(settings.get("download.user_agent", "LegalKnowledgeManager/1.0")),
                timeout=float(settings.get("download.timeout_seconds", 120)),
                max_retries=int(settings.get("download.retries", 3)),
                backoff_seconds=float(settings.get("download.retry_backoff_seconds", 5)),
                cancel_check=lambda: ctx.cancelled,
            )
        except http.DownloadCancelled:
            await downloads.mark(
                download_id, DownloadStatus.CANCELLED, error_message="Cancelled by user"
            )
            raise TaskCancelled(f"Download {download_id} cancelled")
        except http.DownloadFailure as error:
            await self._record_failure(download_id, url, source_id, error)
            return str(DownloadStatus.FAILED)

        documents = DocumentsRepository(self.db)
        existing = await documents.get_by_sha256(outcome.sha256)
        if existing is not None:
            temp_file.unlink(missing_ok=True)
            await downloads.mark(
                download_id, DownloadStatus.SKIPPED_DUPLICATE,
                http_status=outcome.http_status, document_id=existing["id"],
                error_message=None,
            )
            await log_event(
                self.db, "download", "INFO",
                f"Skipped duplicate (already saved as "
                f"“{existing.get('stored_filename') or existing['original_filename']}”)",
                source_id=source_id, document_id=existing["id"],
            )
            return str(DownloadStatus.SKIPPED_DUPLICATE)

        document = await self._file_and_register(
            download_id, url, source_id, source, link_text, settings, temp_file, outcome
        )
        await downloads.mark(
            download_id, DownloadStatus.SUCCEEDED,
            http_status=outcome.http_status, document_id=document["id"],
            error_message=None,
        )
        await log_event(
            self.db, "download", "INFO",
            f"Downloaded “{document['stored_filename']}” ({human_size(outcome.size_bytes)})",
            source_id=source_id, document_id=document["id"],
        )
        if self._classify is not None:
            # Stage 5: read the file's text layer and decide searchable vs
            # OCR-required. Never allowed to fail the download itself.
            await self._classify(document["id"])
        return str(DownloadStatus.SUCCEEDED)

    # -- filing --------------------------------------------------------------

    async def _file_and_register(
        self,
        download_id: int,
        url: str,
        source_id: int | None,
        source: dict[str, Any] | None,
        link_text: str,
        settings: dict[str, Any],
        temp_file: Path,
        outcome: http.DownloadOutcome,
    ) -> dict[str, Any]:
        original_filename = (
            outcome.filename_hint
            or unquote(urlparse(outcome.final_url).path.rsplit("/", 1)[-1])
            or f"download-{download_id}"
        )
        extension = (
            (Path(original_filename).suffix.lstrip(".").lower() or None)
            or link_extension(outcome.final_url)
            or _CONTENT_TYPE_EXTENSIONS.get(outcome.content_type)
            or "bin"
        )
        if "." not in original_filename:
            original_filename = f"{original_filename}.{extension}"
        file_kind = str(_EXTENSION_KINDS.get(extension, FileKind.OTHER))

        title = " ".join(link_text.split()) or _title_from_filename(original_filename)
        doc_date = (
            extract_date(link_text)
            or extract_date(original_filename)
            or extract_date(unquote(url))
        )
        authority = (source or {}).get("authority") or None
        doc_type = guess_doc_type(
            f"{title} {original_filename}", list(settings.get("doc_types.known", []))
        )

        stored_filename = render_filename(
            str(settings.get("naming.template", "{authority} - {doc_type} - {title} - {date}")),
            {
                "authority": authority,
                "doc_type": doc_type,
                "title": title,
                "date": _format_date(doc_date, str(settings.get("naming.date_format", "%Y-%m-%d"))),
                "circular_no": None,
            },
            extension=extension,
            max_length=int(settings.get("naming.max_length", 150)),
            unknown=str(settings.get("naming.unknown_placeholder", "Unknown")),
        )

        fallback_folder = str(settings.get("folders.fallback", "Unsorted"))
        if str(settings.get("folders.rule", "authority")) == "authority":
            folder = sanitize_component(authority, fallback=fallback_folder)
        else:
            # Topic folders need topics, which arrive with the AI stage —
            # until then everything files under the fallback folder.
            folder = sanitize_component(fallback_folder, fallback="Unsorted")

        directory = self.library_root / folder
        directory.mkdir(parents=True, exist_ok=True)
        final_path = unique_path(directory, stored_filename)
        temp_file.replace(final_path)

        rel_path = final_path.relative_to(self.library_root).as_posix()
        try:
            return await DocumentsRepository(self.db).create({
                "source_id": source_id,
                "title": title,
                "authority": authority,
                "doc_type": doc_type,
                "doc_date": doc_date,
                "original_filename": original_filename,
                "stored_filename": final_path.name,
                "rel_path": rel_path,
                "file_kind": file_kind,
                "file_size_bytes": outcome.size_bytes,
                "sha256": outcome.sha256,
                "download_url": url,
            })
        except DuplicateError as error:
            # Two attempts raced to the same file — keep the first, drop ours.
            final_path.unlink(missing_ok=True)
            existing = (error.detail or {}).get("existing") or {}
            if existing.get("id"):
                return existing
            raise

    async def _record_failure(
        self,
        download_id: int,
        url: str,
        source_id: int | None,
        error: http.DownloadFailure,
    ) -> None:
        await DownloadsRepository(self.db).mark(
            download_id, DownloadStatus.FAILED,
            http_status=error.http_status, error_message=str(error),
        )
        await ReviewRepository(self.db).create(
            "download_failure", f"{url} — {error}", source_id=source_id
        )
        await log_event(
            self.db, "download", "ERROR", f"Download failed: {url} — {error}",
            source_id=source_id,
        )


# ---------------------------------------------------------------------------


def _links_of(source: dict[str, Any]) -> list[dict[str, str]]:
    try:
        payload = json.loads(source.get("analysis_json") or "{}")
    except (TypeError, ValueError):
        return []
    links = payload.get("links")
    return links if isinstance(links, list) else []


def _link_text_from_analysis(source: dict[str, Any], url: str) -> str:
    for link in _links_of(source):
        if link.get("url") == url:
            return str(link.get("text", ""))
    return ""


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    return " ".join(stem.replace("_", " ").replace("-", " ").split()) or stem


def _format_date(iso_date: str | None, date_format: str) -> str | None:
    if not iso_date:
        return None
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime(date_format)
    except ValueError:
        return iso_date
