"""Stage 2 core tests — standard library only (python -m unittest).

Covers everything below the HTTP layer: the async SQLite bridge, seeding,
every repository, FTS5 search, the task queue and startup resume. The
FastAPI layer on top is exercised end-to-end by scripts/dev_check.py.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core import defaults  # noqa: E402
from app.core.database import Database  # noqa: E402
from app.core.errors import (  # noqa: E402
    DuplicateError,
    InvalidInputError,
    NotFoundError,
)
from app.core.queue import (  # noqa: E402
    TASK_DOWNLOAD_FILE,
    TaskQueue,
)
from app.core.resume import requeue_pending  # noqa: E402
from app.core.seed import seed_defaults  # noqa: E402
from app.repositories.dashboard import DashboardRepository  # noqa: E402
from app.repositories.documents import DocumentsRepository  # noqa: E402
from app.repositories.downloads import DownloadsRepository  # noqa: E402
from app.repositories.logs import LogsRepository  # noqa: E402
from app.repositories.metadata import MetadataRepository  # noqa: E402
from app.repositories.review import ReviewRepository  # noqa: E402
from app.repositories.search import SearchRepository, build_match_query  # noqa: E402
from app.repositories.settings import SettingsRepository  # noqa: E402
from app.repositories.sources import SourcesRepository  # noqa: E402
from app.repositories.tags import TagsRepository  # noqa: E402

SCHEMA = BACKEND_DIR / "db" / "schema.sql"
MIGRATIONS = BACKEND_DIR / "db" / "migrations"


def make_doc(n: int = 1, **overrides):
    doc = {
        "title": f"Circular {n}",
        "authority": "RBI",
        "doc_type": "Circular",
        "doc_date": "2025-01-0" + str(min(n, 9)),
        "original_filename": f"circ{n}.pdf",
        "stored_filename": f"RBI - Circular - Circular {n} - 2025.pdf",
        "rel_path": f"RBI/circ{n}.pdf",
        "file_kind": "pdf",
        "file_size_bytes": 1000 + n,
        "sha256": f"{n:064x}",
        "text_content": f"Body text about foreign exchange item {n}.",
    }
    doc.update(overrides)
    return doc


class DbCase(unittest.IsolatedAsyncioTestCase):
    """Fresh database file per test."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lkm-test-")
        self.db = Database(
            Path(self.tmp.name) / "test.sqlite3", SCHEMA, MIGRATIONS
        )
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()


