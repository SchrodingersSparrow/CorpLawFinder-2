"""FastAPI application factory.

Run locally with::

    python backend/scripts/run_backend.py

Interactive documentation is served at http://127.0.0.1:8756/docs — every
endpoint can be tried from the browser, no tooling needed.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import AppConfig, get_config
from app.core.database import Database
from app.core.errors import AppError
from app.core.logging_config import setup_logging
from app.core.queue import TaskQueue
from app.core.resume import requeue_pending
from app.core.seed import seed_defaults
from app.api import (
    dashboard,
    documents,
    downloads,
    jobs,
    logs,
    review,
    saved_searches,
    search,
    settings,
    sources,
    system,
    tags,
)

logger = logging.getLogger("lkm.system")


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(cfg.log_dir, cfg.log_level)
        cfg.ensure_directories()

        db = Database(cfg.db_path, cfg.schema_path, cfg.migrations_dir)
        await db.connect()
        await seed_defaults(db)

        queue = TaskQueue(concurrency=2)
        # Real background-work handlers (Stage 4: analysis + downloading;
        # later stages add OCR and AI here via the same registry).
        from app.services.handlers import register_all

        register_all(db, queue)
        await queue.start()

        app.state.config = cfg
        app.state.db = db
        app.state.queue = queue

        resumed = await requeue_pending(db, queue)
        try:
            from app.repositories.logs import log_event

            await log_event(
                db, "system", "INFO", "Backend started",
                version=cfg.app_version, resumed=resumed,
            )
        except Exception:  # pragma: no cover - logging must not block startup
            logger.exception("Could not write startup log row")

        try:
            yield
        finally:
            await queue.stop()
            try:
                from app.repositories.logs import log_event

                await log_event(db, "system", "INFO", "Backend stopped")
            except Exception:  # pragma: no cover
                pass
            await db.close()

    app = FastAPI(
        title="Legal Knowledge Manager",
        version=cfg.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    # The Electron renderer loads from file:// (Origin: "null") in production
    # and from a localhost dev server during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["null"],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- error envelope ---------------------------------------------------
    # Everything expected answers as {"error": {code, message, detail}} so the
    # UI shows one consistent, friendly toast instead of raw tracebacks.

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "detail": exc.detail,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_input",
                    "message": "Some of the provided values are not valid.",
                    "detail": {"errors": exc.errors()},
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": (
                        "Something went wrong inside the backend. The details "
                        "were written to the log (Settings → Logs)."
                    ),
                    "detail": None,
                }
            },
        )

    # ---- routes -----------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "app": "Legal Knowledge Manager backend",
            "docs": "/docs",
            "api": "/api",
        }

    for module in (
        system, sources, documents, downloads, tags,
        review, search, saved_searches, dashboard, settings, logs, jobs,
    ):
        app.include_router(module.router, prefix="/api")

    return app


app = create_app()
