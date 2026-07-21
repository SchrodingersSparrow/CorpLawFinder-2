"""Stage 6 tests: the AI service end-to-end, plus the stdlib Ollama client.

Real database and files; the Ollama calls are injected fakes — except the
client test at the bottom, which runs against a loopback fake Ollama server
to prove the real HTTP/JSON code path.
"""

from __future__ import annotations

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
from app.repositories.ai import AiRepository  # noqa: E402
from app.repositories.documents import DocumentsRepository  # noqa: E402
from app.repositories.metadata import MetadataRepository  # noqa: E402
from app.repositories.review import ReviewRepository  # noqa: E402
from app.repositories.search import SearchRepository  # noqa: E402
from app.repositories.settings import SettingsRepository  # noqa: E402
from app.services.ai import client  # noqa: E402
from app.services.ai.service import AiService  # noqa: E402

SCHEMA = BACKEND_DIR / "db" / "schema.sql"
MIGRATIONS = BACKEND_DIR / "db" / "migrations"

MODEL_ANSWER = json.dumps({
    "one_line_summary": "RBI tightens periodic KYC updation timelines.",
    "detailed_summary": "Regulated entities must complete re-KYC within…",
    "title": "Master Direction – KYC Direction, 2016",
    "authority": "RBI",
    "doc_type": "Master Direction",
    "doc_date": "2026-07-15",
    "circular_no": "DOR.AML.REC.12/2026-27",
    "language": "en",
    "topics": ["Banking – KYC/AML", "Compliance"],
    "keywords": ["KYC", "re-KYC"],
    "confidence": 0.9,
})


class StubQueue:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, dict[str, Any], str | None]] = []

    def has_handler(self, task_type: str) -> bool:
        return True

    def submit(self, task_type, payload=None, dedupe_key=None):
        self.submitted.append((task_type, payload or {}, dedupe_key))
        return Task(id="stub", task_type=task_type, payload=payload or {})


def make_ctx() -> TaskContext:
    return TaskContext(Task(id="t", task_type="ai_summarize", payload={}))


class AiCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lkm-ai-")
        base = Path(self.tmp.name)
        self.library = base / "library"
        (self.library / "Unsorted").mkdir(parents=True)
        self.db = Database(base / "t.sqlite3", SCHEMA, MIGRATIONS)
        await self.db.connect()
        self.queue = StubQueue()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def add_document(self, *, text: str | None = "Regulated entities shall…",
                           folder: str = "Unsorted", **fields) -> dict[str, Any]:
        (self.library / folder).mkdir(parents=True, exist_ok=True)
        (self.library / folder / "doc.pdf").write_bytes(b"%PDF x")
        doc = await DocumentsRepository(self.db).create({
            "original_filename": "doc.pdf",
            "stored_filename": "doc.pdf",
            "rel_path": f"{folder}/doc.pdf",
            "file_kind": "pdf",
            "sha256": "0a" * 32,
            "text_content": text,
            **fields,
        })
        return doc

    def service(self, answer: str = MODEL_ANSWER,
                installed: set[str] | None = None, **overrides) -> AiService:
        calls: dict[str, Any] = {"generate": []}
        self.calls = calls

        def list_models(url, timeout):
            return installed if installed is not None else {
                "qwen2.5:7b-instruct", "qwen2.5:3b-instruct"
            }

        def generate(url, model, prompt, timeout):
            calls["generate"].append({"model": model, "prompt": prompt})
            if isinstance(answer, Exception):
                raise answer
            return answer

        defaults = dict(
            library_root=self.library,
            list_models=list_models,
            generate_json=generate,
        )
        defaults.update(overrides)
        return AiService(self.db, self.queue, **defaults)


