"""Repository for the ``logs`` table (req. 14) + the ``log_event`` helper.

File/console logging is configured in ``app/core/logging_config.py``; this
table exists so the in-app Logs screen can show, filter and scroll history
without touching the file system. Services call :func:`log_event` at
meaningful moments (download finished, OCR failed…), which writes BOTH to the
table and to the normal Python logger.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.database import Database
from app.core.errors import InvalidInputError
from app.core.logging_config import get_logger
from app.models.enums import LogCategory
from app.repositories.base import BaseRepository, loads, rows_to_dicts

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_MAX_LIMIT = 200


class LogsRepository(BaseRepository):
    async def add(
        self,
        level: str,
        category: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> int:
        level = level.upper()
        if level not in _LEVELS:
            raise InvalidInputError(f"Unknown log level {level!r}")
        if category not in LogCategory:
            raise InvalidInputError(f"Unknown log category {category!r}")
        return await self.db.insert(
            "INSERT INTO logs (level, category, message, context_json)"
            " VALUES (?, ?, ?, ?)",
            (
                level,
                category,
                message,
                json.dumps(context, ensure_ascii=False, default=str)
                if context
                else None,
            ),
        )

    async def list(
        self,
        category: str | None = None,
        level: str | None = None,
        limit: int | None = None,
        before_id: int | None = None,
    ) -> dict[str, Any]:
        """Newest-first cursor pagination (pass ``next_before_id`` back in)."""
        limit = min(_MAX_LIMIT, max(1, int(limit or 50)))
        conditions: list[str] = []
        params: list[Any] = []
        if category:
            if category not in LogCategory:
                raise InvalidInputError(f"Unknown log category {category!r}")
            conditions.append("category = ?")
            params.append(category)
        if level:
            level = level.upper()
            if level not in _LEVELS:
                raise InvalidInputError(f"Unknown log level {level!r}")
            conditions.append("level = ?")
            params.append(level)
        if before_id is not None:
            conditions.append("id < ?")
            params.append(before_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await self.db.fetch_all(
            f"SELECT * FROM logs {where} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        )
        items = rows_to_dicts(rows)
        for item in items:
            item["context"] = loads(item.pop("context_json", None))
        next_before = items[-1]["id"] if len(items) == limit else None
        return {"items": items, "next_before_id": next_before}


async def log_event(
    db: Database,
    category: LogCategory | str,
    level: str,
    message: str,
    **context: Any,
) -> None:
    """Write one event to the logs table AND the normal Python logger.

    Never raises — a broken log write must not take down the operation that
    was being logged.
    """
    cat = str(category)
    try:
        await LogsRepository(db).add(level, cat, message, context or None)
    except Exception:  # noqa: BLE001 - logging must never break the caller
        get_logger(cat).exception("Could not write log row: %s", message)
        return
    get_logger(cat).log(
        {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}.get(level.upper(), 20),
        "%s%s",
        message,
        f" | {context}" if context else "",
    )
