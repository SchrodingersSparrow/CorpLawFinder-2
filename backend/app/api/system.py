"""System endpoints: liveness and honest capability reporting."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api.deps import get_app_config, get_db
from app.core import capabilities, defaults
from app.core.config import AppConfig
from app.core.database import Database
from app.models.schemas import CapabilitiesOut, HealthOut

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
async def health(
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_app_config),
) -> HealthOut:
    version = await db.fetch_value(
        "SELECT MAX(version) FROM schema_migrations"
    )
    return HealthOut(
        status="ok",
        version=config.app_version,
        schema_version=int(version or 0),
        db_path=str(config.db_path),
        library_root=str(config.library_root),
        time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )


@router.get("/capabilities", response_model=CapabilitiesOut)
async def get_capabilities() -> CapabilitiesOut:
    """What is installed on THIS machine, feature by feature.

    The Settings screen uses this to show green/grey badges instead of letting
    a missing optional dependency surface as a mystery failure later.
    """
    return CapabilitiesOut(**capabilities.probe())