class TestDatabaseBridge(DbCase):
    async def test_schema_objects_exist(self) -> None:
        names = {
            r["name"] for r in await self.db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        for required in ("sources", "documents", "search_index", "search_fts",
                         "v_dashboard_counts"):
            self.assertIn(required, names)

    async def test_run_rolls_back_on_error(self) -> None:
        def bad(conn):
            conn.execute("INSERT INTO tags (name, kind) VALUES ('x', 'topic')")
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await self.db.run(bad)
        count = await self.db.fetch_value(
            "SELECT COUNT(*) FROM tags WHERE name = 'x'"
        )
        self.assertEqual(count, 0)

    async def test_concurrent_writes_are_serialised(self) -> None:
        async def insert(i: int) -> None:
            await self.db.insert(
                "INSERT INTO logs (level, category, message) VALUES (?, ?, ?)",
                ("INFO", "system", f"m{i}"),
            )

        await asyncio.gather(*(insert(i) for i in range(50)))
        self.assertEqual(
            await self.db.fetch_value("SELECT COUNT(*) FROM logs"), 50
        )


class TestSeed(DbCase):
    async def test_seed_is_idempotent(self) -> None:
        await seed_defaults(self.db)
        await seed_defaults(self.db)
        self.assertEqual(
            await self.db.fetch_value("SELECT COUNT(*) FROM settings"),
            len(defaults.DEFAULT_SETTINGS),
        )
        topics = await self.db.fetch_value(
            "SELECT COUNT(*) FROM tags WHERE kind = 'topic'"
        )
        self.assertEqual(topics, len(defaults.DEFAULT_SETTINGS["topics.default"]))


class TestSources(DbCase):
    async def test_add_normalises_and_rejects_duplicates(self) -> None:
        repo = SourcesRepository(self.db)
        row = await repo.add("rbi.org.in/notifications", title="RBI")
        self.assertTrue(row["url"].startswith("https://"))
        with self.assertRaises(DuplicateError):
            await repo.add(row["url"])
        with self.assertRaises(InvalidInputError):
            await repo.add("not a url")

    async def test_add_many_buckets(self) -> None:
        repo = SourcesRepository(self.db)
        await repo.add("https://a.example.com/x")
        result = await repo.add_many([
            {"url": "https://b.example.com/y"},
            {"url": "https://a.example.com/x"},   # already saved
            {"url": "https://b.example.com/y"},   # repeated in batch
            {"url": "garbage"},
        ])
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(len(result["duplicates"]), 2)
        self.assertEqual(len(result["invalid"]), 1)

    async def test_list_filters_and_update(self) -> None:
        repo = SourcesRepository(self.db)
        a = await repo.add("https://rbi.org.in/one")
        await repo.add("https://sebi.gov.in/two")
        await repo.update(a["id"], {"authority": "RBI"})
        page = await repo.list(authority="rbi")
        self.assertEqual(page["total"], 1)
        page = await repo.list(q="sebi")
        self.assertEqual(page["total"], 1)
        await repo.set_status(a["id"], "analyzed", touch_last_checked=True)
        self.assertEqual((await repo.get(a["id"]))["status"], "analyzed")
        await repo.delete(a["id"])
        with self.assertRaises(NotFoundError):
            await repo.get(a["id"])


class TestDocuments(DbCase):
    async def test_create_duplicate_sha_and_delete(self) -> None:
        docs = DocumentsRepository(self.db)
        d1 = await docs.create(make_doc(1))
        with self.assertRaises(DuplicateError):
            await docs.create(make_doc(2, sha256=d1["sha256"]))
        row = await docs.delete(d1["id"])
        self.assertEqual(row["id"], d1["id"])
        with self.assertRaises(NotFoundError):
            await docs.get(d1["id"])

    async def test_update_canonical_audits_and_reindexes(self) -> None:
        docs = DocumentsRepository(self.db)
        d = await docs.create(make_doc(1))
        await docs.update_canonical(d["id"], {"title": "Renamed Circular"})
        meta = await MetadataRepository(self.db).list_for_document(d["id"])
        by_field = {m["field"]: m for m in meta}
        self.assertEqual(by_field["title"]["extractor"], "user")
        hits = await SearchRepository(self.db).search("Renamed")
        self.assertEqual(hits["total"], 1)
        with self.assertRaises(InvalidInputError):
            await docs.update_canonical(d["id"], {"doc_date": "04-01-2025"})

    async def test_list_filters(self) -> None:
        docs = DocumentsRepository(self.db)
        await docs.create(make_doc(1, authority="RBI"))
        await docs.create(make_doc(2, authority="SEBI", file_kind="docx"))
        self.assertEqual((await docs.list(authority="rbi"))["total"], 1)
        self.assertEqual((await docs.list(file_kind="docx"))["total"], 1)
        self.assertEqual((await docs.list(q="circ1"))["total"], 1)
        with self.assertRaises(InvalidInputError):
            await docs.list(file_kind="exe")

    async def test_get_text_native_and_cleared(self) -> None:
        docs = DocumentsRepository(self.db)
        d = await docs.create(make_doc(1))
        text = await docs.get_text(d["id"])
        self.assertTrue(text["native_text"])
        self.assertIsNone(text["ocr_text"])
        await docs.set_text_content(d["id"], None)
        text = await docs.get_text(d["id"])
        self.assertIsNone(text["native_text"])


class TestSearch(DbCase):
    def test_build_match_query(self) -> None:
        # Stage 7 upgraded the query language: trailing space = finished
        # words; without it the last word matches as a prefix while typing.
        self.assertEqual(build_match_query("foreign exchange "),
                         '"foreign" "exchange"')
        self.assertEqual(build_match_query("foreign exchange"),
                         '"foreign" "exchange"*')
        self.assertEqual(build_match_query("e-rupee "), '"e-rupee"')
        self.assertEqual(build_match_query("crypt* "), '"crypt"*')
        self.assertIsNone(build_match_query("   "))
        self.assertIsNone(build_match_query(None))

    async def test_search_title_body_tag_and_cleanup(self) -> None:
        docs = DocumentsRepository(self.db)
        d = await docs.create(make_doc(1, title="ODI Master Direction"))
        search = SearchRepository(self.db)

        self.assertEqual((await search.search("ODI"))["total"], 1)
        hits = await search.search("foreign")
        self.assertEqual(hits["total"], 1)
        self.assertTrue(hits["items"][0]["snippet"])

        await docs.add_tag(d["id"], "Overseas Investment")
        self.assertEqual((await search.search("Overseas"))["total"], 1)
        self.assertEqual(
            (await search.search("foreign", topic="Overseas Investment"))["total"], 1
        )
        self.assertEqual(
            (await search.search("foreign", authority="SEBI"))["total"], 0
        )

        await docs.delete(d["id"])
        self.assertEqual((await search.search("ODI"))["total"], 0)

    async def test_hyphenated_term_does_not_crash(self) -> None:
        docs = DocumentsRepository(self.db)
        await docs.create(make_doc(1, text_content="The e-rupee pilot began."))
        hits = await SearchRepository(self.db).search("e-rupee")
        self.assertEqual(hits["total"], 1)


class TestTags(DbCase):
    async def test_nocase_get_or_create_and_remove(self) -> None:
        docs = DocumentsRepository(self.db)
        d = await docs.create(make_doc(1))
        tags1 = await docs.add_tag(d["id"], "Fema")
        tags2 = await docs.add_tag(d["id"], "FEMA")
        self.assertEqual(len(tags2), len(tags1))  # NOCASE: same tag
        tag_id = tags1[-1]["id"] if tags1 else None
        listed = await TagsRepository(self.db).list()
        fema = next(t for t in listed if t["name"].lower() == "fema")
        remaining = await docs.remove_tag(d["id"], fema["id"])
        self.assertFalse(any(t["id"] == fema["id"] for t in remaining))


class TestMetadata(DbCase):
    async def test_upsert_validation(self) -> None:
        docs = DocumentsRepository(self.db)
        d = await docs.create(make_doc(1))
        repo = MetadataRepository(self.db)
        await repo.upsert(d["id"], "title", "X", confidence=0.8, extractor="ai")
        await repo.upsert(d["id"], "title", "Y", confidence=0.9, extractor="ai")
        rows = await repo.list_for_document(d["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], "Y")
        with self.assertRaises(InvalidInputError):
            await repo.upsert(d["id"], "title", "Z", confidence=2.0,
                              extractor="ai")
        with self.assertRaises(InvalidInputError):
            await repo.upsert(d["id"], "title", "Z", confidence=0.5,
                              extractor="wizard")


class TestReviewLogsSettingsDashboard(DbCase):
    async def test_review_flow(self) -> None:
        repo = ReviewRepository(self.db)
        item = await repo.create("other", "check me")
        self.assertEqual((await repo.list())["total"], 1)
        resolved = await repo.resolve(item["id"], "resolved")
        self.assertIsNotNone(resolved["resolved_at"])
        self.assertEqual((await repo.list())["total"], 0)  # default: open only
        with self.assertRaises(InvalidInputError):
            await repo.resolve(item["id"], "open")

    async def test_logs_cursor(self) -> None:
        repo = LogsRepository(self.db)
        for i in range(7):
            await repo.add("INFO", "system", f"event {i}")
        page1 = await repo.list(limit=5)
        self.assertEqual(len(page1["items"]), 5)
        self.assertIsNotNone(page1["next_before_id"])
        page2 = await repo.list(limit=5, before_id=page1["next_before_id"])
        self.assertEqual(len(page2["items"]), 2)
        ids1 = {r["id"] for r in page1["items"]}
        ids2 = {r["id"] for r in page2["items"]}
        self.assertFalse(ids1 & ids2)

    async def test_settings_types_and_reset(self) -> None:
        await seed_defaults(self.db)
        repo = SettingsRepository(self.db)
        with self.assertRaises(InvalidInputError):
            await repo.set_many({"nope": 1})
        with self.assertRaises(InvalidInputError):
            await repo.set_many({"download.max_concurrency": "3"})
        with self.assertRaises(InvalidInputError):
            await repo.set_many({"ai.enabled": 1})  # int is not bool
        result = await repo.set_many({"download.max_concurrency": 5,
                                      "ai.enabled": False})
        self.assertEqual(result["values"]["download.max_concurrency"], 5)
        self.assertIn("download.max_concurrency", result["overridden"])
        result = await repo.reset("download.max_concurrency")
        self.assertEqual(result["values"]["download.max_concurrency"], 3)
        result = await repo.reset_all()
        self.assertEqual(result["overridden"], [])

    async def test_dashboard_counts(self) -> None:
        await DocumentsRepository(self.db).create(
            make_doc(1, ocr_status="required")
        )
        counts = await DashboardRepository(self.db).counts()
        self.assertEqual(counts["total_documents"], 1)
        self.assertEqual(counts["documents_needing_ocr"], 1)


class TestQueue(unittest.IsolatedAsyncioTestCase):
    async def test_success_failure_dedupe_cancel(self) -> None:
        queue = TaskQueue(concurrency=2)
        done: list[str] = []

        async def good(payload, ctx):
            done.append(payload["n"])

        async def bad(payload, ctx):
            raise ValueError("expected failure")

        started = asyncio.Event()

        async def slow(payload, ctx):
            started.set()
            await ctx.sleep(30)

        queue.register("good", good)
        queue.register("bad", bad)
        queue.register("slow", slow)
        await queue.start()
        try:
            with self.assertRaises(ValueError):
                queue.submit("unregistered")

            t1 = queue.submit("good", {"n": "a"})
            t2 = queue.submit("bad", {})
            dup1 = queue.submit("good", {"n": "b"}, dedupe_key="same")
            dup2 = queue.submit("good", {"n": "c"}, dedupe_key="same")
            self.assertEqual(dup1.id, dup2.id)

            slow_task = queue.submit("slow", {})
            await asyncio.wait_for(started.wait(), 5)
            cancelled = queue.cancel(slow_task.id)
            self.assertIsNotNone(cancelled)

            for _ in range(200):
                states = {queue.get(t.id).status
                          for t in (t1, t2, slow_task)}
                if states <= {"succeeded", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(queue.get(t1.id).status, "succeeded")
            self.assertEqual(queue.get(t2.id).status, "failed")
            self.assertIn("expected failure", queue.get(t2.id).error)
            self.assertEqual(queue.get(slow_task.id).status, "cancelled")
        finally:
            await queue.stop()

    async def test_cancel_queued_task_instantly(self) -> None:
        queue = TaskQueue(concurrency=1)

        async def noop(payload, ctx):
            await asyncio.sleep(0)

        queue.register("noop", noop)
        # Not started: task stays queued, cancel flips it immediately.
        t = queue.submit("noop", {})
        cancelled = queue.cancel(t.id)
        self.assertEqual(cancelled.status, "cancelled")


class TestResume(DbCase):
    async def test_skips_without_handler_submits_with(self) -> None:
        dls = DownloadsRepository(self.db)
        await dls.create("https://x.example.com/a.pdf")

        queue = TaskQueue()
        result = await requeue_pending(self.db, queue)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["downloads"], 0)

        async def handler(payload, ctx):
            pass

        queue.register(TASK_DOWNLOAD_FILE, handler)
        result = await requeue_pending(self.db, queue)
        self.assertEqual(result["downloads"], 1)
        task = queue.snapshot()[0]
        self.assertEqual(task["task_type"], TASK_DOWNLOAD_FILE)


if __name__ == "__main__":
    unittest.main()
