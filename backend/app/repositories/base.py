"""Shared helpers for the repository layer.

Repositories are the ONLY layer that writes SQL (see docs/ARCHITECTURE.md).
They receive the shared :class:`app.core.database.Database` instance, return
plain ``dict`` objects (never framework types), and raise exceptions from
``app.core.errors``. Routers turn those dicts into Pydantic response models.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence

from app.core.database import Database

# Hard ceiling so a buggy client cannot ask for a million rows at once.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 25


def row_to_dict(row: Any | None) -> dict[str, Any] | None:
    """Convert an ``sqlite3.Row`` / ``aiosqlite.Row`` to a plain dict."""
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def clamp_paging(page: int | None, page_size: int | None) -> tuple[int, int, int]:
    """Normalise pagination inputs → ``(page, page_size, offset)``."""
    page = max(1, int(page or 1))
    page_size = min(MAX_PAGE_SIZE, max(1, int(page_size or DEFAULT_PAGE_SIZE)))
    return page, page_size, (page - 1) * page_size


def build_page(
    items: list[dict[str, Any]], total: int, page: int, page_size: int
) -> dict[str, Any]:
    """Assemble the standard paginated response body."""
    pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def safe_sort(
    sort: str | None,
    order: str | None,
    allowed: Mapping[str, str],
    default: str,
) -> str:
    """Build an ORDER BY clause from a whitelist.

    ``allowed`` maps public sort names to actual SQL expressions, so user
    input never reaches the SQL string directly.
    """
    column = allowed.get((sort or "").strip().lower(), allowed[default])
    direction = "ASC" if (order or "").strip().lower() == "asc" else "DESC"
    return f"ORDER BY {column} {direction}"


def where_sql(conditions: Sequence[str]) -> str:
    """Join condition fragments into a WHERE clause ('' when empty)."""
    return f"WHERE {' AND '.join(conditions)}" if conditions else ""


def dumps(value: Any) -> str | None:
    """JSON-encode for a ``*_json`` column (``None`` stays ``None``)."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def loads(text: str | None, fallback: Any = None) -> Any:
    """Decode a ``*_json`` column defensively — bad data never crashes a list view."""
    if text is None or text == "":
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


class BaseRepository:
    """Common constructor: every repository holds the shared Database."""

    def __init__(self, db: Database) -> None:
        self.db = db
