"""Stage 7 tests: the search query language, live FTS behaviour, sorting,
saved searches, and the first real schema migration."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import Database  # noqa: E402
from app.core.errors import InvalidInputError, NotFoundError  # noqa: E402
from app.repositories.documents import DocumentsRepository  # noqa: E402
from app.repositories.saved_searches import SavedSearchesRepository  # noqa: E402
from app.repositories.search import SearchRepository, build_match_query  # noqa: E402

SCHEMA = BACKEND_DIR / "db" / "schema.sql"
MIGRATIONS = BACKEND_DIR / "db" / "migrations"


class TestQueryLanguage(unittest.TestCase):
    def test_plain_words_are_anded_and_last_gets_prefix(self) -> None:
        self.assertEqual(build_match_query("kyc updation"), '"kyc" "updation"*')

    def test_trailing_space_means_word_is_finished(self) -> None:
        self.assertEqual(build_match_query("kyc updation "), '"kyc" "updation"')

    def test_single_letter_gets_no_auto_prefix(self) -> None:
        self.assertEqual(build_match_query("k"), '"k"')

    def test_quoted_phrase_is_preserved(self) -> None:
        self.assertEqual(
            build_match_query('"master direction" kyc'),
            '"master direction" "kyc"*',
        )

    def test_or_between_terms(self) -> None:
        self.assertEqual(build_match_query("fema OR odi "), '"fema" OR "odi"')
        self.assertEqual(build_match_query("fema or odi "), '"fema" OR "odi"')

    def test_dangling_or_is_dropped(self) -> None:
        self.assertEqual(build_match_query("fema OR "), '"fema"')
        self.assertEqual(build_match_query("OR fema "), '"fema"')
        self.assertIsNone(build_match_query("OR OR"))

    def test_exclusions(self) -> None:
        self.assertEqual(build_match_query("kyc -draft "), '("kyc") NOT "draft"')
        self.assertEqual(
            build_match_query('kyc -"draft circular" updation '),
            '("kyc" "updation") NOT "draft circular"',
        )

    def test_only_exclusions_is_unsearchable(self) -> None:
        self.assertIsNone(build_match_query("-draft -old"))

    def test_explicit_star_kept_anywhere(self) -> None:
        self.assertEqual(build_match_query("amalgam* deal "), '"amalgam"* "deal"')

    def test_hostile_input_stays_inside_quotes(self) -> None:
        import re
        for nasty in ('((("; DROP TABLE--', 'a" OR "b', "NEAR(", "x:y", "()"):
            expression = build_match_query(nasty)
            if expression is not None:
                # Remove quoted terms; only harmless glue may remain.
                leftover = re.sub(r'"[^"]*"\*?', " ", expression)
                self.assertRegex(leftover, r"^[\s()ORNT]*$",
                                 f"{nasty!r} -> {expression!r}")

    def test_empty_and_junk(self) -> None:
        self.assertIsNone(build_match_query(""))
        self.assertIsNone(build_match_query("   "))
        self.assertIsNone(build_match_query('"'))
        self.assertIsNone(build_match_query("-"))


class LiveDbCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lkm-s7-")
        self.db = Database(Path(self.tmp.name) / "t.sqlite3", SCHEMA, MIGRATIONS)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def add(self, title: str, text: str, doc_date: str | None,
                  sha: str) -> dict[str, Any]:
        return await DocumentsRepository(self.db).create({
            "title": title, "original_filename": f"{sha[:6]}.pdf",
            "stored_filename": f"{sha[:6]}.pdf", "rel_path": f"X/{sha[:6]}.pdf",
            "file_kind": "pdf", "sha256": sha, "text_content": text,
            "doc_date": doc_date,
        })


class TestLiveSearch(LiveDbCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.add("Master Direction – KYC", "periodic updation of kyc",
                       "2026-07-15", "aa" * 32)
        await self.add("Draft circular on KYC", "a draft for consultation",
                       "2026-01-05", "bb" * 32)
        await self.add("FEMA ODI framework", "overseas direct investment rules",
                       None, "cc" * 32)

    async def test_type_ahead_prefix_finds_partial_words(self) -> None:
        hits = await SearchRepository(self.db).search(q="mast")
        self.assertEqual(hits["total"], 1)
        self.assertEqual(hits["items"][0]["title"], "Master Direction – KYC")

    async def test_phrase_search(self) -> None:
        hits = await SearchRepository(self.db).search(q='"periodic updation" ')
        self.assertEqual(hits["total"], 1)
        hits = await SearchRepository(self.db).search(q='"updation periodic" ')
        self.assertEqual(hits["total"], 0)  # order matters inside a phrase

    async def test_or_and_not(self) -> None:
        hits = await SearchRepository(self.db).search(q="fema OR kyc ")
        self.assertEqual(hits["total"], 3)
        hits = await SearchRepository(self.db).search(q="kyc -draft ")
        self.assertEqual(hits["total"], 1)
        self.assertNotIn("Draft", hits["items"][0]["title"])

    async def test_items_expose_id_for_navigation(self) -> None:
        hits = await SearchRepository(self.db).search(q="fema ")
        item = hits["items"][0]
        self.assertIn("id", item)                 # the fixed alias
        self.assertIn("original_filename", item)  # the UI's title fallback
        doc = await DocumentsRepository(self.db).get(item["id"])
        self.assertEqual(doc["title"], "FEMA ODI framework")

    async def test_sort_newest_and_oldest_with_null_dates_last(self) -> None:
        newest = await SearchRepository(self.db).search(q="kyc OR fema ", sort="newest")
        self.assertEqual([i["doc_date"] for i in newest["items"]],
                         ["2026-07-15", "2026-01-05", None])
        oldest = await SearchRepository(self.db).search(q="kyc OR fema ", sort="oldest")
        self.assertEqual([i["doc_date"] for i in oldest["items"]],
                         ["2026-01-05", "2026-07-15", None])

    async def test_bad_sort_value_rejected(self) -> None:
        with self.assertRaises(InvalidInputError):
            await SearchRepository(self.db).search(q="kyc", sort="cleverest")

    async def test_hostile_queries_never_raise_sql_errors(self) -> None:
        for nasty in ('((("; DROP TABLE--', 'a" OR "b', "NEAR(x, y)",
                      "col:value", "*", '"""', "x AND y", "-"):
            try:
                result = await SearchRepository(self.db).search(q=nasty)
                self.assertIsInstance(result["total"], int)
            except InvalidInputError:
                pass  # "nothing searchable" is an acceptable, friendly answer

    async def test_unsearchable_input_gets_friendly_error(self) -> None:
        with self.assertRaises(InvalidInputError):
            await SearchRepository(self.db).search(q="-only -negatives")


