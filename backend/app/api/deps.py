"""FastAPI dependencies.

Everything long-lived (database bridge, task queue, config) is created once in
:mod:`app.main`'s lifespan and parked on ``app.state``. Routers reach it via
these tiny functions so tests can swap the whole app state in one place.
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import AppConfig
from app.core.database import Database
from app.core.queue import FEATURE_STAGES, TaskQueue
from app.core.errors import FeatureNotAvailableError


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_queue(request: Request) -> TaskQueue:
    return request.app.state.queue


def get_app_config(request: Request) -> AppConfig:
    return request.app.state.config


def ensure_handler(queue: TaskQueue, task_type: str) -> None:
    """Raise the friendly 'arrives in Stage N' error while a worker is absent.

    Stage 2 wires every button; Stages 4-6 register the real handlers. This
    keeps the UI honest instead of surfacing a 500.
    """
    if queue.has_handler(task_type):
        return
    feature, stage = FEATURE_STAGES.get(task_type, (task_type, 0))
    raise FeatureNotAvailableError(feature, stage)
