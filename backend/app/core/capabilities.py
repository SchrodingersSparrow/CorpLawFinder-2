"""Report which optional features are usable on this machine.

The app degrades gracefully (docs/ARCHITECTURE.md): missing Playwright means
static fetching, missing PaddleOCR means Tesseract, missing Ollama means AI
features are switched off with a clear message — never a crash. This module
is the single place that checks what is installed; the UI reads it through
``GET /api/capabilities`` to decide which buttons to enable.

Checks are import-probes only (no network, instant). Whether the Ollama
*server* is actually running is a live question answered in Stage 6.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from typing import Any

from app.core import defaults


def _installed(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):  # pragma: no cover - broken package edge
        return False


def _fts5_available() -> bool:
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False


def _feature(
    stage: int, available: bool, missing: list[str], note: str
) -> dict[str, Any]:
    return {"stage": stage, "available": available, "missing": missing, "note": note}


def probe() -> dict[str, Any]:
    """Return the capability map used by the UI and the health endpoint."""
    web = {name: _installed(name) for name in ("requests", "bs4", "playwright")}
    ocr = {
        name: _installed(name)
        for name in ("pdfplumber", "pypdf", "pdf2image", "pytesseract", "paddleocr")
    }
    httpx_ok = _installed("httpx")

    analysis_missing = [n for n in ("requests", "bs4") if not web[n]]
    ocr_core_missing = [n for n in ("pdfplumber", "pypdf") if not ocr[n]]
    ocr_engines = [n for n in ("paddleocr", "pytesseract") if ocr[n]]

    return {
        "app_version": defaults.APP_VERSION,
        "fts5": _fts5_available(),
        "features": {
            "backend_api": _feature(2, True, [], "Core API (this stage) is always on."),
            "website_analysis": _feature(
                4,
                not analysis_missing,
                analysis_missing,
                "Playwright adds JavaScript-heavy sites; static pages work "
                "with requests + BeautifulSoup alone."
                + ("" if web["playwright"] else " Playwright not installed yet."),
            ),
            "downloading": _feature(
                4,
                web["requests"],
                [] if web["requests"] else ["requests"],
                "Polite multi-file downloader.",
            ),
            "ocr": _feature(
                5,
                not ocr_core_missing and bool(ocr_engines),
                ocr_core_missing
                + ([] if ocr_engines else ["paddleocr or pytesseract"]),
                f"Engines found: {', '.join(ocr_engines) or 'none'}. "
                "PaddleOCR preferred, Tesseract is the fallback.",
            ),
            "local_ai": _feature(
                6,
                httpx_ok,
                [] if httpx_ok else ["httpx"],
                "Talks to Ollama at "
                f"{defaults.DEFAULT_SETTINGS['ai.ollama_url']}; whether the "
                "Ollama server is running is checked live in Stage 6.",
            ),
            "search": _feature(
                2,
                _fts5_available(),
                [] if _fts5_available() else ["SQLite built with FTS5"],
                "Full-text search over titles, text, OCR output, summaries "
                "and tags. Richer search screen arrives in Stage 7.",
            ),
        },
    }