class TestSavedSearches(LiveDbCase):
    async def test_save_list_use_delete_roundtrip(self) -> None:
        repo = SavedSearchesRepository(self.db)
        first = await repo.save("KYC watch", "kyc -draft",
                                {"authority": "RBI", "sort": "newest", "empty": ""})
        self.assertEqual(first["filters"], {"authority": "RBI", "sort": "newest"})

        second = await repo.save("FEMA", "fema OR odi", {})
        listed = (await repo.list())["items"]
        self.assertEqual([r["name"] for r in listed], ["FEMA", "KYC watch"])

        used = await repo.touch(first["id"])
        self.assertEqual(used["query"], "kyc -draft")
        listed = (await repo.list())["items"]
        self.assertEqual(listed[0]["name"], "KYC watch")  # recency reordered

        await repo.delete(second["id"])
        listed = (await repo.list())["items"]
        self.assertEqual(len(listed), 1)
        with self.assertRaises(NotFoundError):
            await repo.delete(second["id"])

    async def test_same_name_updates_instead_of_erroring(self) -> None:
        repo = SavedSearchesRepository(self.db)
        first = await repo.save("Watch", "old query", {})
        updated = await repo.save("watch", "new query", {"sort": "newest"})
        self.assertEqual(updated["id"], first["id"])  # case-insensitive match
        self.assertEqual(updated["query"], "new query")
        self.assertEqual(len((await repo.list())["items"]), 1)

    async def test_validation(self) -> None:
        repo = SavedSearchesRepository(self.db)
        with self.assertRaises(InvalidInputError):
            await repo.save("  ", "kyc", {})
        with self.assertRaises(InvalidInputError):
            await repo.save("x" * 61, "kyc", {})
        with self.assertRaises(InvalidInputError):
            await repo.save("Name", "   ", {})


class TestMigrationOnOldDatabase(unittest.IsolatedAsyncioTestCase):
    async def test_old_database_gains_saved_searches_via_migration(self) -> None:
        import re
        import sqlite3

        with tempfile.TemporaryDirectory(prefix="lkm-mig-") as tmp:
            db_path = Path(tmp) / "old.sqlite3"
            # Imitate a pre-Stage-7 database: today's schema minus the table.
            old_schema = re.sub(
                r"CREATE TABLE IF NOT EXISTS saved_searches.*?;\n",
                "", SCHEMA.read_text(), flags=re.DOTALL,
            )
            conn = sqlite3.connect(db_path)
            conn.executescript(old_schema)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, description)"
                " VALUES (1, 'base schema')"
            )
            conn.commit()
            conn.close()

            db = Database(db_path, SCHEMA, MIGRATIONS)
            await db.connect()
            try:
                row = await db.fetch_one(
                    "SELECT name FROM sqlite_master WHERE name = 'saved_searches'"
                )
                self.assertIsNotNone(row)  # migration created it
                versions = [r["version"] for r in await db.fetch_all(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )]
                self.assertIn(2, versions)
                saved = await SavedSearchesRepository(db).save("m", "q", {})
                self.assertEqual(saved["name"], "m")
            finally:
                await db.close()


if __name__ == "__main__":
    unittest.main()
