"""Application logging (req. 14).

Logs flow to three places:

1. Console — for development.
2. Rotating file ``<LKM_HOME>/logs/lkm.log`` — for support / debugging.
3. The ``logs`` database table — written by services through LogRepository
   (Stage 2) so the in-app Logs viewer works; the file handlers here do not
   touch the database.

Loggers are namespaced per category so filtering is trivial:
``lkm.system``, ``lkm.analysis``, ``lkm.download``, ``lkm.ocr``, ``lkm.ai``,
``lkm.search``. Use :func:`get_logger` to obtain one.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from app.models.enums import LogCategory

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    """Configure root 'lkm' logger with console + rotating file handlers.

    Safe to call more than once; subsequent calls are no-ops.
    """
    global _configured
    if _configured:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    root = logging.getLogger("lkm")
    root.setLevel(level.upper())

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "lkm.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Quieten noisy third-party loggers without hiding real problems.
    for noisy in ("urllib3", "httpx", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    root.info("Logging initialised (level=%s, dir=%s)", level.upper(), log_dir)


def get_logger(category: LogCategory | str = LogCategory.SYSTEM) -> logging.Logger:
    """Return the namespaced logger for a category, e.g. ``lkm.download``."""
    value = category.value if isinstance(category, LogCategory) else str(category)
    return logging.getLogger(f"lkm.{value}")
