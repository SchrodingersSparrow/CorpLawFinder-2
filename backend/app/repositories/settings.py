"""Repository for the ``settings`` table (user-editable configuration).

The database only stores keys the user actually changed; everything else
falls back to ``defaults.DEFAULT_SETTINGS``. That way a future stage can add
new settings (or improve a default) without migrating anyone's database.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core import defaults
from app.core.errors import InvalidInputError
from app.repositories.base import BaseRepository, loads


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, int):
        return "whole number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    return type(value).__name__


def _type_matches(default: Any, value: Any) -> bool:
    """Is ``value`` the same kind of thing as the default? (bool ≠ int!)"""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list)
    return True


class SettingsRepository(BaseRepository):
    async def get_all(self) -> dict[str, Any]:
        """Merged view: defaults overlaid with stored overrides."""
        rows = await self.db.fetch_all("SELECT key, value_json FROM settings")
        stored = {row["key"]: loads(row["value_json"]) for row in rows}
        values = dict(defaults.DEFAULT_SETTINGS)
        overridden = []
        for key, value in stored.items():
            if key in values and value != defaults.DEFAULT_SETTINGS.get(key):
                overridden.append(key)
            values[key] = value
        return {
            "values": values,
            "defaults": dict(defaults.DEFAULT_SETTINGS),
            "overridden": sorted(overridden),
        }

    async def get_value(self, key: str) -> Any:
        if key not in defaults.DEFAULT_SETTINGS:
            raise InvalidInputError(f"Unknown setting {key!r}")
        row = await self.db.fetch_one(
            "SELECT value_json FROM settings WHERE key = ?", (key,)
        )
        if row is None:
            return defaults.DEFAULT_SETTINGS[key]
        return loads(row["value_json"], defaults.DEFAULT_SETTINGS[key])

    async def set_many(self, values: dict[str, Any]) -> dict[str, Any]:
        """Validate + store several settings atomically; returns merged view."""
        if not values:
            raise InvalidInputError("No settings were provided.")
        for key, value in values.items():
            if key not in defaults.DEFAULT_SETTINGS:
                raise InvalidInputError(
                    f"Unknown setting {key!r}. Valid keys are listed by GET /api/settings."
                )
            default = defaults.DEFAULT_SETTINGS[key]
            if not _type_matches(default, value):
                raise InvalidInputError(
                    f"Setting {key!r} must be a {_type_name(default)} "
                    f"(got {_type_name(value)})."
                )

        def job(conn: sqlite3.Connection) -> None:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO settings (key, value_json) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = datetime('now')
                    """,
                    (key, json.dumps(value, ensure_ascii=False)),
                )

        await self.db.run(job)
        return await self.get_all()

    async def reset(self, key: str) -> dict[str, Any]:
        if key not in defaults.DEFAULT_SETTINGS:
            raise InvalidInputError(f"Unknown setting {key!r}")
        await self.db.execute("DELETE FROM settings WHERE key = ?", (key,))
        # Re-seed the default row so init_db-style inspection still sees it.
        await self.db.execute(
            "INSERT OR IGNORE INTO settings (key, value_json) VALUES (?, ?)",
            (key, json.dumps(defaults.DEFAULT_SETTINGS[key])),
        )
        return await self.get_all()

    async def reset_all(self) -> dict[str, Any]:
        def job(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM settings")
            for key, value in defaults.DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT INTO settings (key, value_json) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )

        await self.db.run(job)
        return await self.get_all()
