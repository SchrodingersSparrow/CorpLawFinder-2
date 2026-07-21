"""Stage 5 tests: native text extraction (stdlib formats) + classification."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.ocr.extract import (  # noqa: E402
    extract_docx_text,
    extract_html_text,
    extract_xlsx_text,
    is_searchable_text,
)

DOCX_DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Master Direction on KYC</w:t></w:r></w:p>
    <w:p><w:r><w:t>Applicable to </w:t></w:r><w:r><w:t>all banks</w:t></w:r></w:p>
    <w:p><w:r><w:t>Column A</w:t><w:tab/><w:t>Column B</w:t></w:r></w:p>
    <w:p></w:p>
  </w:body>
</w:document>
"""

XLSX_SHARED_STRINGS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3">
  <si><t>Compliance calendar</t></si>
  <si><r><t>Quarterly </t></r><r><t>filing</t></r></si>
  <si><t>  </t></si>
</sst>
"""


def make_docx(directory: Path) -> Path:
    path = directory / "sample.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", DOCX_DOCUMENT_XML)
    return path


def make_xlsx(directory: Path) -> Path:
    path = directory / "sample.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/sharedStrings.xml", XLSX_SHARED_STRINGS)
    return path


class TestExtraction(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lkm-extract-")
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_docx_paragraphs_runs_and_tabs(self) -> None:
        text = extract_docx_text(make_docx(self.dir))
        lines = text.splitlines()
        self.assertEqual(lines[0], "Master Direction on KYC")
        self.assertEqual(lines[1], "Applicable to all banks")  # runs joined
        self.assertIn("Column A\tColumn B", lines[2])          # tab preserved
        self.assertEqual(len(lines), 3)                        # empty p dropped

    def test_docx_garbage_returns_empty(self) -> None:
        bad = self.dir / "not-a.docx"
        bad.write_bytes(b"this is not a zip archive at all")
        self.assertEqual(extract_docx_text(bad), "")

    def test_xlsx_shared_strings(self) -> None:
        text = extract_xlsx_text(make_xlsx(self.dir))
        self.assertEqual(
            text.splitlines(),
            ["Compliance calendar", "Quarterly filing"],  # rich runs joined,
        )                                                  # blank cell dropped

    def test_xlsx_without_shared_strings_is_empty(self) -> None:
        path = self.dir / "numbers.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
        self.assertEqual(extract_xlsx_text(path), "")

    def test_html_visible_text_only(self) -> None:
        page = self.dir / "saved.html"
        page.write_text(
            "<html><head><title>T</title><style>.x{color:red}</style>"
            "<script>var a=1;</script></head>"
            "<body><h1>Circular</h1><p>First para.</p>"
            "<div>Second <b>bold</b> para.</div></body></html>",
            encoding="utf-8",
        )
        text = extract_html_text(page)
        self.assertIn("Circular", text)
        self.assertIn("First para.", text)
        self.assertIn("Second bold para.", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("var a=1", text)


class TestClassification(unittest.TestCase):
    def test_threshold_scales_with_pages(self) -> None:
        rich = "x" * 500
        self.assertTrue(is_searchable_text(rich, page_count=1, min_chars_per_page=40))
        self.assertTrue(is_searchable_text(rich, page_count=10, min_chars_per_page=40))
        self.assertFalse(is_searchable_text(rich, page_count=20, min_chars_per_page=40))

    def test_scanned_pdf_signature(self) -> None:
        # Scanned PDFs extract to nothing or to stray characters.
        self.assertFalse(is_searchable_text("", 5, 40))
        self.assertFalse(is_searchable_text("  \n ~ 3 .", 5, 40))

    def test_zero_pages_treated_as_one(self) -> None:
        self.assertTrue(is_searchable_text("y" * 50, page_count=0, min_chars_per_page=40))


if __name__ == "__main__":
    unittest.main()