class TestApply(AiCase):
    async def test_full_apply_summary_metadata_topics_index(self) -> None:
        doc = await self.add_document(title=None)
        await self.service().handle({"document_id": doc["id"]}, make_ctx())

        runs = await AiRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(len(runs), 1)
        run = await AiRepository(self.db).get(runs[0]["id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["confidence"], 0.9)
        self.assertIn("KYC", run["one_line_summary"])
        self.assertEqual(run["topics"],  # repo decodes topics_json for callers
                         ["Banking – KYC/AML", "Compliance"])

        # Canonical promotion filled the blanks…
        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["title"], "Master Direction – KYC Direction, 2016")
        self.assertEqual(row["authority"], "RBI")
        self.assertEqual(row["doc_date"], "2026-07-15")

        # …the audit trail knows who said so…
        meta = {m["field"]: m for m in
                await MetadataRepository(self.db).list_for_document(doc["id"])}
        self.assertEqual(meta["title"]["extractor"], "ai")
        self.assertEqual(meta["circular_no"]["value"], "DOR.AML.REC.12/2026-27")

        # …topics became removable AI tags…
        tags = await DocumentsRepository(self.db).tags_for(doc["id"])
        names = {t["name"] for t in tags}
        self.assertIn("Banking – KYC/AML", names)
        self.assertTrue(all(t["kind"] == "topic" for t in tags))

        # …and the summary is searchable.
        hits = await SearchRepository(self.db).search(q="timelines")
        self.assertEqual(hits["total"], 1)
        # High confidence → no review item.
        self.assertEqual((await ReviewRepository(self.db).list())["total"], 0)

    async def test_existing_values_are_never_overwritten(self) -> None:
        doc = await self.add_document(
            title="My own title", authority="SEBI", doc_type=None
        )
        await self.service().handle({"document_id": doc["id"]}, make_ctx())
        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["title"], "My own title")     # user's value wins
        self.assertEqual(row["authority"], "SEBI")         # untouched
        self.assertEqual(row["doc_type"], "Master Direction")  # blank filled
        meta = {m["field"]: m for m in
                await MetadataRepository(self.db).list_for_document(doc["id"])}
        self.assertEqual(meta["title"]["value"],
                         "Master Direction – KYC Direction, 2016")  # candidate kept

    async def test_low_confidence_lands_in_review(self) -> None:
        answer = json.loads(MODEL_ANSWER)
        answer["confidence"] = 0.3
        doc = await self.add_document()
        await self.service(json.dumps(answer)).handle(
            {"document_id": doc["id"]}, make_ctx()
        )
        review = await ReviewRepository(self.db).list()
        self.assertEqual(review["total"], 1)
        self.assertEqual(review["items"][0]["category"], "low_ai_confidence")
        self.assertIn("30%", review["items"][0]["detail"])

    async def test_ocr_text_is_used_when_no_native_text(self) -> None:
        doc = await self.add_document(text=None)
        from app.repositories.ocr import OcrRepository

        run_id = await OcrRepository(self.db).create_run(doc["id"], "tesseract")
        await OcrRepository(self.db).mark(
            run_id, "completed", text_content="Scanned circular about margins."
        )
        await self.service().handle({"document_id": doc["id"]}, make_ctx())
        prompt = self.calls["generate"][0]["prompt"]
        self.assertIn("Scanned circular about margins.", prompt)

    async def test_topic_rule_moves_file_into_topic_folder(self) -> None:
        await SettingsRepository(self.db).set_many({"folders.rule": "topic"})
        doc = await self.add_document()
        await self.service().handle({"document_id": doc["id"]}, make_ctx())

        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertTrue(row["rel_path"].startswith("Banking – KYC AML/"),
                        row["rel_path"])  # sanitized topic folder
        moved = self.library / row["rel_path"]
        self.assertTrue(moved.is_file())
        self.assertFalse((self.library / "Unsorted" / "doc.pdf").exists())

    async def test_authority_rule_leaves_file_alone(self) -> None:
        doc = await self.add_document()
        await self.service().handle({"document_id": doc["id"]}, make_ctx())
        row = await DocumentsRepository(self.db).get(doc["id"])
        self.assertEqual(row["rel_path"], "Unsorted/doc.pdf")


