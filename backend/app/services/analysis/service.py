"""Website analysis service (Stage 4) — the TASK_ANALYZE_SOURCE handler.

Flow for one source:

1. Fetch the page with a plain HTTP request (enough for RBI and SEBI).
2. If the URL turns out to BE a document (a pasted direct PDF link), record
   it as its own single download link — no page to parse.
3. If the page is a JavaScript shell (the MCA V3 portal), re-fetch with the
   headless browser — or, when Playwright isn't installed yet, fail with the
   exact install commands.
4. If the page is behind a login (common on MCA now), stop with an honest
   explanation and a Review Queue entry — the app reads public pages only.
5. Otherwise: collect document links, guess authority and document type,
   store everything on the source row, and (if enabled) queue the downloads.

The service takes injectable fetch functions so tests can drive every path
without a network or a browser.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.database import Database
from app.core.queue import TASK_DOWNLOAD_FILE, TaskContext, TaskQueue
from app.models.enums import SourceStatus
from app.repositories.logs import log_event
from app.repositories.review import ReviewRepository
from app.repositories.settings import SettingsRepository
from app.repositories.sources import SourcesRepository
from app.services.analysis import fetcher, parser

StaticFetch = Callable[..., fetcher.FetchResult]
BrowserFetch = Callable[..., Awaitable[fetcher.FetchResult]]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class AnalysisService:
    def __init__(
        self,
        db: Database,
        queue: TaskQueue,
        *,
        fetch_static: StaticFetch = fetcher.fetch_static,
        fetch_with_browser: BrowserFetch = fetcher.fetch_with_browser,
    ) -> None:
        self.db = db
        self.queue = queue
        self._fetch_static = fetch_static
        self._fetch_browser = fetch_with_browser

    # -- queue handler -------------------------------------------------------

    async def handle(self, payload: dict[str, Any], ctx: TaskContext) -> None:
        await self.analyze(int(payload["source_id"]), str(payload["url"]), ctx)

    # -- the work ------------------------------------------------------------

    async def analyze(self, source_id: int, url: str, ctx: TaskContext) -> None:
        sources = SourcesRepository(db=self.db)
        settings = (await SettingsRepository(self.db).get_all())["values"]
        user_agent = str(settings.get("download.user_agent", "LegalKnowledgeManager/1.0"))
        timeout = float(settings.get("download.timeout_seconds", 120))
        use_browser = str(settings.get("analysis.use_browser", "auto"))

        await sources.set_status(source_id, SourceStatus.ANALYZING)
        ctx.raise_if_cancelled()

        try:
            result = await asyncio.to_thread(
                self._fetch_static, url, timeout=min(timeout, 60), user_agent=user_agent
            )
        except fetcher.FetchFailure as error:
            await self._fail(source_id, str(error))
            return

        ctx.raise_if_cancelled()

        # A pasted direct file link is its own single "document found".
        if result.file_kind:
            await self._record(
                source_id, url,
                page_title=_filename_of(result.url),
                page=parser.ParsedPage(),
                links=[{
                    "url": result.url,
                    "text": _filename_of(result.url),
                    "kind": result.file_kind,
                }],
                used_browser=False,
                settings=settings,
            )
            return

        page = parser.parse_page(result.text)

        if use_browser == "always" or (
            use_browser != "never" and parser.needs_browser(result.text, page)
        ):
            try:
                result = await self._fetch_browser(
                    url, timeout=min(timeout, 90), user_agent=user_agent
                )
                page = parser.parse_page(result.text)
            except fetcher.BrowserUnavailable as error:
                if not parser.find_document_links(result.url, page):
                    await self._fail(source_id, str(error), review=True)
                    return
                # Static parse already found real files — good enough, carry on.
            except fetcher.FetchFailure as error:
                await self._fail(source_id, str(error))
                return

        ctx.raise_if_cancelled()

        login_reason = parser.detect_login_wall(result.url, page, result.http_status)
        if login_reason:
            await self._fail(source_id, login_reason, review=True)
            return

        if result.http_status >= 400:
            await self._fail(
                source_id, f"The site answered HTTP {result.http_status} for this page."
            )
            return

        links = parser.find_document_links(
            result.url, page, _discovery_extensions(settings)
        )
        await self._record(
            source_id, url,
            page_title=page.title,
            page=page,
            links=links,
            used_browser=result.used_browser,
            settings=settings,
        )

    # -- outcomes ------------------------------------------------------------

    async def _record(
        self,
        source_id: int,
        url: str,
        *,
        page_title: str,
        page: parser.ParsedPage,
        links: list[dict[str, str]],
        used_browser: bool,
        settings: dict[str, Any],
    ) -> None:
        known_authorities = list(settings.get("authorities.known", []))
        known_types = list(settings.get("doc_types.known", []))
        authority = parser.guess_authority(url, page_title, known_authorities)
        source_type = parser.guess_doc_type(page_title, known_types)
        pdf_count = sum(1 for link in links if link["kind"] == "pdf")

        await SourcesRepository(self.db).record_analysis(
            source_id,
            page_title=(page_title or None) and page_title[:300],
            authority=authority,
            source_type=source_type,
            pdf_count=pdf_count,
            document_count=len(links),
            analysis_json=json.dumps({
                "analyzed_at": _now(),
                "final_url": url,
                "used_browser": used_browser,
                "links": links,
            }),
            status=SourceStatus.ANALYZED,
            error_message=None if links else "No downloadable files were found on this page.",
        )
        await log_event(
            self.db, "analysis", "INFO",
            f"Analysed page: {len(links)} file(s) found ({pdf_count} PDF)",
            source_id=source_id,
        )

        if (
            links
            and bool(settings.get("analysis.auto_download", True))
            and self.queue.has_handler(TASK_DOWNLOAD_FILE)
        ):
            self.queue.submit(
                TASK_DOWNLOAD_FILE,
                {"source_id": source_id, "url": url},
                dedupe_key=f"download-source:{source_id}",
            )

    async def _fail(self, source_id: int, message: str, review: bool = False) -> None:
        await SourcesRepository(self.db).set_status(
            source_id, SourceStatus.FAILED, error_message=message,
            touch_last_checked=True,
        )
        await log_event(
            self.db, "analysis", "ERROR", f"Analysis failed: {message}",
            source_id=source_id,
        )
        if review:
            await ReviewRepository(self.db).create(
                "other", message, source_id=source_id
            )


def _filename_of(url: str) -> str:
    from urllib.parse import unquote, urlparse

    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return name or url


def _discovery_extensions(settings: dict[str, Any]) -> tuple[str, ...]:
    allowed = settings.get("download.allowed_extensions") or []
    file_only = tuple(
        str(ext).lower().lstrip(".") for ext in allowed
        if str(ext).lower().lstrip(".") not in ("html", "htm")
    )
    return file_only or parser.DISCOVERY_EXTENSIONS
