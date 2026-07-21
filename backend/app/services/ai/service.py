"""Local-AI service (Stage 6) — the TASK_AI_SUMMARIZE handler.

For one document: take its text (native extraction, else OCR), ask the local
Ollama model for a single JSON answer — summary, metadata, topics, keywords,
confidence — and put every piece where it belongs:

* summaries → ``ai_summaries`` (the search index folds them in on rebuild);
* metadata → ``document_metadata`` candidates with confidence and
  ``extractor='ai'`` (the audit trail), and canonical document fields are
  filled **only where blank** — a value the user or the analyzer already set
  is never overwritten by the model;
* topics → tags of kind *topic* with ``origin='ai'`` (users can remove them);
* self-reported confidence below the configured threshold → Review Queue;
* with **folders.rule = topic**, the file is moved into its topic folder.

Everything runs on the user's own machine; no document text leaves it. The
Ollama client calls are injectable, so tests drive every path offline.

Payload shapes: ``{"document_id": …}`` from the Summarise button (a durable
run row is created here) and ``{"summary_id": …, "document_id": …,
"model": …}`` from startup resume.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from app.core.database import Database
from app.core.queue import TASK_AI_SUMMARIZE, TaskCancelled, TaskContext, TaskQueue
from app.models.enums import JobStatus, TagKind, TagOrigin
from app.repositories.ai import AiRepository
from app.repositories.documents import DocumentsRepository
from app.repositories.logs import log_event
from app.repositories.review import ReviewRepository
from app.repositories.search import rebuild_index_sync
from app.repositories.settings import SettingsRepository
from app.repositories.tags import assign_tag_sync, get_or_create_tag_sync
from app.services.ai import client, prompts
from app.utils.files import resolve_in_library
from app.utils.naming import sanitize_component, unique_path

_CANONICAL_PROMOTABLE = ("title", "authority", "doc_type", "doc_date", "language")
_METADATA_FIELDS = _CANONICAL_PROMOTABLE + ("circular_no",)


class AiService:
    def __init__(
        self,
        db: Database,
        queue: TaskQueue,
        *,
        library_root: Path | None = None,
        list_models: Callable[..., set[str]] = client.list_models,
        generate_json: Callable[..., str] = client.generate_json,
    ) -> None:
        self.db = db
        self.queue = queue
        self._library_root = library_root
        self._list_models = list_models
        self._generate = generate_json

    @property
    def library_root(self) -> Path:
        if self._library_root is None:
            from app.core.config import get_config

            self._library_root = get_config().library_root
        return self._library_root

    # ------------------------------------------------------------------
    # Queueing (auto-run hook used by the OCR/classification service)
    # ------------------------------------------------------------------

    async def queue_run(self, document_id: int) -> int | None:
        settings = (await SettingsRepository(self.db).get_all())["values"]
        if not bool(settings.get("ai.enabled", True)):
            return None
        if not bool(settings.get("ai.auto_run", True)):
            return None
        if not self.queue.has_handler(TASK_AI_SUMMARIZE):
            return None
        model = str(settings.get("ai.model", "qwen2.5:7b-instruct"))
        run_id = await AiRepository(self.db).create_run(document_id, model)
        self.queue.submit(
            TASK_AI_SUMMARIZE,
            {"summary_id": run_id, "document_id": document_id, "model": model},
            dedupe_key=f"ai:{run_id}",
        )
        return run_id

    # ------------------------------------------------------------------
    # The queue handler
    # ------------------------------------------------------------------

    async def handle(self, payload: dict[str, Any], ctx: TaskContext) -> None:
        document_id = int(payload["document_id"])
        settings = (await SettingsRepository(self.db).get_all())["values"]
        model = str(payload.get("model") or settings.get("ai.model", "qwen2.5:7b-instruct"))

        if "summary_id" in payload:
            run_id = int(payload["summary_id"])
        else:
            run_id = await AiRepository(self.db).create_run(document_id, model)

        if not bool(settings.get("ai.enabled", True)):
            await AiRepository(self.db).mark(
                run_id, str(JobStatus.FAILED),
                error_message="AI is turned off (Settings → Local AI).",
            )
            return

        try:
            await self._run(run_id, document_id, model, settings, ctx)
        except TaskCancelled:
            await AiRepository(self.db).mark(
                run_id, str(JobStatus.CANCELLED), error_message="Cancelled by user"
            )
            raise

    async def _run(
        self,
        run_id: int,
        document_id: int,
        model: str,
        settings: dict[str, Any],
        ctx: TaskContext,
    ) -> None:
        ai = AiRepository(self.db)
        documents = DocumentsRepository(self.db)

        doc = await documents.get(document_id)
        text_row = await documents.get_text(document_id)
        text = (text_row.get("native_text") or "").strip() or (
            text_row.get("ocr_text") or ""
        ).strip()
        if not text:
            await self._fail(
                run_id, document_id,
                "There is no text to summarise yet — if this is a scanned "
                "document, run OCR first.",
            )
            return

        base_url = str(settings.get("ai.ollama_url", "http://127.0.0.1:11434"))
        timeout = float(settings.get("ai.request_timeout_seconds", 180))

        try:
            installed = await asyncio.to_thread(
                self._list_models, base_url, timeout=min(timeout, 15)
            )
        except client.OllamaUnavailable as error:
            await self._fail(run_id, document_id, str(error))
            return

        chosen = self._pick_model(model, settings, installed)
        if chosen is None:
            small = str(settings.get("ai.small_model", "") or "")
            await self._fail(
                run_id, document_id,
                str(client.ModelMissing(model))
                + (f" (fallback {small!r} is not installed either)" if small else ""),
            )
            return
        if chosen != model:
            await log_event(
                self.db, "ai", "INFO",
                f"Model {model!r} is not installed — using {chosen!r} instead.",
                document_id=document_id,
            )

        ctx.raise_if_cancelled()
        await ai.mark(run_id, str(JobStatus.RUNNING))

        prompt = prompts.build_prompt(
            title=doc.get("title"),
            authority=doc.get("authority"),
            doc_type=doc.get("doc_type"),
            filename=doc.get("stored_filename") or doc.get("original_filename") or "",
            text=text,
            max_chars=int(settings.get("ai.max_input_chars", 12000)),
            known_authorities=list(settings.get("authorities.known", [])),
            known_doc_types=list(settings.get("doc_types.known", [])),
            known_topics=list(settings.get("topics.default", [])),
        )

        try:
            raw = await asyncio.to_thread(
                self._generate, base_url, chosen, prompt, timeout=timeout
            )
        except client.ModelMissing as error:
            await self._fail(run_id, document_id, str(error))
            return
        except client.OllamaUnavailable as error:
            await self._fail(run_id, document_id, str(error))
            return

        ctx.raise_if_cancelled()

        parsed = prompts.parse_response(
            raw,
            known_authorities=list(settings.get("authorities.known", [])),
            known_doc_types=list(settings.get("doc_types.known", [])),
        )
        if parsed is None or not (
            parsed["one_line_summary"] or parsed["detailed_summary"]
        ):
            await self._fail(
                run_id, document_id,
                "The model's answer could not be read as the expected JSON — "
                "try again, or try a different model in Settings → Local AI.",
            )
            return

        await self._apply(run_id, document_id, chosen, doc, parsed, settings)

    def _pick_model(
        self, preferred: str, settings: dict[str, Any], installed: set[str]
    ) -> str | None:
        small = str(settings.get("ai.small_model", "") or "")
        for candidate in (preferred, small):
            if candidate and (candidate in installed or candidate.split(":")[0] in installed):
                return candidate
        return None

    # ------------------------------------------------------------------
    # Applying the answer
    # ------------------------------------------------------------------

    async def _apply(
        self,
        run_id: int,
        document_id: int,
        model: str,
        doc: dict[str, Any],
        parsed: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        confidence = parsed.get("confidence")

        await AiRepository(self.db).mark(
            run_id, str(JobStatus.COMPLETED),
            one_line_summary=parsed["one_line_summary"],
            detailed_summary=parsed["detailed_summary"],
            topics_json=json.dumps(parsed["topics"], ensure_ascii=False),
            keywords_json=json.dumps(parsed["keywords"], ensure_ascii=False),
            authority=parsed["authority"],
            confidence=confidence,
        )

        # Audit trail: every extracted field, with confidence and provenance.
        candidates = {
            field: parsed.get(field) for field in _METADATA_FIELDS if parsed.get(field)
        }
        # Canonical promotion: blanks only — existing values always win.
        promote = {
            field: value for field, value in candidates.items()
            if field in _CANONICAL_PROMOTABLE and not doc.get(field)
        }

        documents = DocumentsRepository(self.db)
        if promote:
            await documents.update_canonical(
                document_id, promote, extractor="ai", confidence=confidence
            )
        from app.repositories.metadata import MetadataRepository

        metadata = MetadataRepository(self.db)
        for field, value in candidates.items():
            if field not in promote:  # promoted fields were recorded already
                await metadata.upsert(document_id, field, str(value), confidence, "ai")

        if parsed["topics"]:
            await self._tag_topics(document_id, parsed["topics"], confidence)

        await documents.reindex(document_id)  # summaries join the index

        await log_event(
            self.db, "ai", "INFO",
            f"AI summary completed with {model}"
            + (f" (confidence {confidence:.0%})" if confidence is not None else "")
            + (f"; topics: {', '.join(parsed['topics'])}" if parsed["topics"] else ""),
            document_id=document_id,
        )

        threshold = float(settings.get("ai.low_confidence_threshold", 0.55))
        if confidence is not None and confidence < threshold:
            await ReviewRepository(self.db).create(
                "low_ai_confidence",
                f"The model reported only {confidence:.0%} confidence for "
                f"“{doc.get('title') or doc.get('stored_filename')}” — please "
                "check the summary and extracted details.",
                document_id=document_id,
            )

        if str(settings.get("folders.rule", "authority")) == "topic" and parsed["topics"]:
            await self._file_into_topic_folder(
                document_id, parsed["topics"][0], settings
            )

    async def _tag_topics(
        self, document_id: int, topics: list[str], confidence: float | None
    ) -> None:
        def job(conn: sqlite3.Connection) -> None:
            for topic in topics:
                tag = get_or_create_tag_sync(conn, topic, kind=str(TagKind.TOPIC))
                assign_tag_sync(
                    conn, document_id, tag["id"],
                    origin=str(TagOrigin.AI), confidence=confidence,
                )
            rebuild_index_sync(conn, document_id)

        await self.db.run(job)

    async def _file_into_topic_folder(
        self, document_id: int, topic: str, settings: dict[str, Any]
    ) -> None:
        """folders.rule = topic: move the file from the fallback folder into
        library/<Topic>/ now that a topic is known. Never fails the run."""
        try:
            doc = await DocumentsRepository(self.db).get(document_id)
            current = resolve_in_library(self.library_root, doc.get("rel_path"))
            if current is None or not current.is_file():
                return
            folder = sanitize_component(
                topic, fallback=str(settings.get("folders.fallback", "Unsorted"))
            )
            if current.parent.name == folder:
                return  # already filed there
            directory = self.library_root / folder
            directory.mkdir(parents=True, exist_ok=True)
            destination = unique_path(directory, current.name)
            current.replace(destination)
            rel_path = destination.relative_to(self.library_root).as_posix()
            await self.db.execute(
                "UPDATE documents SET rel_path = ?, stored_filename = ? WHERE id = ?",
                (rel_path, destination.name, document_id),
            )
            await log_event(
                self.db, "ai", "INFO",
                f"Filed under the “{folder}” topic folder.",
                document_id=document_id,
            )
        except OSError as error:
            await log_event(
                self.db, "ai", "WARNING",
                f"Could not move the file into its topic folder: {error}",
                document_id=document_id,
            )

    async def _fail(self, run_id: int, document_id: int, message: str) -> None:
        await AiRepository(self.db).mark(
            run_id, str(JobStatus.FAILED), error_message=message
        )
        await ReviewRepository(self.db).create(
            "other", message, document_id=document_id
        )
        await log_event(
            self.db, "ai", "ERROR", f"AI summarising failed: {message}",
            document_id=document_id,
        )
