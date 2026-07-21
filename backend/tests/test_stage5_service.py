"""Stage 5 tests: the OCR service end-to-end with injected fake engines.

Real database and real files in a temp library; the PDF extractor, page
renderer and page readers are injected fakes so every path — searchable,
scanned, missing engine, failure, cancellation — runs deterministically
with nothing installed.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import Database  # noqa: E402
from app.core.queue import Task, TaskContext  # noqa: E402
from app.repositories.documents import DocumentsRepository  # noqa: E402
from app.repositories.ocr import OcrRepository  # noqa: E402
from app.repositories.review import ReviewRepository  # noqa: E402
from app.repositories.search import SearchRepository  # noqa: E402
from app.repositories.settings import SettingsRepository  # noqa: E402
from app.services.ocr.engines import EngineUnavailable  # noqa: E402
from app.services.ocr.extract import ExtractorUnavailable  # noqa: E402
from app.services.ocr.service import OcrService  # noqa: E402

SCHEMA = BACKEND_DIR / "db" / "schema.sql"
MIGRATIONS = BACKEND_DIR / "db" / "migrations"

RICH_TEXT = (
    "Master Direction – Know Your Customer (KYC) Direction, 2016. "
    "Regulated entities shall undertake customer identification. " * 5
)


class StubQueue:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, dict[str, Any], str | None]] = []

    def has_handler(self, task_type: str) -> bool:
        return True

    def submit(self, task_type, payload=None, dedupe_key=None):
        self.submitted.append((task_type, payload or {}, dedupe_key))
        return Task(id="stub", task_type=task_type, payload=payload or {})


def make_ctx() -> TaskContext:
    return TaskContext(Task(id="t", task_type="run_ocr", payload={}))


class OcrCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lkm-ocr-")
        base = Path(self.tmp.name)
        self.library = base / "library"
        (self.library / "RBI").mkdir(parents=True)
        self.db = Database(base / "t.sqlite3", SCHEMA, MIGRATIONS)
        await self.db.connect()
        self.queue = StubQueue()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def add_document(self, filename: str = "scan.pdf", kind: str = "pdf",
                           sha: str = "01" * 32) -> dict[str, Any]:
        (self.library / "RBI" / filename).write_bytes(b"%PDF fixture bytes")
        return await DocumentsRepository(self.db).create({
            "title": "KYC Direction",
            "authority": "RBI",
            "original_filename": filename,
            "stored_filename": filename,
            "rel_path": f"RBI/{filename}",
            "file_kind": kind,
            "sha256": sha,
        })

    def service(self, **overrides) -> OcrService:
        defaults = dict(
            library_root=self.library,
            extract_pdf=lambda path: (RICH_TEXT, 2),
            render_pages=lambda path, dpi, poppler_path: ["img1", "img2"],
            page_readers={
                "paddleocr": lambda img, s: (f"paddle text {img}", 0.9),
                "tesseract": lambda img, s: (f"tesseract text {img}", None),
            },
            engine_available=lambda engine, settings: True,
        )
        defaults.update(overrides)
        return OcrService(self.db, self.queue, **defaults)


class TestClassification(OcrCase):
    async def test_pdf_with_text_layer_becomes_searchable(self) -> None:
        doc = await self.add_document()
        await self.service().classify_document(doc["id"])

        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["is_searchable"], 1)
        self.assertEqual(row["ocr_status"], "not_required")
        self.assertEqual(row["page_count"], 2)

        hits = await SearchRepository(self.db).search(q="customer")
        self.assertEqual(hits["total"], 1)  # native text reached the index
        self.assertEqual(self.queue.submitted, [])

    async def test_scanned_pdf_marked_required_and_auto_queued(self) -> None:
        doc = await self.add_document()
        service = self.service(extract_pdf=lambda path: ("", 3))
        await service.classify_document(doc["id"])

        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["is_searchable"], 0)
        self.assertEqual(row["ocr_status"], "queued")  # auto-run queued it
        self.assertEqual(row["page_count"], 3)

        self.assertEqual(len(self.queue.submitted), 1)
        task_type, payload, dedupe = self.queue.submitted[0]
        self.assertEqual(task_type, "run_ocr")
        self.assertEqual(payload["document_id"], doc["id"])
        self.assertIn("ocr_id", payload)
        self.assertEqual(dedupe, f"ocr:{payload['ocr_id']}")
        runs = await OcrRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(len(runs), 1)  # durable row exists for resume

    async def test_auto_run_respects_setting(self) -> None:
        await SettingsRepository(self.db).set_many({"ocr.auto_run": False})
        doc = await self.add_document()
        await self.service(extract_pdf=lambda path: ("", 3)).classify_document(doc["id"])
        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["ocr_status"], "required")
        self.assertEqual(self.queue.submitted, [])

    async def test_docx_uses_stdlib_extraction(self) -> None:
        import zipfile

        doc = await self.add_document("note.docx", kind="docx", sha="02" * 32)
        path = self.library / "RBI" / "note.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="urn:x"><w:body><w:p><w:r>'
                "<w:t>" + ("Board resolution text. " * 20) + "</w:t>"
                "</w:r></w:p></w:body></w:document>",
            )
        await self.service().classify_document(doc["id"])
        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["is_searchable"], 1)
        hits = await SearchRepository(self.db).search(q="resolution")
        self.assertEqual(hits["total"], 1)

    async def test_missing_pdf_libraries_log_and_leave_unchecked(self) -> None:
        def unavailable(path):
            raise ExtractorUnavailable("install hint here")

        doc = await self.add_document()
        await self.service(extract_pdf=unavailable).classify_document(doc["id"])
        row = await DocumentsRepository(self.db).get(doc["id"])
        # Untouched: stays exactly as created (no false classification).
        self.assertEqual(row["ocr_status"], "not_required")
        logs = await self.db.fetch_all(
            "SELECT message FROM logs WHERE level = 'WARNING'"
        )
        self.assertTrue(any("install hint" in r["message"] for r in logs))

    async def test_zip_files_are_not_ocr_candidates(self) -> None:
        doc = await self.add_document("bundle.zip", kind="zip", sha="03" * 32)
        await self.service().classify_document(doc["id"])
        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["is_searchable"], 0)
        self.assertEqual(row["ocr_status"], "not_required")


class TestOcrRuns(OcrCase):
    async def test_button_payload_creates_run_and_completes(self) -> None:
        doc = await self.add_document()
        service = self.service()
        await service.handle({"document_id": doc["id"]}, make_ctx())

        runs = await OcrRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "completed")
        self.assertEqual(runs[0]["engine"], "paddleocr")  # preferred available
        self.assertEqual(runs[0]["page_count"], 2)
        self.assertAlmostEqual(runs[0]["avg_confidence"], 0.9)

        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["ocr_status"], "completed")

        # The OCR text is searchable without touching documents.text_content.
        text = await DocumentsRepository(self.db).get_text(doc["id"])
        self.assertIsNone(text["native_text"])
        self.assertIn("paddle text", text["ocr_text"])
        hits = await SearchRepository(self.db).search(q="paddle")
        self.assertEqual(hits["total"], 1)

    async def test_resume_payload_uses_existing_run_row(self) -> None:
        doc = await self.add_document()
        run_id = await OcrRepository(self.db).create_run(doc["id"], "tesseract")
        await self.service().handle(
            {"ocr_id": run_id, "document_id": doc["id"], "engine": "tesseract"},
            make_ctx(),
        )
        run = await OcrRepository(self.db).get(run_id)
        self.assertEqual(run["status"], "completed")
        self.assertIn("tesseract text", run["text_content"])
        self.assertIsNone(run["avg_confidence"])  # tesseract reports none
        runs = await OcrRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(len(runs), 1)  # no second row created

    async def test_engine_falls_back_when_preferred_missing(self) -> None:
        doc = await self.add_document()
        service = self.service(
            engine_available=lambda engine, s: engine == "tesseract"
        )
        await service.handle({"document_id": doc["id"]}, make_ctx())
        runs = await OcrRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(runs[0]["engine"], "tesseract")
        self.assertEqual(runs[0]["status"], "completed")

    async def test_missing_programs_fail_with_hint_and_review_item(self) -> None:
        def no_poppler(path, dpi, poppler_path):
            raise EngineUnavailable("Install Poppler like so…")

        doc = await self.add_document()
        await self.service(render_pages=no_poppler).handle(
            {"document_id": doc["id"]}, make_ctx()
        )
        runs = await OcrRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(runs[0]["status"], "failed")
        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["ocr_status"], "failed")
        review = await ReviewRepository(self.db).list()
        self.assertEqual(review["total"], 1)
        self.assertEqual(review["items"][0]["category"], "ocr_failure")
        self.assertIn("Poppler", review["items"][0]["detail"])

    async def test_blank_scan_fails_honestly(self) -> None:
        doc = await self.add_document()
        service = self.service(
            page_readers={"paddleocr": lambda img, s: ("   ", None),
                          "tesseract": lambda img, s: ("", None)},
        )
        await service.handle({"document_id": doc["id"]}, make_ctx())
        runs = await OcrRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("no text", runs[0]["error_message"])

    async def test_cancellation_between_pages(self) -> None:
        doc = await self.add_document()
        ctx = make_ctx()

        def reader(img, s):
            ctx.task.cancel_event.set()  # user presses Cancel during page 1
            return "partial", None

        from app.core.queue import TaskCancelled

        with self.assertRaises(TaskCancelled):
            await self.service(
                page_readers={"paddleocr": reader, "tesseract": reader}
            ).handle({"document_id": doc["id"]}, ctx)

        runs = await OcrRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(runs[0]["status"], "cancelled")
        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["ocr_status"], "required")  # button comes back

    async def test_missing_file_fails_before_running(self) -> None:
        doc = await self.add_document()
        (self.library / "RBI" / "scan.pdf").unlink()
        await self.service().handle({"document_id": doc["id"]}, make_ctx())
        runs = await OcrRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("not on disk", runs[0]["error_message"])


class TestDownloadClassifyWiring(unittest.IsolatedAsyncioTestCase):
    """A successful download hands the new document to the classifier."""

    async def test_classifier_called_after_download(self) -> None:
        from app.services.analysis.service import AnalysisService
        from app.services.downloader.service import DownloaderService
        from tests.stage4_server import FixtureServer
        from app.repositories.sources import SourcesRepository

        server = FixtureServer().start()
        tmp = tempfile.TemporaryDirectory(prefix="lkm-wire-")
        try:
            base = Path(tmp.name)
            db = Database(base / "t.sqlite3", SCHEMA, MIGRATIONS)
            await db.connect()
            try:
                queue = StubQueue()
                classified: list[int] = []

                async def classify(document_id: int) -> None:
                    classified.append(document_id)

                analysis = AnalysisService(db, queue)
                downloader = DownloaderService(
                    db, queue, analysis,
                    library_root=base / "library", classify=classify,
                )
                source = await SourcesRepository(db).add(
                    url=server.url("/direct-file")
                )
                await downloader.download_source(source["id"], make_ctx())

                docs = await db.fetch_all("SELECT id FROM documents")
                self.assertEqual(len(docs), 1)
                self.assertEqual(classified, [docs[0]["id"]])
            finally:
                await db.close()
        finally:
            server.stop()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