class TestFailures(AiCase):
    async def test_no_text_yet_fails_with_guidance(self) -> None:
        doc = await self.add_document(text=None)
        await self.service().handle({"document_id": doc["id"]}, make_ctx())
        run = (await AiRepository(self.db).list_for_document(doc["id"]))[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("run OCR first", run["error_message"])
        self.assertEqual((await ReviewRepository(self.db).list())["total"], 1)

    async def test_ollama_down_fails_with_install_hint(self) -> None:
        def down(url, timeout):
            raise client.OllamaUnavailable(client.OLLAMA_HINT)

        doc = await self.add_document()
        await self.service(list_models=down).handle(
            {"document_id": doc["id"]}, make_ctx()
        )
        run = (await AiRepository(self.db).list_for_document(doc["id"]))[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("ollama.com", run["error_message"])
        review = await ReviewRepository(self.db).list()
        self.assertEqual(review["items"][0]["category"], "other")

    async def test_missing_model_falls_back_to_small_model(self) -> None:
        doc = await self.add_document()
        await self.service(installed={"qwen2.5:3b-instruct"}).handle(
            {"document_id": doc["id"]}, make_ctx()
        )
        self.assertEqual(self.calls["generate"][0]["model"], "qwen2.5:3b-instruct")
        run = (await AiRepository(self.db).list_for_document(doc["id"]))[0]
        self.assertEqual(run["status"], "completed")

    async def test_no_model_at_all_fails_with_pull_command(self) -> None:
        doc = await self.add_document()
        await self.service(installed=set()).handle(
            {"document_id": doc["id"]}, make_ctx()
        )
        run = (await AiRepository(self.db).list_for_document(doc["id"]))[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("ollama pull", run["error_message"])

    async def test_unreadable_answer_fails_honestly(self) -> None:
        doc = await self.add_document()
        await self.service("I refuse to answer in JSON.").handle(
            {"document_id": doc["id"]}, make_ctx()
        )
        run = (await AiRepository(self.db).list_for_document(doc["id"]))[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("could not be read", run["error_message"])

    async def test_disabled_setting_stops_politely(self) -> None:
        await SettingsRepository(self.db).set_many({"ai.enabled": False})
        doc = await self.add_document()
        await self.service().handle({"document_id": doc["id"]}, make_ctx())
        run = (await AiRepository(self.db).list_for_document(doc["id"]))[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("turned off", run["error_message"])
        self.assertEqual(self.calls["generate"], [])  # model never called

    async def test_resume_payload_reuses_run_row(self) -> None:
        doc = await self.add_document()
        run_id = await AiRepository(self.db).create_run(doc["id"], "qwen2.5:7b-instruct")
        await self.service().handle(
            {"summary_id": run_id, "document_id": doc["id"],
             "model": "qwen2.5:7b-instruct"},
            make_ctx(),
        )
        runs = await AiRepository(self.db).list_for_document(doc["id"])
        self.assertEqual(len(runs), 1)  # no second row
        self.assertEqual((await AiRepository(self.db).get(run_id))["status"],
                         "completed")

    async def test_queue_run_respects_settings_and_submits(self) -> None:
        doc = await self.add_document()
        service = self.service()
        run_id = await service.queue_run(doc["id"])
        self.assertIsNotNone(run_id)
        task_type, payload, dedupe = self.queue.submitted[0]
        self.assertEqual(task_type, "ai_summarize")
        self.assertEqual(payload,
                         {"summary_id": run_id, "document_id": doc["id"],
                          "model": "qwen2.5:7b-instruct"})
        self.assertEqual(dedupe, f"ai:{run_id}")

        await SettingsRepository(self.db).set_many({"ai.auto_run": False})
        self.assertIsNone(await service.queue_run(doc["id"]))


class TestClientAgainstFakeOllama(unittest.TestCase):
    """The real stdlib client against a loopback fake Ollama."""

    def setUp(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        answers = self.answers = {}

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/api/tags":
                    self._reply({"models": [{"name": "qwen2.5:7b-instruct"}]})
                else:
                    self.send_response(404); self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                request = json.loads(self.rfile.read(length))
                answers["last_request"] = request
                if request["model"] == "missing:latest":
                    self._reply({"error": "model 'missing:latest' not found"})
                else:
                    self._reply({"response": "{\"ok\": true}", "done": True})

            def log_message(self, *a) -> None:  # noqa: ANN002
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_list_models_includes_short_names(self) -> None:
        models = client.list_models(self.base, timeout=5)
        self.assertIn("qwen2.5:7b-instruct", models)
        self.assertIn("qwen2.5", models)

    def test_generate_sends_json_mode_and_returns_response(self) -> None:
        out = client.generate_json(self.base, "qwen2.5:7b-instruct", "hi", timeout=5)
        self.assertEqual(out, '{"ok": true}')
        request = self.answers["last_request"]
        self.assertEqual(request["format"], "json")
        self.assertIs(request["stream"], False)

    def test_model_missing_raises_pull_hint(self) -> None:
        with self.assertRaises(client.ModelMissing) as caught:
            client.generate_json(self.base, "missing:latest", "hi", timeout=5)
        self.assertIn("ollama pull missing:latest", str(caught.exception))

    def test_unreachable_raises_install_hint(self) -> None:
        with self.assertRaises(client.OllamaUnavailable) as caught:
            client.list_models("http://127.0.0.1:9", timeout=1)
        self.assertIn("ollama.com", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
