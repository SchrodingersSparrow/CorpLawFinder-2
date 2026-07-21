"""OCR service (Stage 5) — classification plus the TASK_RUN_OCR handler.

Two jobs:

**Classify** (runs right after every download, and on demand): extract the
file's native text — PDFs via pdfplumber/pypdf, Word/Excel/HTML with the
standard library. Enough text per page → the document is *searchable* and
its text goes straight into the search index. A scanned PDF → *OCR
required*, and (by default) an OCR run is queued automatically.

**OCR** (the queue handler): render each PDF page to an image and read it
with the configured engine — PaddleOCR when installed, Tesseract otherwise.
The engine is chosen by actual availability at queue time, so a machine
without PaddleOCR quietly uses Tesseract. Results land in ``ocr_results``;
the search index picks the text up on rebuild (the document's own
``text_content`` stays reserved for native text). Cancellation is honoured
between pages; failures carry install hints and file a Review Queue entry.

The handler accepts both payload shapes that exist in the wild:
``{"document_id": …}`` from the Run OCR button (a run row is created here)
and ``{"ocr_id": …, "document_id": …, "engine": …}`` from startup resume.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

from app.core.database import Database
from app.core.queue import TASK_RUN_OCR, TaskCancelled, TaskContext, TaskQueue
from app.models.enums import FileKind, JobStatus, OcrEngine, OcrStatus
from app.repositories.documents import DocumentsRepository
from app.repositories.logs import log_event
from app.repositories.ocr import OcrRepository
from app.repositories.review import ReviewRepository
from app.repositories.settings import SettingsRepository
from app.utils.files import resolve_in_library
from app.services.ocr import engines, extract


class OcrService:
    def __init__(
        self,
        db: Database,
        queue: TaskQueue,
        *,
        library_root: Path | None = None,
        extract_pdf: Callable[[Path], tuple[str, int]] = extract.extract_pdf_text,
        render_pages: Callable[..., Any] = engines.render_pdf_pages,
        page_readers: dict[str, Callable[..., tuple[str, float | None]]] | None = None,
        engine_available: Callable[[str, dict[str, Any]], bool] | None = None,
        summarize: Any = None,  # async (document_id) -> None; Stage 6 AI hook
    ) -> None:
        self.db = db
        self.queue = queue
        self._library_root = library_root
        self._extract_pdf = extract_pdf
        self._render_pages = render_pages
        self._page_readers = page_readers or {
            str(OcrEngine.PADDLEOCR): self._read_page_paddle,
            str(OcrEngine.TESSERACT): self._read_page_tesseract,
        }
        self._engine_available = engine_available or _engine_available
        self._summarize = summarize

    @property
    def library_root(self) -> Path:
        if self._library_root is None:
            from app.core.config import get_config

            self._library_root = get_config().library_root
        return self._library_root

    # ------------------------------------------------------------------
    # Classification (after download / on demand)
    # ------------------------------------------------------------------

    async def classify_document(self, document_id: int) -> None:
        """Decide searchable vs OCR-required and store native text.

        Never raises: a classification hiccup must not fail the download
        that triggered it. Problems are logged instead.
        """
        try:
            await self._classify(document_id)
        except Exception as error:  # noqa: BLE001 — deliberate belt-and-braces
            await log_event(
                self.db, "ocr", "WARNING",
                f"Could not classify document text: {error}",
                document_id=document_id,
            )

    async def _classify(self, document_id: int) -> None:
        documents = DocumentsRepository(self.db)
        doc = await documents.get(document_id)
        settings = (await SettingsRepository(self.db).get_all())["values"]
        min_chars = int(settings.get("ocr.min_chars_per_page_searchable", 40))

        path = resolve_in_library(self.library_root, doc.get("rel_path"))
        if path is None or not path.is_file():
            await log_event(
                self.db, "ocr", "WARNING",
                "Cannot classify: the document's file is not on disk.",
                document_id=document_id,
            )
            return

        kind = doc.get("file_kind")
        text = ""
        page_count: int | None = None

        if kind == str(FileKind.PDF):
            try:
                text, page_count = await asyncio.to_thread(self._extract_pdf, path)
            except extract.ExtractorUnavailable as error:
                await log_event(
                    self.db, "ocr", "WARNING", str(error), document_id=document_id
                )
                return
        elif kind == str(FileKind.DOCX):
            text = await asyncio.to_thread(extract.extract_docx_text, path)
        elif kind == str(FileKind.XLSX):
            text = await asyncio.to_thread(extract.extract_xlsx_text, path)
        elif kind == str(FileKind.HTML):
            text = await asyncio.to_thread(extract.extract_html_text, path)
        else:
            # zip/other: nothing to read; not an OCR candidate either.
            await documents.set_extraction(
                document_id, text=None, page_count=None,
                is_searchable=False, ocr_status=str(OcrStatus.NOT_REQUIRED),
            )
            return

        searchable = extract.is_searchable_text(text, page_count or 1, min_chars)

        if searchable:
            await documents.set_extraction(
                document_id, text=text.strip(), page_count=page_count,
                is_searchable=True, ocr_status=str(OcrStatus.NOT_REQUIRED),
            )
            await log_event(
                self.db, "ocr", "INFO",
                f"Text layer found ({len(text.strip())} characters) — "
                "document is searchable.",
                document_id=document_id,
            )
            if self._summarize is not None:
                await self._summarize(document_id)
            return

        if kind != str(FileKind.PDF):
            # A wordless spreadsheet or empty page — nothing OCR could add.
            await documents.set_extraction(
                document_id, text=text.strip() or None, page_count=page_count,
                is_searchable=False, ocr_status=str(OcrStatus.NOT_REQUIRED),
            )
            return

        await documents.set_extraction(
            document_id, text=None, page_count=page_count,
            is_searchable=False, ocr_status=str(OcrStatus.REQUIRED),
        )
        await log_event(
            self.db, "ocr", "INFO",
            "No usable text layer — this looks like a scanned document "
            "(OCR required).",
            document_id=document_id,
        )

        if bool(settings.get("ocr.auto_run", True)) and self.queue.has_handler(
            TASK_RUN_OCR
        ):
            await self.queue_run(document_id, settings)

    async def queue_run(
        self, document_id: int, settings: dict[str, Any] | None = None
    ) -> int:
        """Create a durable run row and submit it (used by auto-run)."""
        if settings is None:
            settings = (await SettingsRepository(self.db).get_all())["values"]
        engine = self._choose_engine(settings)
        run_id = await OcrRepository(self.db).create_run(document_id, engine)
        await DocumentsRepository(self.db).set_ocr_status(
            document_id, str(OcrStatus.QUEUED)
        )
        self.queue.submit(
            TASK_RUN_OCR,
            {"ocr_id": run_id, "document_id": document_id, "engine": engine},
            dedupe_key=f"ocr:{run_id}",
        )
        return run_id

    def _choose_engine(self, settings: dict[str, Any]) -> str:
        """The preferred engine that is actually installed, else the fallback,
        else the preference as-is (the run will then fail with the exact
        install instructions — visible, actionable)."""
        preferred = str(settings.get("ocr.engine", OcrEngine.PADDLEOCR))
        fallback = str(settings.get("ocr.fallback_engine", OcrEngine.TESSERACT))
        for candidate in (preferred, fallback):
            if candidate in OcrEngine and self._engine_available(candidate, settings):
                return candidate
        return preferred if preferred in OcrEngine else str(OcrEngine.TESSERACT)

    # ------------------------------------------------------------------
    # The queue handler
    # ------------------------------------------------------------------

    async def handle(self, payload: dict[str, Any], ctx: TaskContext) -> None:
        document_id = int(payload["document_id"])
        settings = (await SettingsRepository(self.db).get_all())["values"]

        if "ocr_id" in payload:  # startup resume knows its run row
            run_id = int(payload["ocr_id"])
            engine = str(payload.get("engine") or self._choose_engine(settings))
        else:  # the Run OCR button: create the durable row here
            engine = self._choose_engine(settings)
            run_id = await OcrRepository(self.db).create_run(document_id, engine)

        await self._run(run_id, document_id, engine, settings, ctx)

    async def _run(
        self,
        run_id: int,
        document_id: int,
        engine: str,
        settings: dict[str, Any],
        ctx: TaskContext,
    ) -> None:
        ocr = OcrRepository(self.db)
        documents = DocumentsRepository(self.db)

        doc = await documents.get(document_id)
        path = resolve_in_library(self.library_root, doc.get("rel_path"))
        if path is None or not path.is_file():
            await self._fail(
                run_id, document_id,
                "The document's file is not on disk (was it moved or deleted?).",
            )
            return

        await ocr.mark(run_id, str(JobStatus.RUNNING))
        await documents.set_ocr_status(document_id, str(OcrStatus.RUNNING))
        started = time.monotonic()

        reader = self._page_readers.get(engine)
        if reader is None:
            await self._fail(run_id, document_id, f"Unknown OCR engine {engine!r}.")
            return

        try:
            pages = await asyncio.to_thread(
                self._render_pages,
                path,
                dpi=int(settings.get("ocr.render_dpi", 300)),
                poppler_path=str(settings.get("ocr.poppler_path", "") or "") or None,
            )
        except engines.EngineUnavailable as error:
            await self._fail(run_id, document_id, str(error))
            return

        page_texts: list[str] = []
        confidences: list[float] = []
        try:
            for index, image in enumerate(pages):
                ctx.raise_if_cancelled()
                text, confidence = await asyncio.to_thread(
                    reader, image, settings
                )
                page_texts.append(text.strip())
                if confidence is not None:
                    confidences.append(confidence)
                await log_event(
                    self.db, "ocr", "DEBUG",
                    f"OCR page {index + 1} done ({len(text.strip())} characters).",
                    document_id=document_id,
                )
        except TaskCancelled:
            await ocr.mark(
                run_id, str(JobStatus.CANCELLED), error_message="Cancelled by user"
            )
            await documents.set_ocr_status(document_id, str(OcrStatus.REQUIRED))
            raise
        except engines.EngineUnavailable as error:
            await self._fail(run_id, document_id, str(error))
            return
        except Exception as error:  # noqa: BLE001 — engine crash on some page
            await self._fail(
                run_id, document_id, f"The OCR engine failed: {error}"
            )
            return

        full_text = "\n\n".join(t for t in page_texts if t).strip()
        duration = round(time.monotonic() - started, 2)

        if not full_text:
            await self._fail(
                run_id, document_id,
                "OCR finished but recognised no text — the scan may be "
                "blank, rotated, or too low-quality.",
                page_count=len(page_texts), duration=duration,
            )
            return

        await ocr.mark(
            run_id, str(JobStatus.COMPLETED),
            text_content=full_text,
            avg_confidence=(
                round(sum(confidences) / len(confidences), 4) if confidences else None
            ),
            page_count=len(page_texts),
            duration_seconds=duration,
        )
        await documents.set_ocr_status(document_id, str(OcrStatus.COMPLETED))
        await documents.reindex(document_id)  # index merges the OCR text in
        await log_event(
            self.db, "ocr", "INFO",
            f"OCR completed with {engine}: {len(page_texts)} page(s), "
            f"{len(full_text)} characters in {duration}s — document is now "
            "searchable.",
            document_id=document_id,
        )
        if self._summarize is not None:
            await self._summarize(document_id)

    async def _fail(
        self,
        run_id: int,
        document_id: int,
        message: str,
        *,
        page_count: int | None = None,
        duration: float | None = None,
    ) -> None:
        await OcrRepository(self.db).mark(
            run_id, str(JobStatus.FAILED), error_message=message,
            page_count=page_count, duration_seconds=duration,
        )
        await DocumentsRepository(self.db).set_ocr_status(
            document_id, str(OcrStatus.FAILED)
        )
        await ReviewRepository(self.db).create(
            "ocr_failure", message, document_id=document_id
        )
        await log_event(
            self.db, "ocr", "ERROR", f"OCR failed: {message}",
            document_id=document_id,
        )

    # ------------------------------------------------------------------
    # Default page readers (thin adapters over the engines module)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_page_paddle(image: Any, settings: dict[str, Any]) -> tuple[str, float | None]:
        return engines.ocr_page_paddleocr(
            image, languages=list(settings.get("ocr.languages", ["en"]))
        )

    @staticmethod
    def _read_page_tesseract(image: Any, settings: dict[str, Any]) -> tuple[str, float | None]:
        return engines.ocr_page_tesseract(
            image,
            languages=list(settings.get("ocr.languages", ["en"])),
            tesseract_path=str(settings.get("ocr.tesseract_path", "") or "") or None,
        )


def _engine_available(engine: str, settings: dict[str, Any]) -> bool:
    """Cheap availability probe used to pick an engine at queue time."""
    if engine == str(OcrEngine.PADDLEOCR):
        try:
            import paddleocr  # noqa: F401
        except Exception:  # noqa: BLE001 — any import problem means unavailable
            return False
        return True
    if engine == str(OcrEngine.TESSERACT):
        try:
            engines.find_tesseract(
                str(settings.get("ocr.tesseract_path", "") or "") or None
            )
        except engines.EngineUnavailable:
            return False
        return True
    return False
