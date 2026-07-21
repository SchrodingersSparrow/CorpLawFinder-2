"""Native text extraction (requirement 6, first half).

Answers one question per file: does it already contain real text, and if so,
what is it? PDFs with a text layer, Word files, spreadsheets and saved web
pages all yield their text here and become searchable immediately; a scanned
PDF yields (almost) nothing and is classified **OCR required** for the
engines in :mod:`app.services.ocr.engines`.

DOCX and XLSX are ZIP archives of XML, so the standard library reads them
outright — no python-docx, no openpyxl. Only PDF needs real libraries
(pdfplumber, with pypdf as fallback), imported lazily.
"""

from __future__ import annotations

import io
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


class ExtractorUnavailable(Exception):
    """The Python libraries needed to read this file type are missing."""

    PDF_HINT = (
        "Reading PDF text needs the backend packages. From the project "
        "folder run:  pip install -r backend/requirements.txt"
    )


# ---------------------------------------------------------------------------
# DOCX — word/document.xml inside the zip
# ---------------------------------------------------------------------------


def extract_docx_text(path: Path) -> str:
    """Paragraph text from a .docx (stdlib only). Empty string if unreadable."""
    try:
        with zipfile.ZipFile(path) as archive:
            xml_bytes = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError):
        return ""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return ""

    paragraphs: list[str] = []
    for paragraph in root.iterfind(".//{*}p"):
        pieces: list[str] = []
        for node in paragraph.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "t" and node.text:
                pieces.append(node.text)
            elif tag in ("tab",):
                pieces.append("\t")
            elif tag in ("br", "cr"):
                pieces.append("\n")
        text = "".join(pieces).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


# ---------------------------------------------------------------------------
# XLSX — xl/sharedStrings.xml holds the cell strings
# ---------------------------------------------------------------------------


def extract_xlsx_text(path: Path) -> str:
    """Cell text from a .xlsx (stdlib only). Numbers-only sheets yield ''."""
    try:
        with zipfile.ZipFile(path) as archive:
            xml_bytes = archive.read("xl/sharedStrings.xml")
    except (zipfile.BadZipFile, KeyError, OSError):
        return ""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return ""

    strings: list[str] = []
    for item in root.iterfind(".//{*}si"):
        text = "".join(t.text or "" for t in item.iterfind(".//{*}t")).strip()
        if text:
            strings.append(text)
    return "\n".join(strings)


# ---------------------------------------------------------------------------
# HTML — visible text only
# ---------------------------------------------------------------------------


class _TextCollector(HTMLParser):
    _SKIP = ("script", "style", "noscript")
    _BLOCK = ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs) -> None:  # noqa: ANN001
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag) -> None:  # noqa: ANN001
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data) -> None:  # noqa: ANN001
        if not self._skip_depth and data.strip():
            self.parts.append(data)


def extract_html_text(path: Path) -> str:
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    collector = _TextCollector()
    try:
        collector.feed(html)
    except Exception:  # noqa: BLE001 — broken markup should never crash
        pass
    lines = (line.strip() for line in "".join(collector.parts).splitlines())
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# PDF — the one format that needs real libraries (lazily imported)
# ---------------------------------------------------------------------------


def extract_pdf_text(path: Path) -> tuple[str, int]:
    """``(text, page_count)`` from a PDF's native text layer.

    Tries pdfplumber (better layout handling), falls back to pypdf. A scanned
    PDF returns nearly empty text — that emptiness IS the classification
    signal. Raises :class:`ExtractorUnavailable` when neither library exists.
    """
    try:
        return _pdf_text_pdfplumber(path)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — a corrupt file: give pypdf its chance
        pass
    try:
        return _pdf_text_pypdf(path)
    except ImportError as error:
        raise ExtractorUnavailable(ExtractorUnavailable.PDF_HINT) from error


def _pdf_text_pdfplumber(path: Path) -> tuple[str, int]:
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        count = len(pdf.pages)
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n\n".join(pages).strip(), count


def _pdf_text_pypdf(path: Path) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages).strip(), len(reader.pages)


# ---------------------------------------------------------------------------
# The classification itself (pure)
# ---------------------------------------------------------------------------


def is_searchable_text(text: str, page_count: int, min_chars_per_page: int) -> bool:
    """True when the extracted text is substantial enough to search.

    A scanned PDF usually extracts to nothing at all, or to a few stray
    characters per page; a born-digital circular extracts to hundreds.
    """
    pages = max(1, int(page_count or 1))
    return len((text or "").strip()) >= max(1, int(min_chars_per_page)) * pages
