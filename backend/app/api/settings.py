"""Settings endpoints. Every key is validated against the known defaults and
type-checked, so a typo can never silently corrupt behaviour."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.core.database import Database
from app.models.schemas import SettingsOut, SettingsUpdate
from app.repositories.logs import log_event
from app.repositories.settings import SettingsRepository

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
async def get_settings(db: Database = Depends(get_db)) -> Any:
    return await SettingsRepository(db).get_all()


@router.put("", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdate, db: Database = Depends(get_db)
) -> Any:
    result = await SettingsRepository(db).set_many(body.values)
    await log_event(
        db, "system", "INFO", "Settings changed", keys=sorted(body.values)
    )
    return result


@router.delete("/{key}", response_model=SettingsOut)
async def reset_setting(key: str, db: Database = Depends(get_db)) -> Any:
    """Reset one key back to its default."""
    return await SettingsRepository(db).reset(key)


@router.post("/reset", response_model=SettingsOut)
async def reset_all_settings(db: Database = Depends(get_db)) -> Any:
    result = await SettingsRepository(db).reset_all()
    await log_event(db, "system", "INFO", "All settings reset to defaults")
    return result
