"""In-memory background task queue (standard library only).

Design decision (docs/ARCHITECTURE.md): there is deliberately **no jobs
table**. The queue lives in memory; durability comes from the per-work-unit
status columns that already exist (``downloads.status``, ``ocr_results.status``,
``ai_summaries.status``). On startup :mod:`app.core.resume` re-queues anything
still marked ``queued`` / ``running`` from a previous run, so a crash or
shutdown never loses work — the row is the source of truth, the queue is just
the engine.

Stage 2 ships the engine fully working (submit, workers, cooperative
cancellation, de-duplication, snapshots for the Jobs screen). Stages 4-6
plug in real handlers::

    queue.register(TASK_RUN_OCR, ocr_service.handle)

Handlers receive ``(payload, ctx)`` and should call
``ctx.raise_if_cancelled()`` inside long loops so Cancel buttons work.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger("lkm.system")

# Task type names (queue vocabulary, not a DB vocabulary).
TASK_ANALYZE_SOURCE = "analyze_source"
TASK_DOWNLOAD_FILE = "download_file"
TASK_RUN_OCR = "run_ocr"
TASK_AI_SUMMARIZE = "ai_summarize"

#: Which delivery stage brings the real handler for each task type. Used to
#: build the friendly "arrives in Stage N" message while a handler is missing.
FEATURE_STAGES: dict[str, tuple[str, int]] = {
    TASK_ANALYZE_SOURCE: ("Website analysis", 4),
    TASK_DOWNLOAD_FILE: ("Document downloading", 4),
    TASK_RUN_OCR: ("OCR", 5),
    TASK_AI_SUMMARIZE: ("AI summarising", 6),
}

Handler = Callable[[dict[str, Any], "TaskContext"], Awaitable[None]]

_FINISHED = ("succeeded", "failed", "cancelled")
_HISTORY_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class TaskCancelled(Exception):
    """Raised inside a handler when the user pressed Cancel."""


@dataclass
class Task:
    """One unit of background work, visible on the Jobs screen."""

    id: str
    task_type: str
    payload: dict[str, Any]
    dedupe_key: str | None = None
    status: str = "queued"  # queued | running | succeeded | failed | cancelled
    error: str | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "payload": self.payload,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class TaskContext:
    """Handed to handlers so long jobs can cooperate with cancellation."""

    def __init__(self, task: Task) -> None:
        self.task = task

    @property
    def cancelled(self) -> bool:
        return self.task.cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled(f"Task {self.task.id} was cancelled")

    async def sleep(self, seconds: float) -> None:
        """Sleep that wakes early (and raises) if the task is cancelled."""
        try:
            await asyncio.wait_for(self.task.cancel_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
        raise TaskCancelled(f"Task {self.task.id} was cancelled")


class TaskQueue:
    """Asyncio worker pool with named task types and cooperative cancel."""

    def __init__(self, concurrency: int = 2) -> None:
        self._concurrency = max(1, int(concurrency))
        self._handlers: dict[str, Handler] = {}
        self._pending: asyncio.Queue[Task] = asyncio.Queue()
        self._tasks: OrderedDict[str, Task] = OrderedDict()
        self._workers: list[asyncio.Task] = []
        self._counter = itertools.count(1)
        self._started = False

    # -- registry -----------------------------------------------------------

    def register(self, task_type: str, handler: Handler) -> None:
        self._handlers[task_type] = handler

    def has_handler(self, task_type: str) -> bool:
        return task_type in self._handlers

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker_loop(i), name=f"lkm-worker-{i}")
            for i in range(self._concurrency)
        ]
        logger.info("Task queue started with %d worker(s)", self._concurrency)

    async def stop(self) -> None:
        """Cancel workers; running tasks are marked cancelled (their durable
        status rows get re-queued by app.core.resume on next start)."""
        if not self._started:
            return
        self._started = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        for task in self._tasks.values():
            if task.status in ("queued", "running"):
                task.status = "cancelled"
                task.error = "Backend was shut down"
                task.finished_at = _now()
        logger.info("Task queue stopped")

    # -- submission ---------------------------------------------------------

    def submit(
        self,
        task_type: str,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> Task:
        """Queue a task. With ``dedupe_key``, an already active duplicate is
        returned instead of queueing the same work twice."""
        if task_type not in self._handlers:
            raise ValueError(f"No handler registered for task type {task_type!r}")
        if dedupe_key:
            for existing in self._tasks.values():
                if existing.dedupe_key == dedupe_key and existing.status in (
                    "queued",
                    "running",
                ):
                    return existing
        task = Task(
            id=f"{task_type}-{next(self._counter)}",
            task_type=task_type,
            payload=payload or {},
            dedupe_key=dedupe_key,
        )
        self._tasks[task.id] = task
        self._trim_history()
        self._pending.put_nowait(task)
        return task

    # -- inspection / control ----------------------------------------------

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def snapshot(self, active_only: bool = False) -> list[dict[str, Any]]:
        items = [
            t.to_dict()
            for t in self._tasks.values()
            if not (active_only and t.status in _FINISHED)
        ]
        items.reverse()  # newest first
        return items

    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status in ("queued", "running"))

    def cancel(self, task_id: str) -> Task | None:
        """Request cancellation. Queued tasks die immediately; running tasks
        get their event set and finish at the next cooperation point."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if task.status == "queued":
            task.status = "cancelled"
            task.error = "Cancelled before it started"
            task.finished_at = _now()
        elif task.status == "running":
            task.cancel_event.set()
        return task

    # -- internals ----------------------------------------------------------

    def _trim_history(self) -> None:
        finished = [tid for tid, t in self._tasks.items() if t.status in _FINISHED]
        excess = len(self._tasks) - _HISTORY_LIMIT
        for tid in finished[: max(0, excess)]:
            self._tasks.pop(tid, None)

    async def _worker_loop(self, index: int) -> None:
        while True:
            task = await self._pending.get()
            if task.status == "cancelled":  # cancelled while still queued
                self._pending.task_done()
                continue
            task.status = "running"
            task.started_at = _now()
            handler = self._handlers.get(task.task_type)
            try:
                if handler is None:  # handler unregistered after submit
                    raise RuntimeError(f"No handler for {task.task_type!r}")
                await handler(task.payload, TaskContext(task))
                task.status = "succeeded"
            except TaskCancelled:
                task.status = "cancelled"
                task.error = "Cancelled by user"
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.error = "Backend was shut down"
                task.finished_at = _now()
                self._pending.task_done()
                raise
            except Exception as exc:  # noqa: BLE001 - reported, never crashes worker
                task.status = "failed"
                task.error = f"{type(exc).__name__}: {exc}"
                logger.exception("Task %s failed", task.id)
            finally:
                if task.finished_at is None:
                    task.finished_at = _now()
            self._pending.task_done()
