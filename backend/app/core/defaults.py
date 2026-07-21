"""Central defaults for Legal Knowledge Manager.

This module is intentionally **standard-library only** so that
``backend/scripts/init_db.py`` can run before any third-party packages are
installed. The runtime configuration (``app/core/config.py``, pydantic-based)
imports from here so there is exactly one place where defaults live.

Settings below are seeded into the ``settings`` database table on first run
and become user-editable from the Settings screen. Values here are only the
*initial* values; the database copy wins afterwards.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Final

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Two ways this code runs (Stage 8):
#  * from source — paths hang off the project root, as always;
#  * frozen into lkm-backend.exe by PyInstaller inside the installed desktop
#    app — schema/migrations ship next to the executable (in its _internal
#    bundle dir), and user data defaults to a folder in the user's home
#    (the Electron shell normally sets LKM_HOME to Documents\Legal Knowledge
#    Manager before starting the backend).
FROZEN: Final[bool] = bool(getattr(sys, "frozen", False))

if FROZEN:
    _BUNDLE_DIR: Final[Path] = Path(
        getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)
    )
    PROJECT_ROOT: Final[Path] = Path(sys.executable).resolve().parent
    _DEFAULT_HOME: Final[Path] = Path.home() / "Legal Knowledge Manager"
    SCHEMA_PATH: Final[Path] = _BUNDLE_DIR / "db" / "schema.sql"
    MIGRATIONS_DIR: Final[Path] = _BUNDLE_DIR / "db" / "migrations"
else:
    # backend/app/core/defaults.py -> parents[3] == project root
    PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
    _DEFAULT_HOME: Final[Path] = PROJECT_ROOT / "data"
    SCHEMA_PATH: Final[Path] = PROJECT_ROOT / "backend" / "db" / "schema.sql"
    MIGRATIONS_DIR: Final[Path] = PROJECT_ROOT / "backend" / "db" / "migrations"

# All runtime data (database, downloaded library, logs) lives under one folder
# so the whole knowledge base can be backed up by copying a single directory.
# Override with the LKM_HOME environment variable if desired.
APP_HOME: Final[Path] = Path(os.environ.get("LKM_HOME", _DEFAULT_HOME)).resolve()

DB_DIR: Final[Path] = APP_HOME / "db"
DB_PATH: Final[Path] = DB_DIR / "lkm.sqlite3"
LIBRARY_ROOT: Final[Path] = APP_HOME / "library"
LOG_DIR: Final[Path] = APP_HOME / "logs"


SCHEMA_VERSION: Final[int] = 1

# Application version, reported by GET /api/health. Bumped once per stage.
APP_VERSION: Final[str] = "1.0.0"

# ---------------------------------------------------------------------------
# Backend server (loopback only — never exposed to the network)
# ---------------------------------------------------------------------------
BACKEND_HOST: Final[str] = "127.0.0.1"
BACKEND_PORT: Final[int] = 8756

# ---------------------------------------------------------------------------
# User-editable settings, seeded into the `settings` table (JSON-encoded).
# Keys use dotted namespaces: naming.*, folders.*, download.*, ocr.*, ai.*, ui.*
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: Final[dict[str, Any]] = {
    # --- Smart file naming (req. 4) --------------------------------------
    # Placeholders: {authority} {doc_type} {title} {date} {circular_no}
    "naming.template": "{authority} - {doc_type} - {title} - {date}",
    "naming.date_format": "%Y-%m-%d",
    "naming.max_length": 150,          # keep well under Windows path limits
    "naming.unknown_placeholder": "Unknown",

    # --- Folder organisation (req. 5) ------------------------------------
    # 'authority'  -> library/RBI/..., library/SEBI/...
    # 'topic'      -> library/Banking/..., library/FEMA/...
    "folders.rule": "authority",
    "folders.fallback": "Unsorted",

    # --- Website analysis (req. 2, Stage 4) --------------------------------
    # Start downloading automatically once analysis finds files on a page.
    "analysis.auto_download": True,
    # When to use the headless browser: 'auto' (only for JavaScript-built
    # pages such as the MCA portal), 'never', or 'always'.
    "analysis.use_browser": "auto",

    # --- Downloader (req. 3) ----------------------------------------------
    "download.max_concurrency": 3,
    "download.retries": 3,
    "download.retry_backoff_seconds": 5,
    "download.timeout_seconds": 120,
    "download.polite_delay_seconds": 1.5,   # gap between requests to one site
    "download.user_agent": "LegalKnowledgeManager/1.0 (personal research tool)",
    "download.allowed_extensions": ["pdf", "docx", "xlsx", "zip", "html", "htm"],

    # --- OCR (req. 6) ------------------------------------------------------
    "ocr.engine": "paddleocr",
    "ocr.fallback_engine": "tesseract",
    "ocr.languages": ["en"],
    # A PDF page with fewer extractable characters than this is treated as a
    # scanned image page when classifying Searchable vs OCR Required.
    "ocr.min_chars_per_page_searchable": 40,
    "ocr.render_dpi": 300,
    # Run OCR automatically when a downloaded PDF turns out to be scanned.
    "ocr.auto_run": True,
    # Where the external programs live. Empty = find them automatically
    # (PATH, then the usual Windows install locations).
    "ocr.poppler_path": "",
    "ocr.tesseract_path": "",

    # --- Local AI via Ollama (req. 8 & 9) ----------------------------------
    "ai.enabled": True,
    # Summarise automatically once a document's text is ready.
    "ai.auto_run": True,
    "ai.ollama_url": "http://127.0.0.1:11434",
    "ai.model": "qwen2.5:7b-instruct",
    "ai.small_model": "qwen2.5:3b-instruct",   # fallback for low-RAM machines
    "ai.max_input_chars": 12000,
    "ai.low_confidence_threshold": 0.55,        # below this -> Review Queue
    "ai.request_timeout_seconds": 180,

    # --- Search (req. 10) ---------------------------------------------------
    "search.snippet_tokens": 40,
    "search.page_size": 25,

    # --- Reference data used by the analyzer / classifier -------------------
    "authorities.known": [
        "RBI", "SEBI", "MCA", "IRDAI", "PFRDA", "IFSCA", "CBDT", "CBIC",
        "DPIIT", "FIU-IND", "NPCI", "DEA", "MeitY", "NCLT", "NCLAT", "GoI",
    ],
    "doc_types.known": [
        "Master Direction", "Master Circular", "Circular", "Notification",
        "Press Release", "FAQ", "Guidelines", "Act", "Rules", "Regulations",
        "Order", "Report", "Draft", "Speech",
    ],
    "topics.default": [
        "KYC", "AML", "FEMA", "Companies Act", "NBFC", "Deposits", "Lending",
        "Payments", "Securities", "Insolvency", "Banking Regulation",
        "Taxation", "Data Protection", "Outsourcing", "Digital Lending",
    ],

    # --- UI ------------------------------------------------------------------
    "ui.theme": "system",   # 'light' | 'dark' | 'system'
}
