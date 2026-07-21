"""Shared status vocabularies.

These enums mirror the CHECK constraints in ``backend/db/schema.sql``.
If you change a value here, change the schema (via a migration) as well.
Using ``StrEnum`` means values serialize as plain strings in JSON responses
and can be bound directly as SQLite parameters.
"""

from __future__ import annotations

from enum import StrEnum


class SourceStatus(StrEnum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentStatus(StrEnum):
    NEW = "new"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class FileKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    ZIP = "zip"
    HTML = "html"
    OTHER = "other"


class OcrStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class OcrEngine(StrEnum):
    PADDLEOCR = "paddleocr"
    TESSERACT = "tesseract"


class DownloadStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    CANCELLED = "cancelled"


class JobStatus(StrEnum):
    """Generic lifecycle for OCR / AI background jobs."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewCategory(StrEnum):
    DOWNLOAD_FAILURE = "download_failure"
    OCR_FAILURE = "ocr_failure"
    METADATA_FAILURE = "metadata_failure"
    LOW_AI_CONFIDENCE = "low_ai_confidence"
    OTHER = "other"


class ReviewStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class LogCategory(StrEnum):
    SYSTEM = "system"
    ANALYSIS = "analysis"
    DOWNLOAD = "download"
    OCR = "ocr"
    AI = "ai"
    SEARCH = "search"


class TagKind(StrEnum):
    TOPIC = "topic"
    KEYWORD = "keyword"
    CUSTOM = "custom"


class TagOrigin(StrEnum):
    AI = "ai"
    USER = "user"


class MetadataExtractor(StrEnum):
    PATTERN = "pattern"   # regex / rule based
    AI = "ai"             # local LLM
    USER = "user"         # manually edited
