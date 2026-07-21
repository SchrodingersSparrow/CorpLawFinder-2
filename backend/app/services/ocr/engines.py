"""OCR engines for scanned documents (requirement 6, second half).

Two engines, as configured in Settings:

* **PaddleOCR** — better accuracy, heavy install (``pip install paddlepaddle
  paddleocr``), used when present.
* **Tesseract** — the dependable fallback: install the UB Mannheim build on
  Windows and it just works.

Rendering PDF pages to images uses pdf2image, which needs the Poppler
programs. On Windows neither Poppler nor Tesseract lives on PATH by default,
so this module auto-detects their usual install locations and lets Settings
override (``ocr.poppler_path`` / ``ocr.tesseract_path``). Every missing piece
raises :class:`EngineUnavailable` carrying the exact install steps.

Everything imports lazily: the backend runs fine before any of this is
installed, and the service layer injects fakes for tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterable

POPPLER_HINT = (
    "Rendering PDF pages needs Poppler. On Windows: download the latest "
    "'Release' zip from https://github.com/oschwartz10612/poppler-windows/releases , "
    "unzip it somewhere permanent (e.g. C:\\poppler), then either add its "
    "Library\\bin folder to PATH or paste that folder into Settings → OCR → "
    "'Poppler location'."
)
TESSERACT_HINT = (
    "Tesseract is not installed. On Windows: run the installer from "
    "https://github.com/UB-Mannheim/tesseract/wiki (default location is "
    "fine), then restart the app. If installed somewhere unusual, paste the "
    "path to tesseract.exe into Settings → OCR → 'Tesseract location'."
)
PADDLE_HINT = (
    "PaddleOCR is not installed (it is optional — Tesseract is used "
    "instead). To install it:  pip install paddlepaddle paddleocr"
)

#: Where the UB Mannheim installer puts tesseract.exe by default.
_TESSERACT_DEFAULT_WINDOWS = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]

#: settings ocr.languages use short codes; Tesseract wants ISO 639-2.
_TESSERACT_LANGUAGES = {"en": "eng", "hi": "hin", "mr": "mar", "ta": "tam",
                        "te": "tel", "bn": "ben", "gu": "guj", "kn": "kan",
                        "ml": "mal", "pa": "pan", "ur": "urd"}


class EngineUnavailable(Exception):
    """A needed program or package is missing; the message says what to do."""


# ---------------------------------------------------------------------------
# Locating external programs
# ---------------------------------------------------------------------------


def find_poppler(configured: str | None) -> str | None:
    """Poppler's bin folder, or None to rely on PATH.

    Raises :class:`EngineUnavailable` when Poppler is nowhere to be found.
    """
    if configured:
        folder = Path(configured)
        if (folder / "pdftoppm.exe").exists() or (folder / "pdftoppm").exists():
            return str(folder)
        raise EngineUnavailable(
            f"Settings point Poppler at {configured!r}, but pdftoppm was not "
            "found there. " + POPPLER_HINT
        )
    if shutil.which("pdftoppm"):
        return None  # on PATH — pdf2image finds it by itself
    raise EngineUnavailable(POPPLER_HINT)


def find_tesseract(configured: str | None) -> str:
    """Full path to the tesseract executable."""
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path)
        raise EngineUnavailable(
            f"Settings point Tesseract at {configured!r}, but nothing is "
            "there. " + TESSERACT_HINT
        )
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    for candidate in _TESSERACT_DEFAULT_WINDOWS:
        if candidate.is_file():
            return str(candidate)
    raise EngineUnavailable(TESSERACT_HINT)


# ---------------------------------------------------------------------------
# Rendering PDF pages
# ---------------------------------------------------------------------------


def render_pdf_pages(
    path: Path, *, dpi: int, poppler_path: str | None
) -> Iterable[Any]:
    """PIL images for every page of the PDF (blocking — run in a thread)."""
    try:
        from pdf2image import convert_from_path
    except ImportError as error:
        raise EngineUnavailable(
            "The pdf2image package is missing. From the project folder run:  "
            "pip install -r backend/requirements.txt"
        ) from error

    poppler = find_poppler(poppler_path)
    try:
        return convert_from_path(
            str(path), dpi=int(dpi), poppler_path=poppler, fmt="png"
        )
    except Exception as error:  # noqa: BLE001 — poppler missing/broken mid-call
        message = str(error)
        if "poppler" in message.lower() or "pdftoppm" in message.lower():
            raise EngineUnavailable(POPPLER_HINT) from error
        raise


# ---------------------------------------------------------------------------
# Reading one page
# ---------------------------------------------------------------------------


def ocr_page_tesseract(
    image: Any, *, languages: list[str], tesseract_path: str | None
) -> tuple[str, float | None]:
    """``(text, confidence)`` for one page via Tesseract."""
    try:
        import pytesseract
    except ImportError as error:
        raise EngineUnavailable(
            "The pytesseract package is missing. From the project folder "
            "run:  pip install -r backend/requirements.txt"
        ) from error

    pytesseract.pytesseract.tesseract_cmd = find_tesseract(tesseract_path)
    lang = "+".join(_TESSERACT_LANGUAGES.get(code, code) for code in languages) or "eng"
    try:
        text = pytesseract.image_to_string(image, lang=lang)
    except pytesseract.TesseractNotFoundError as error:
        raise EngineUnavailable(TESSERACT_HINT) from error
    except pytesseract.TesseractError as error:
        # Wrong language pack etc. — retry with English before giving up.
        if lang != "eng":
            text = pytesseract.image_to_string(image, lang="eng")
        else:
            raise EngineUnavailable(f"Tesseract failed: {error}") from error
    return text, None  # image_to_string reports no confidence


_paddle_cache: dict[str, Any] = {}


def ocr_page_paddleocr(
    image: Any, *, languages: list[str]
) -> tuple[str, float | None]:
    """``(text, confidence)`` for one page via PaddleOCR."""
    try:
        from paddleocr import PaddleOCR
    except ImportError as error:
        raise EngineUnavailable(PADDLE_HINT) from error

    lang = (languages[0] if languages else "en") or "en"
    engine = _paddle_cache.get(lang)
    if engine is None:
        engine = PaddleOCR(lang=lang, use_angle_cls=True, show_log=False)
        _paddle_cache[lang] = engine

    import numpy

    result = engine.ocr(numpy.asarray(image.convert("RGB")), cls=True)
    lines: list[str] = []
    confidences: list[float] = []
    for block in result or []:
        for entry in block or []:
            try:
                text, confidence = entry[1][0], float(entry[1][1])
            except (TypeError, IndexError, ValueError):
                continue
            if text:
                lines.append(text)
                confidences.append(confidence)
    average = sum(confidences) / len(confidences) if confidences else None
    return "\n".join(lines), average
