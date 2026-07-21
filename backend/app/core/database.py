"""Async SQLite access layer (standard library only).

Design decision (recorded in docs/ARCHITECTURE.md): the app talks to SQLite
through **hand-written SQL in repository classes**, not an ORM. FTS5 queries,
triggers and bm25() ranking are first-class SQL features that ORMs handle
awkwardly, and a single-user desktop app does not need ORM abstraction.

Stage 2 change: the original plan used the third-party ``aiosqlite`` package.
It was replaced with this small bridge built on the standard library, because
``aiosqlite`` does exactly the same thing internally (a worker thread that owns
the connection) and dropping it means one less package that can fail to
install on the user's machine.

How it works
    * One background thread owns a single ``sqlite3`` connection.
    * Coroutines submit small functions to that thread through a queue and
      await a ``concurrent.futures.Future`` wrapped for asyncio.
    * SQLite serializes writes anyway, so one connection in WAL mode is
      simpler and safer than a pool for a desktop app.
    * :meth:`run` executes a whole function atomically (commit on success,
      rollback on any exception) — that is the transaction primitive used by
      repositories for multi-statement operations.

The same ``schema.sql`` used by ``scripts/init_db.py`` is applied on startup,
so a user who deletes the database gets a fresh working one on next launch.
Numbered migrations in ``backend/db/migrations/`` (e.g. ``002_add_x.sql``)
are applied in order and recorded in ``schema_migrations``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue as _queue
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from app.core import defaults

logger = logging.getLogger("lkm.system")

T = TypeVar("T")

_MIGRATION_FILE_RE = re.compile(r"^(\d{3,})_.+\.sql$")

# Connection-level pragmas. WAL lets the UI read while background jobs write.
_PRAGMAS: tuple[str, ...] = (
    "PRAGMA foreign_keys = ON;",
    "PRAGMA journal_mode = WAL;",
    "PRAGMA synchronous = NORMAL;",
    "PRAGMA busy_timeout = 5000;",
)


@dataclass(frozen=True)
class ExecResult:
    """Outcome of a write statement."""

    rowcount: int
    lastrowid: int


class Database:
    """A thin async wrapper around one ``sqlite3`` connection in a worker thread.

    All access goes through the helper methods below; repositories receive a
    ``Database`` instance via dependency injection and never touch ``sqlite3``
    directly (except inside functions passed to :meth:`run`).
    """

    def __init__(
        self,
        db_path: Path | None = None,
        schema_path: Path | None = None,
        migrations_dir: Path | None = None,
    ) -> None:
        self._db_path = Path(db_path or defaults.DB_PATH)
        self._schema_path = Path(schema_path or defaults.SCHEMA_PATH)
        self._migrations_dir = Path(migrations_dir or defaults.MIGRATIONS_DIR)
        self._jobs: _queue.SimpleQueue[
            tuple[Callable[[sqlite3.Connection], Any], concurrent.futures.Future] | None
        ] = _queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._running = False

    # -- worker thread ------------------------------------------------------

    def _worker(self) -> None:
        """Own the connection for its whole life; process jobs until sentinel."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            while True:
                item = self._jobs.get()
                if item is None:
                    break
                fn, fut = item
                if not fut.set_running_or_notify_cancel():
                    continue
                try:
                    fut.set_result(fn(conn))
                except BaseException as exc:  # noqa: BLE001 - forwarded to caller
                    try:
                        conn.rollback()
                    except sqlite3.Error:  # pragma: no cover - defensive
                        pass
                    fut.set_exception(exc)
        finally:
            conn.close()

    def _submit(self, fn: Callable[[sqlite3.Connection], T]) -> "asyncio.Future[T]":
        if not self._running:
            raise RuntimeError("Database.connect() has not been called")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._jobs.put((fn, fut))
        return asyncio.wrap_future(fut)

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Start the worker, apply pragmas, base schema and migrations."""
        if self._running:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._worker, name="lkm-sqlite", daemon=True
        )
        self._running = True
        self._thread.start()

        def _init(conn: sqlite3.Connection) -> None:
            for pragma in _PRAGMAS:
                conn.execute(pragma)

        await self._submit(_init)
        await self.apply_schema()
        await self.apply_migrations(self._migrations_dir)
        logger.info("Database ready at %s", self._db_path)

    async def close(self) -> None:
        if not self._running:
            return
        self._running = False
        self._jobs.put(None)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 10.0)
            self._thread = None

    # -- transactions -------------------------------------------------------

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``fn(conn)`` in the database thread as ONE transaction.

        Commits if ``fn`` returns normally, rolls back if it raises. Use this
        for any operation that must change several rows atomically, e.g.::

            def job(conn):
                conn.execute("UPDATE documents SET title=? WHERE id=?", (t, i))
                rebuild_index_sync(conn, i)
                return True
            await db.run(job)
        """

        def _tx(conn: sqlite3.Connection) -> T:
            try:
                result = fn(conn)
                conn.commit()
                return result
            except BaseException:
                conn.rollback()
                raise

        return await self._submit(_tx)

    async def _read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run a read-only function without an explicit commit."""
        return await self._submit(fn)

    # -- schema -------------------------------------------------------------

    async def apply_schema(self) -> None:
        """Apply the idempotent base schema and record schema version 1."""
        sql = self._schema_path.read_text(encoding="utf-8")

        def job(conn: sqlite3.Connection) -> None:
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, description)"
                " VALUES (?, ?)",
                (defaults.SCHEMA_VERSION, "base schema"),
            )

        await self.run(job)

    async def apply_migrations(self, migrations_dir: Path) -> None:
        """Apply any numbered ``NNN_description.sql`` files not yet recorded."""
        if not migrations_dir.exists():
            return
        rows = await self.fetch_all("SELECT version FROM schema_migrations")
        applied = {row["version"] for row in rows}
        for path in sorted(migrations_dir.glob("*.sql")):
            match = _MIGRATION_FILE_RE.match(path.name)
            if not match:
                logger.warning("Ignoring migration with unexpected name: %s", path.name)
                continue
            version = int(match.group(1))
            if version in applied:
                continue
            logger.info("Applying migration %s", path.name)
            sql = path.read_text(encoding="utf-8")

            def job(conn: sqlite3.Connection, sql: str = sql, version: int = version,
                    stem: str = path.stem) -> None:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                    (version, stem),
                )

            await self.run(job)

    # -- query helpers ------------------------------------------------------

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> ExecResult:
        """Execute one write statement and commit."""
        p = tuple(params)

        def job(conn: sqlite3.Connection) -> ExecResult:
            cur = conn.execute(sql, p)
            return ExecResult(cur.rowcount, int(cur.lastrowid or 0))

        return await self.run(job)

    async def execute_many(
        self, sql: str, seq_of_params: Iterable[Iterable[Any]]
    ) -> int:
        """Execute a statement for every parameter tuple; returns total rowcount."""
        rows = [tuple(p) for p in seq_of_params]

        def job(conn: sqlite3.Connection) -> int:
            cur = conn.executemany(sql, rows)
            return cur.rowcount

        return await self.run(job)

    async def fetch_one(
        self, sql: str, params: Iterable[Any] = ()
    ) -> sqlite3.Row | None:
        p = tuple(params)
        return await self._read(lambda conn: conn.execute(sql, p).fetchone())

    async def fetch_all(
        self, sql: str, params: Iterable[Any] = ()
    ) -> list[sqlite3.Row]:
        p = tuple(params)
        return await self._read(lambda conn: conn.execute(sql, p).fetchall())

    async def fetch_value(self, sql: str, params: Iterable[Any] = ()) -> Any:
        """Return the first column of the first row (or None)."""
        row = await self.fetch_one(sql, params)
        return None if row is None else row[0]

    async def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Execute an INSERT and return the new rowid."""
        result = await self.execute(sql, params)
        return result.lastrowid
