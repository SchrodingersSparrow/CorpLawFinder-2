"""Stage 4 tests: analysis + downloader services end-to-end.

Real database (fresh temp file per test), real HTTP against the loopback
fixture server, real files on disk in a temp library — only the task queue is
a stub that records submissions (nothing should run implicitly during a test).
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import Database  # noqa: E402
from app.core.queue import Task, TaskContext  # noqa: E402
from app.repositories.downloads import DownloadsRepository  # noqa: E402
from app.repositories.review import ReviewRepository  # noqa: E402
from app.repositories.settings import SettingsRepository  # noqa: E402
from app.repositories.sources import SourcesRepository  # noqa: E402
from app.services.analysis.service import AnalysisService  # noqa: E402
from app.services.downloader.service import DownloaderService  # noqa: E402
from tests.stage4_server import FixtureServer  # noqa: E402

SCHEMA = BACKEND_DIR / "db" / "schema.sql"
MIGRATIONS = BACKEND_DIR / "db" / "migrations"


class StubQueue:
    """Records submissions; reports every task type as handled."""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, dict[str, Any], str | None]] = []

    def has_handler(self, task_type: str) -> bool:
        return True

    def submit(self, task_type, payload=None, dedupe_key=None):
        self.submitted.append((task_type, payload or {}, dedupe_key))
        return Task(id="stub", task_type=task_type, payload=payload or {})


def make_ctx() -> TaskContext:
    return TaskContext(Task(id="test-task", task_type="test", payload={}))


class ServiceCase(unittest.IsolatedAsyncioTestCase):
    server: FixtureServer

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FixtureServer().start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lkm-svc-")
        base = Path(self.tmp.name)
        self.library = base / "library"
        self.db = Database(base / "test.sqlite3", SCHEMA, MIGRATIONS)
        await self.db.connect()
        # Keep tests quick: no polite pauses, tiny retry backoff.
        await SettingsRepository(self.db).set_many({
            "download.polite_delay_seconds": 0,
            "download.retry_backoff_seconds": 0,
        })
        self.queue = StubQueue()
        self.analysis = AnalysisService(self.db, self.queue)
        self.downloader = DownloaderService(
            self.db, self.queue, self.analysis, library_root=self.library
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def add_source(self, path: str, **fields) -> dict[str, Any]:
        repo = SourcesRepository(self.db)
        row = await repo.add(url=self.server.url(path))
        if fields:
            row = await repo.update(row["id"], fields)
        return row

    async def get_source(self, source_id: int) -> dict[str, Any]:
        return await SourcesRepository(self.db).get(source_id)


class TestAnalysis(ServiceCase):
    async def test_listing_page_finds_links_and_queues_download(self) -> None:
        source = await self.add_source("/listing.html")
        await self.analysis.analyze(source["id"], source["url"], make_ctx())

        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "analyzed")
        self.assertEqual(row["page_title"], "Reserve Bank of India - Test Listing")
        self.assertEqual(row["document_count"], 3)
        self.assertEqual(row["pdf_count"], 3)
        self.assertIsNone(row["error_message"])
        self.assertIsNotNone(row["last_checked"])

        links = json.loads(row["analysis_json"])["links"]
        self.assertEqual(
            [link["url"] for link in links],
            [self.server.url("/a.pdf"), self.server.url("/b.pdf"),
             self.server.url("/dup.pdf")],
        )  # about.html excluded from discovery
        self.assertIn("July 15, 2026", links[0]["text"])

        # analysis.auto_download default True → download task submitted.
        self.assertEqual(len(self.queue.submitted), 1)
        task_type, payload, dedupe = self.queue.submitted[0]
        self.assertEqual(task_type, "download_file")
        self.assertEqual(payload["source_id"], source["id"])
        self.assertEqual(dedupe, f"download-source:{source['id']}")

    async def test_auto_download_can_be_turned_off(self) -> None:
        await SettingsRepository(self.db).set_many({"analysis.auto_download": False})
        source = await self.add_source("/listing.html")
        await self.analysis.analyze(source["id"], source["url"], make_ctx())
        self.assertEqual(self.queue.submitted, [])

    async def test_login_page_fails_honestly_with_review_item(self) -> None:
        source = await self.add_source("/login")
        await self.analysis.analyze(source["id"], source["url"], make_ctx())

        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn("login", row["error_message"].lower())

        review = await ReviewRepository(self.db).list()
        self.assertEqual(review["total"], 1)
        self.assertEqual(review["items"][0]["category"], "other")
        self.assertEqual(review["items"][0]["source_id"], source["id"])
        self.assertEqual(self.queue.submitted, [])  # nothing to download

    async def test_page_without_files_is_analyzed_with_note(self) -> None:
        source = await self.add_source("/empty.html")
        await self.analysis.analyze(source["id"], source["url"], make_ctx())

        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "analyzed")
        self.assertEqual(row["document_count"], 0)
        self.assertIn("No downloadable files", row["error_message"])
        self.assertEqual(self.queue.submitted, [])

    async def test_direct_file_url_becomes_its_own_link(self) -> None:
        source = await self.add_source("/direct-file")
        await self.analysis.analyze(source["id"], source["url"], make_ctx())

        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "analyzed")
        self.assertEqual(row["document_count"], 1)
        self.assertEqual(row["pdf_count"], 1)
        links = json.loads(row["analysis_json"])["links"]
        self.assertEqual(links[0]["url"], self.server.url("/direct-file"))

    async def test_unreachable_site_fails_with_reason(self) -> None:
        repo = SourcesRepository(self.db)
        source = await repo.add(url="http://127.0.0.1:9/nothing")
        await self.analysis.analyze(source["id"], source["url"], make_ctx())
        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn("Could not reach", row["error_message"])


class TestDownloadSource(ServiceCase):
    async def test_full_fan_out_with_dedupe_naming_and_folders(self) -> None:
        source = await self.add_source("/listing.html", authority="RBI")
        await self.downloader.download_source(source["id"], make_ctx())

        rows = await DownloadsRepository(self.db).for_source(source["id"])
        by_url = {row["url"].rsplit("/", 1)[-1]: row["status"] for row in rows}
        self.assertEqual(by_url, {
            "a.pdf": "succeeded",
            "b.pdf": "succeeded",
            "dup.pdf": "skipped_duplicate",  # same bytes as a.pdf → SHA match
        })

        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "completed")
        self.assertIsNone(row["error_message"])

        docs = await self.db.fetch_all(
            "SELECT title, doc_type, doc_date, authority, stored_filename, rel_path"
            " FROM documents ORDER BY id"
        )
        self.assertEqual(len(docs), 2)  # duplicate created no third document
        first = dict(docs[0])
        self.assertEqual(first["authority"], "RBI")
        self.assertEqual(first["doc_type"], "Master Direction")
        self.assertEqual(first["doc_date"], "2026-07-15")
        self.assertTrue(first["stored_filename"].startswith("RBI - Master Direction"))
        self.assertTrue(first["rel_path"].startswith("RBI/"))
        second = dict(docs[1])
        self.assertEqual(second["doc_date"], "2026-03-05")  # 05/03/2026, day first

        for doc in (first, second):
            path = self.library / doc["rel_path"]
            self.assertTrue(path.is_file(), f"missing {path}")
        leftovers = list((self.library / ".incoming").glob("*"))
        self.assertEqual(leftovers, [])  # no .part files left behind

        # The duplicate row points at the document it duplicates.
        dup_row = next(r for r in rows if r["url"].endswith("/dup.pdf"))
        full = await DownloadsRepository(self.db).get(dup_row["id"])
        self.assertIsNotNone(full["document_id"])

    async def test_rerun_skips_already_downloaded_urls(self) -> None:
        source = await self.add_source("/listing.html", authority="RBI")
        await self.downloader.download_source(source["id"], make_ctx())
        count_before = len(await DownloadsRepository(self.db).for_source(source["id"]))

        await self.downloader.download_source(source["id"], make_ctx())
        count_after = len(await DownloadsRepository(self.db).for_source(source["id"]))
        self.assertEqual(count_before, count_after)  # no repeat attempts
        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "completed")

    async def test_unanalyzed_source_is_analyzed_first(self) -> None:
        source = await self.add_source("/listing.html")
        self.assertIsNone(source["analysis_json"])
        await self.downloader.download_source(source["id"], make_ctx())
        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["document_count"], 3)

    async def test_mixed_failure_rolls_up_and_files_review_item(self) -> None:
        mixed = (
            '<html><head><title>Mixed</title></head><body>'
            '<a href="/b.pdf">Good circular</a>'
            '<a href="/gone.pdf">Broken link to a removed file</a>'
            '<p>A listing where one file is fine and the other is gone. Extra '
            'sentences keep the JavaScript-shell heuristic comfortable.</p>'
            "</body></html>"
        ).encode()
        self.server.httpd.routes["/mixed.html"] = (
            lambda h: h._send(200, mixed, "text/html")
        )

        source = await self.add_source("/mixed.html", authority="RBI")
        await self.downloader.download_source(source["id"], make_ctx())

        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "completed")
        self.assertIn("1 of 2 files failed", row["error_message"])

        review = await ReviewRepository(self.db).list()
        self.assertEqual(review["total"], 1)
        self.assertEqual(review["items"][0]["category"], "download_failure")
        self.assertIn("/gone.pdf", review["items"][0]["detail"])

    async def test_single_retry_payload_updates_rollup(self) -> None:
        source = await self.add_source("/listing.html", authority="RBI")
        downloads = DownloadsRepository(self.db)
        download_id = await downloads.create(
            self.server.url("/b.pdf"), source_id=source["id"]
        )
        await self.downloader.handle(
            {"download_id": download_id, "url": self.server.url("/b.pdf"),
             "source_id": source["id"]},
            make_ctx(),
        )
        full = await downloads.get(download_id)
        self.assertEqual(full["status"], "succeeded")
        self.assertIsNotNone(full["document_id"])
        self.assertEqual(full["attempts"], 1)
        row = await self.get_source(source["id"])
        self.assertEqual(row["status"], "completed")  # rollup ran


if __name__ == "__main__":
    unittest.main()
