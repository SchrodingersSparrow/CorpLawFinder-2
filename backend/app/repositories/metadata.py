"""Repository for ``document_metadata`` — extraction candidates + audit trail.

Canonical values live on the ``documents`` row; every candidate (who said the
title is X, with what confidence) is kept here. When the user edits a field,
an ``extractor='user'`` row with confidence 1.0 records that decision, so the
Stage 6 AI never silently overwrites a human choice.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.core.errors import InvalidInputError
from app.models.enums import MetadataExtractor
from app.repositories.base import BaseRepository, rows_to_dicts


def upsert_metadata_sync(
    conn: sqlite3.Connection,
    document_id: int,
    field: str,
    value: str | None,
    confidence: float | None,
    extractor: str,
) -> None:
    if extractor not in MetadataExtractor:
        raise InvalidInputError(f"Unknown metadata extractor {extractor!r}")
    if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
        raise InvalidInputError("Confidence must be between 0 and 1")
    conn.execute(
        """
        INSERT INTO document_metadata (document_id, field, value, confidence, extractor)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(document_id, field) DO UPDATE SET
            value = excluded.value,
            confidence = excluded.confidence,
            extractor = excluded.extractor,
            updated_at = datetime('now')
        """,
        (document_id, field, value, confidence, extractor),
    )


class MetadataRepository(BaseRepository):
    async def list_for_document(self, document_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT field, value, confidence, extractor, updated_at"
            " FROM document_metadata WHERE document_id = ? ORDER BY field",
            (document_id,),
        )
        return rows_to_dicts(rows)

    async def upsert(
        self,
        document_id: int,
        field: str,
        value: str | None,
        confidence: float | None,
        extractor: str,
    ) -> None:
        await self.db.run(
            lambda conn: upsert_metadata_sync(
                conn, document_id, field, value, confidence, extractor
            )
        )
