"""Stage 4 tests: the streaming downloader against a real loopback server."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.downloader.http import (  # noqa: E402
    DownloadCancelled,
    DownloadFailure,
    download_to_file,
    filename_from_disposition,
)
from tests.stage4_server import PDF_A, PDF_B, FixtureServer  # noqa: E402


class DownloaderHttpTest(unittest.TestCase):
    server: FixtureServer

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FixtureServer().start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lkm-dl-")
        self.dest = Path(self.tmp.name) / "out.bin"
        self.addCleanup(self.tmp.cleanup)

    def fetch(self, path: str, **kwargs):
        kwargs.setdefault("user_agent", "test-agent")
        kwargs.setdefault("timeout", 10)
        kwargs.setdefault("backoff_seconds", 0.01)
        return download_to_file(self.server.url(path), self.dest, **kwargs)

    def test_streams_and_hashes_correctly(self) -> None:
        outcome = self.fetch("/a.pdf")
        self.assertEqual(self.dest.read_bytes(), PDF_A)
        self.assertEqual(outcome.size_bytes, len(PDF_A))
        self.assertEqual(outcome.sha256, hashlib.sha256(PDF_A).hexdigest())
        self.assertEqual(outcome.content_type, "application/pdf")
        self.assertEqual(outcome.http_status, 200)

    def test_follows_redirects_and_reports_final_url(self) -> None:
        outcome = self.fetch("/redirect")
        self.assertEqual(self.dest.read_bytes(), PDF_A)
        self.assertTrue(outcome.final_url.endswith("/a.pdf"))

    def test_retries_transient_500s_then_succeeds(self) -> None:
        self.server.flaky_hits = 0
        outcome = self.fetch("/flaky.pdf", max_retries=3)
        self.assertEqual(self.server.flaky_hits, 3)  # two failures + success
        self.assertEqual(self.dest.read_bytes(), PDF_B)
        self.assertEqual(outcome.http_status, 200)

    def test_gives_up_after_retry_budget(self) -> None:
        self.server.flaky_hits = -10  # needs 12 hits to succeed; budget is 2
        with self.assertRaises(DownloadFailure) as caught:
            self.fetch("/flaky.pdf", max_retries=1)
        self.assertEqual(caught.exception.http_status, 500)
        self.assertFalse(self.dest.exists())

    def test_404_fails_immediately_without_retries(self) -> None:
        before = self.server.flaky_hits
        with self.assertRaises(DownloadFailure) as caught:
            self.fetch("/gone.pdf", max_retries=5)
        self.assertEqual(caught.exception.http_status, 404)
        self.assertEqual(self.server.flaky_hits, before)  # flaky untouched
        self.assertFalse(self.dest.exists())

    def test_unreachable_host_raises_cleanly(self) -> None:
        with self.assertRaises(DownloadFailure):
            download_to_file(
                "http://127.0.0.1:9/never", self.dest,
                user_agent="t", timeout=2, max_retries=0, backoff_seconds=0.01,
            )

    def test_content_disposition_hint(self) -> None:
        outcome = self.fetch("/named")
        self.assertEqual(outcome.filename_hint, "Circular No. 12 of 2026.pdf")

    def test_cancel_before_start(self) -> None:
        with self.assertRaises(DownloadCancelled):
            self.fetch("/a.pdf", cancel_check=lambda: True)
        self.assertFalse(self.dest.exists())


class DispositionParsing(unittest.TestCase):
    def test_plain_and_quoted(self) -> None:
        self.assertEqual(
            filename_from_disposition('attachment; filename="a b.pdf"'), "a b.pdf"
        )
        self.assertEqual(
            filename_from_disposition("inline; filename=report.pdf"), "report.pdf"
        )

    def test_rfc5987_utf8(self) -> None:
        self.assertEqual(
            filename_from_disposition("attachment; filename*=UTF-8''%E0%A4%95.pdf"),
            "क.pdf",
        )

    def test_path_components_are_stripped(self) -> None:
        self.assertEqual(
            filename_from_disposition('attachment; filename="..\\evil\\x.pdf"'),
            "x.pdf",
        )

    def test_absent(self) -> None:
        self.assertIsNone(filename_from_disposition(None))
        self.assertIsNone(filename_from_disposition("attachment"))


if __name__ == "__main__":
    unittest.main()
