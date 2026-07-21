"""Full-text search over the document library (req. 10).

Two responsibilities live here:

1. **Indexing** — :func:`rebuild_index_sync` flattens everything searchable
   about one document (title, authority, doc type, native text, OCR text, AI
   summary, tag names) into its ``search_index`` row; schema triggers mirror
   that into the ``search_fts`` FTS5 table. Call it inside the same
   transaction as whatever changed the inputs.

2. **Querying** — :meth:`SearchRepository.search` runs a ranked (bm25) FTS5
   MATCH with metadata filters and returns snippets.

Escaping note (learned the hard way in the Stage 1 self-test): characters
like ``-`` are operators in FTS5 query syntax, so raw user input is NEVER
passed to MATCH. :func:`build_match_query` wraps every whitespace-separated
term in double quotes — ``KYC-2025`` becomes the phrase ``"KYC-2025"`` —
which searches literals safely. A trailing ``*`` is preserved as a prefix
search (``bank*`` finds banking).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.core.errors import InvalidInputError
from app.repositories.ai import latest_summary_sync
from app.repositories.base import BaseRepository, clamp_paging
from app.repositories.ocr import latest_ocr_text_sync
from app.repositories.tags import tag_names_for_document_sync

_SNIPPET_MARK_OPEN = "["
_SNIPPET_MARK_CLOSE = "]"

#: Validated ORDER BY fragments — the sort value never reaches SQL directly.
_SORTS = {
    "relevance": "rank",
    "newest": "(d.doc_date IS NULL), d.doc_date DESC, rank",
    "oldest": "(d.doc_date IS NULL), d.doc_date ASC, rank",
}


# -- query building ---------------------------------------------------------

_TOKEN_RE = re.compile(r'-?"[^"]*"?|\S+')


def build_match_query(raw: str | None) -> str | None:
    """Turn what a person types into a safe FTS5 MATCH expression.

    The little query language (Stage 7):

    * plain words — all must appear: ``kyc updation``
    * ``"quoted phrases"`` — words in exact order: ``"master direction"``
    * ``OR`` between terms: ``fema OR odi``
    * ``-word`` / ``-"phrase"`` — must NOT appear: ``kyc -draft``
    * a trailing ``*`` matches word beginnings; the LAST word gets this
      automatically while typing (so ``mast`` already finds “Master
      Direction”) unless the input ends with a space or a closing quote.

    Every term is emitted inside double quotes with interior quotes removed,
    so user input can never inject FTS5 syntax. Returns None when nothing
    searchable remains (e.g. empty input, or only exclusions).
    """
    text = raw or ""
    ends_open = bool(text) and not text[-1].isspace() and text[-1] != '"'

    positives: list[str] = []   # rendered terms and the literal "OR"
    negatives: list[str] = []   # rendered terms to exclude
    tokens = _TOKEN_RE.findall(text)

    for index, token in enumerate(tokens):
        negative = token.startswith("-")
        if negative:
            token = token[1:]
        phrase = token.startswith('"')
        explicit_prefix = token.endswith("*") and not phrase
        cleaned = token.replace('"', "").rstrip("*").strip()

        if not cleaned:
            continue
        if not negative and not phrase and cleaned.upper() == "OR":
            if positives and positives[-1] != "OR":
                positives.append("OR")
            continue

        is_last = index == len(tokens) - 1
        auto_prefix = (
            is_last and ends_open and not negative and not phrase
            and len(cleaned) >= 2
        )
        rendered = f'"{cleaned}"'
        if explicit_prefix or auto_prefix:
            rendered += "*"
        (negatives if negative else positives).append(rendered)

    while positives and positives[-1] == "OR":
        positives.pop()
    while positives and positives[0] == "OR":
        positives.pop(0)

    if not positives:
        return None
    expression = " ".join(positives)
    if negatives:
        expression = "(" + expression + ")" + "".join(
            f" NOT {term}" for term in negatives
        )
    return expression


# -- indexing ---------------------------------------------------------------

def rebuild_index_sync(conn: sqlite3.Connection, document_id: int) -> bool:
    """(Re)build one document's search_index row. False if the doc is gone."""
    doc = conn.execute(
        "SELECT id, title, authority, doc_type, text_content"
        " FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if doc is None:
        conn.execute("DELETE FROM search_index WHERE document_id = ?", (document_id,))
        return False

    parts = [doc["text_content"] or "", latest_ocr_text_sync(conn, document_id) or ""]
    body = "\n".join(p for p in parts if p).strip()

    summary_row = latest_summary_sync(conn, document_id)
    summary = ""
    if summary_row:
        summary = " ".join(
            p for p in (summary_row.get("one_line_summary"),
                        summary_row.get("detailed_summary")) if p
        )

    tags = " ".join(tag_names_for_document_sync(conn, document_id))

    conn.execute(
        """
        INSERT INTO search_index (document_id, title, authority, doc_type,
                                  body, summary, tags, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(document_id) DO UPDATE SET
            title = excluded.title,
            authority = excluded.authority,
            doc_type = excluded.doc_type,
            body = excluded.body,
            summary = excluded.summary,
            tags = excluded.tags,
            updated_at = datetime('now')
        """,
        (document_id, doc["title"], doc["authority"], doc["doc_type"],
         body, summary, tags),
    )
    return True


class SearchRepository(BaseRepository):
    async def rebuild_document(self, document_id: int) -> bool:
        return await self.db.run(lambda conn: rebuild_index_sync(conn, document_id))

    async def rebuild_all(self) -> int:
        """Re-index every document (Settings screen 'Rebuild search index')."""

        def job(conn: sqlite3.Connection) -> int:
            ids = [r["id"] for r in conn.execute("SELECT id FROM documents")]
            for doc_id in ids:
                rebuild_index_sync(conn, doc_id)
            return len(ids)

        return await self.db.run(job)

    async def search(
        self,
        q: str,
        authority: str | None = None,
        doc_type: str | None = None,
        file_kind: str | None = None,
        topic: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        snippet_tokens: int = 40,
        sort: str = "relevance",
    ) -> dict[str, Any]:
        order_by = _SORTS.get(sort or "relevance")
        if order_by is None:
            raise InvalidInputError(
                "sort must be one of: " + ", ".join(_SORTS)
            )
        match = build_match_query(q)
        if match is None:
            raise InvalidInputError("Please type something to search for.")
        page, page_size, offset = clamp_paging(page, page_size)

        conditions = ["search_fts MATCH ?"]
        params: list[Any] = [match]
        if authority:
            conditions.append("d.authority = ? COLLATE NOCASE")
            params.append(authority)
        if doc_type:
            conditions.append("d.doc_type = ? COLLATE NOCASE")
            params.append(doc_type)
        if file_kind:
            conditions.append("d.file_kind = ?")
            params.append(file_kind)
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
        where = " AND ".join(conditions)

        base = (
            "FROM search_fts JOIN documents d ON d.id = search_fts.rowid"
            f" WHERE {where}"
        )
        total = await self.db.fetch_value(f"SELECT COUNT(*) {base}", params)
        rows = await self.db.fetch_all(
            f"""
            SELECT d.id AS id, d.title, d.original_filename, d.authority,
                   d.doc_type, d.doc_date, d.file_kind, d.ocr_status,
                   d.downloaded_at,
                   bm25(search_fts) AS rank,
                   snippet(search_fts, -1, ?, ?, ' … ', ?) AS snippet
            {base}
            ORDER BY {order_by} LIMIT ? OFFSET ?
            """,
            [_SNIPPET_MARK_OPEN, _SNIPPET_MARK_CLOSE,
             max(8, int(snippet_tokens)), *params, page_size, offset],
        )
        items = [dict(r) for r in rows]
        pages = (int(total or 0) + page_size - 1) // page_size if total else 0
        return {
            "items": items,
            "total": int(total or 0),
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "query": q,
            "match_expression": match,
        }
