"""Repository for the ``documents`` table — the heart of the library.

Every mutation that changes searchable content runs in ONE transaction with
:func:`app.repositories.search.rebuild_index_sync`, so the search index can
never drift from the data (a crash rolls both back together).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.core.errors import DuplicateError, InvalidInputError, NotFoundError
from app.models.enums import DocumentStatus, FileKind, MetadataExtractor, OcrStatus
from app.repositories.base import (
    BaseRepository,
    build_page,
    clamp_paging,
    row_to_dict,
    rows_to_dicts,
    safe_sort,
    where_sql,
)
from app.repositories.metadata import upsert_metadata_sync
from app.repositories.search import rebuild_index_sync
from app.repositories.tags import (
    assign_tag_sync,
    get_or_create_tag_sync,
    tags_for_document_sync,
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Canonical fields a user (or a later-stage extractor) may set directly.
CANONICAL_FIELDS = ("title", "authority", "doc_type", "doc_date", "language")

_SORTS = {
    "downloaded_at": "d.downloaded_at",
    "doc_date": "d.doc_date",
    "title": "d.title COLLATE NOCASE",
    "authority": "d.authority COLLATE NOCASE",
    "file_size": "d.file_size_bytes",
}

#: Columns returned by list views — everything except the heavy text_content.
_LIST_COLUMNS = (
    "d.id, d.source_id, d.title, d.authority, d.doc_type, d.doc_date, d.language, "
    "d.original_filename, d.stored_filename, d.rel_path, d.file_kind, "
    "d.file_size_bytes, d.sha256, d.download_url, d.downloaded_at, d.page_count, "
    "d.is_searchable, d.ocr_status, d.status, d.created_at, d.updated_at"
)


def _validate_canonical(fields: dict[str, Any]) -> dict[str, Any]:
    updates = {k: v for k, v in fields.items() if k in CANONICAL_FIELDS}
    if not updates:
        raise InvalidInputError(
            "Nothing to update — editable fields are: " + ", ".join(CANONICAL_FIELDS)
        )
    doc_date = updates.get("doc_date")
    if doc_date is not None and doc_date != "" and not _ISO_DATE_RE.match(str(doc_date)):
        raise InvalidInputError(
            "doc_date must look like 2025-03-12 (YYYY-MM-DD).", {"doc_date": doc_date}
        )
    return {k: (None if v == "" else v) for k, v in updates.items()}


class DocumentsRepository(BaseRepository):
    # -- create (used by tests now, by the Stage 4 downloader for real) ------

    async def create(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Insert a document row + its search index atomically.

        Required: ``original_filename``, ``file_kind``, ``sha256``.
        Raises :class:`DuplicateError` when the same file (by SHA-256) is
        already in the library (req. 11).
        """
        for required in ("original_filename", "file_kind", "sha256"):
            if not doc.get(required):
                raise InvalidInputError(f"Document is missing {required!r}")
        if doc["file_kind"] not in FileKind:
            raise InvalidInputError(f"Unknown file kind {doc['file_kind']!r}")
        if doc.get("ocr_status", OcrStatus.NOT_REQUIRED) not in OcrStatus:
            raise InvalidInputError(f"Unknown OCR status {doc.get('ocr_status')!r}")

        existing = await self.get_by_sha256(doc["sha256"])
        if existing is not None:
            raise DuplicateError(
                "An identical file is already in the library "
                f"(saved as “{existing.get('stored_filename') or existing['original_filename']}”).",
                existing,
            )

        doc = dict(doc)  # defaults for NOT NULL columns (explicit NULL would
        # bypass the schema's own DEFAULT clauses)
        doc.setdefault("ocr_status", str(OcrStatus.NOT_REQUIRED))
        doc.setdefault("status", str(DocumentStatus.NEW))
        if doc.get("is_searchable") is None:
            doc["is_searchable"] = 1 if doc.get("text_content") else 0

        columns = (
            "source_id", "title", "authority", "doc_type", "doc_date", "language",
            "original_filename", "stored_filename", "rel_path", "file_kind",
            "file_size_bytes", "sha256", "download_url", "page_count",
            "is_searchable", "ocr_status", "text_content", "status",
        )
        values = [doc.get(c) for c in columns]

        def job(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                f"INSERT INTO documents ({', '.join(columns)})"
                f" VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
            new_id = int(cur.lastrowid)
            rebuild_index_sync(conn, new_id)
            return new_id

        try:
            new_id = await self.db.run(job)
        except sqlite3.IntegrityError as exc:  # race on sha256 UNIQUE
            raise DuplicateError(
                "An identical file is already in the library."
            ) from exc
        return await self.get(new_id)

    # -- read ---------------------------------------------------------------

    async def get(self, document_id: int) -> dict[str, Any]:
        row = await self.db.fetch_one(
            f"SELECT {_LIST_COLUMNS}, s.url AS source_url"
            " FROM documents d LEFT JOIN sources s ON s.id = d.source_id"
            " WHERE d.id = ?",
            (document_id,),
        )
        if row is None:
            raise NotFoundError("document", document_id)
        return row_to_dict(row)  # type: ignore[return-value]

    async def get_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        row = await self.db.fetch_one(
            "SELECT * FROM documents WHERE sha256 = ?", (sha256,)
        )
        return row_to_dict(row)

    async def get_text(self, document_id: int) -> dict[str, Any]:
        """Native text + latest OCR text (served by its own endpoint because
        text can be megabytes and does not belong in list responses)."""
        row = await self.db.fetch_one(
            "SELECT id, text_content FROM documents WHERE id = ?", (document_id,)
        )
        if row is None:
            raise NotFoundError("document", document_id)
        from app.repositories.ocr import latest_ocr_text_sync

        ocr_text = await self.db.run(
            lambda conn: latest_ocr_text_sync(conn, document_id)
        )
        return {
            "document_id": document_id,
            "native_text": row["text_content"],
            "ocr_text": ocr_text,
        }

    async def tags_for(self, document_id: int) -> list[dict[str, Any]]:
        return await self.db.run(
            lambda conn: tags_for_document_sync(conn, document_id)
        )

    async def list(
        self,
        authority: str | None = None,
        doc_type: str | None = None,
        file_kind: str | None = None,
        ocr_status: str | None = None,
        status: str | None = None,
        topic: str | None = None,
        source_id: int | None = None,
        q: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        sort: str | None = None,
        order: str | None = None,
    ) -> dict[str, Any]:
        page, page_size, offset = clamp_paging(page, page_size)
        conditions: list[str] = []
        params: list[Any] = []
        if authority:
            conditions.append("d.authority = ? COLLATE NOCASE")
            params.append(authority)
        if doc_type:
            conditions.append("d.doc_type = ? COLLATE NOCASE")
            params.append(doc_type)
        if file_kind:
            if file_kind not in FileKind:
                raise InvalidInputError(f"Unknown file kind {file_kind!r}")
            conditions.append("d.file_kind = ?")
            params.append(file_kind)
        if ocr_status:
            if ocr_status not in OcrStatus:
                raise InvalidInputError(f"Unknown OCR status {ocr_status!r}")
            conditions.append("d.ocr_status = ?")
            params.append(ocr_status)
        if status:
            if status not in DocumentStatus:
                raise InvalidInputError(f"Unknown document status {status!r}")
            conditions.append("d.status = ?")
            params.append(status)
        if source_id is not None:
            conditions.append("d.source_id = ?")
            params.append(source_id)
        if q:
            conditions.append(
                "(d.title LIKE ? OR d.original_filename LIKE ? OR d.stored_filename LIKE ?)"
            )
            like = f"%{q}%"
            params.extend([like, like, like])
        if date_from:
            conditions.append("d.doc_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("d.doc_date <= ?")
            params.append(date_to)
        if topic:
            conditions.append(
                "EXISTS (SELECT 1 FROM document_tags dt JOIN tags t ON t.id = dt.tag_id"
                " WHERE dt.document_id = d.id AND t.name = ? COLLATE NOCASE)"
            )
            params.append(topic)

        where = where_sql(conditions)
        total = await self.db.fetch_value(
            f"SELECT COUNT(*) FROM documents d {where}", params
        )
        order_by = safe_sort(sort, order, _SORTS, "downloaded_at")
        rows = await self.db.fetch_all(
            f"""
            SELECT {_LIST_COLUMNS}, s.url AS source_url
            FROM documents d LEFT JOIN sources s ON s.id = d.source_id
            {where} {order_by} LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        )
        return build_page(rows_to_dicts(rows), int(total or 0), page, page_size)

    async def distinct_values(self) -> dict[str, list[str]]:
        """Filter-dropdown data: which authorities / types actually exist."""
        authorities = await self.db.fetch_all(
            "SELECT DISTINCT authority FROM documents"
            " WHERE authority IS NOT NULL ORDER BY authority COLLATE NOCASE"
        )
        doc_types = await self.db.fetch_all(
            "SELECT DISTINCT doc_type FROM documents"
            " WHERE doc_type IS NOT NULL ORDER BY doc_type COLLATE NOCASE"
        )
        return {
            "authorities": [r["authority"] for r in authorities],
            "doc_types": [r["doc_type"] for r in doc_types],
        }

    # -- update -------------------------------------------------------------

    async def update_canonical(
        self,
        document_id: int,
        fields: dict[str, Any],
        extractor: str = MetadataExtractor.USER,
        confidence: float | None = 1.0,
    ) -> dict[str, Any]:
        """Set canonical fields + audit rows + search index, atomically."""
        updates = _validate_canonical(fields)

        def job(conn: sqlite3.Connection) -> None:
            exists = conn.execute(
                "SELECT 1 FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if exists is None:
                raise NotFoundError("document", document_id)
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE documents SET {set_clause} WHERE id = ?",
                [*updates.values(), document_id],
            )
            for field, value in updates.items():
                upsert_metadata_sync(
                    conn, document_id, field,
                    None if value is None else str(value),
                    confidence, extractor,
                )
            rebuild_index_sync(conn, document_id)

        await self.db.run(job)
        return await self.get(document_id)

    async def add_tag(
        self, document_id: int, name: str, origin: str = "user"
    ) -> list[dict[str, Any]]:
        """Attach a tag by name (creating it if new) and re-index. Returns the
        document's updated tag list."""

        def job(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            exists = conn.execute(
                "SELECT 1 FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if exists is None:
                raise NotFoundError("document", document_id)
            tag = get_or_create_tag_sync(conn, name)
            assign_tag_sync(conn, document_id, tag["id"], origin=origin)
            rebuild_index_sync(conn, document_id)
            return tags_for_document_sync(conn, document_id)

        return await self.db.run(job)

    async def remove_tag(self, document_id: int, tag_id: int) -> list[dict[str, Any]]:
        def job(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            cur = conn.execute(
                "DELETE FROM document_tags WHERE document_id = ? AND tag_id = ?",
                (document_id, tag_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("tag on this document", tag_id)
            rebuild_index_sync(conn, document_id)
            return tags_for_document_sync(conn, document_id)

        return await self.db.run(job)

    async def set_extraction(
        self,
        document_id: int,
        *,
        text: str | None,
        page_count: int | None,
        is_searchable: bool,
        ocr_status: str,
    ) -> None:
        """Store the Stage 5 classification result in one atomic write:
        native text (if any), page count, searchable flag, OCR status —
        and rebuild the document's search index."""
        if ocr_status not in OcrStatus:
            raise InvalidInputError(f"Unknown OCR status {ocr_status!r}")

        def job(conn: sqlite3.Connection) -> None:
            cur = conn.execute(
                "UPDATE documents SET text_content = ?,"
                " page_count = COALESCE(?, page_count),"
                " is_searchable = ?, ocr_status = ? WHERE id = ?",
                (text, page_count, 1 if is_searchable else 0,
                 ocr_status, document_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("document", document_id)
            rebuild_index_sync(conn, document_id)

        await self.db.run(job)

    async def reindex(self, document_id: int) -> None:
        """Rebuild one document's search index row (e.g. after OCR text
        lands in ocr_results — the index merges it in automatically)."""
        await self.db.run(lambda conn: rebuild_index_sync(conn, document_id))

    async def set_text_content(self, document_id: int, text: str | None) -> None:
        """Store natively-extracted text and re-index (Stage 4/5 use this)."""

        def job(conn: sqlite3.Connection) -> None:
            cur = conn.execute(
                "UPDATE documents SET text_content = ? WHERE id = ?",
                (text, document_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError("document", document_id)
            rebuild_index_sync(conn, document_id)

        await self.db.run(job)

    async def set_ocr_status(self, document_id: int, ocr_status: str) -> None:
        if ocr_status not in OcrStatus:
            raise InvalidInputError(f"Unknown OCR status {ocr_status!r}")
        result = await self.db.execute(
            "UPDATE documents SET ocr_status = ? WHERE id = ?",
            (ocr_status, document_id),
        )
        if result.rowcount == 0:
            raise NotFoundError("document", document_id)

    # -- delete -------------------------------------------------------------

    async def delete(self, document_id: int) -> dict[str, Any]:
        """Delete the row (cascades clean metadata, tags, OCR, AI, search).
        Returns the deleted row so the caller can also remove the file."""
        doc = await self.get(document_id)
        await self.db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return doc
