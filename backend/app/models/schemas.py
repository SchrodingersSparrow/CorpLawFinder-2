"""Pydantic models for API requests and responses.

Conventions
-----------
* Timestamps travel as plain ISO strings — SQLite stores them that way and the
  UI formats them; converting to datetime and back adds nothing.
* Page envelopes are CONCRETE classes (SourcePage, DocumentPage, ...) rather
  than a generic, because concrete models render much more clearly in the
  auto-generated /docs page a non-developer will actually look at.
* Output models use ``extra="allow"`` so a repository adding a handy computed
  column (e.g. ``source_url``) never breaks a response.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"


class _Out(BaseModel):
    """Base for rows read from the database."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class SourceCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    authority: str | None = Field(default=None, max_length=120)
    source_type: str | None = Field(default=None, max_length=120)


class SourceBatchCreate(BaseModel):
    sources: list[SourceCreate] = Field(min_length=1, max_length=500)


class SourceUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    authority: str | None = Field(default=None, max_length=120)
    source_type: str | None = Field(default=None, max_length=120)

    def changed_fields(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class SourceOut(_Out):
    id: int
    url: str
    status: str


class SourcePage(BaseModel):
    items: list[SourceOut]
    total: int
    page: int
    page_size: int
    pages: int


class BatchAddResult(BaseModel):
    added: list[SourceOut]
    duplicates: list[dict[str, Any]]
    invalid: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class DocumentOut(_Out):
    id: int
    original_filename: str
    file_kind: str
    status: str


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    page: int
    page_size: int
    pages: int


class DocumentDetail(DocumentOut):
    metadata: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    ocr_runs: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []


class DocumentUpdate(BaseModel):
    """Manual corrections to the canonical fields (audited as extractor=user)."""

    title: str | None = Field(default=None, max_length=500)
    authority: str | None = Field(default=None, max_length=120)
    doc_type: str | None = Field(default=None, max_length=120)
    doc_date: str | None = Field(default=None, pattern=ISO_DATE)
    language: str | None = Field(default=None, max_length=30)

    @field_validator("doc_date", mode="before")
    @classmethod
    def _blank_date_is_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    def changed_fields(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class DocumentTextOut(BaseModel):
    document_id: int
    kind: str  # "native" | "ocr" | "none"
    text: str | None
    length: int


class TagAssign(BaseModel):
    name: str = Field(min_length=1, max_length=60)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TagOut(_Out):
    id: int
    name: str
    kind: str


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: str = Field(default="custom", pattern="^(topic|keyword|custom)$")


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


class DownloadOut(_Out):
    id: int
    url: str
    status: str


class DownloadPage(BaseModel):
    items: list[DownloadOut]
    total: int
    page: int
    page_size: int
    pages: int


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


class ReviewItemOut(_Out):
    id: int
    category: str
    status: str


class ReviewPage(BaseModel):
    items: list[ReviewItemOut]
    total: int
    page: int
    page_size: int
    pages: int


class ReviewResolve(BaseModel):
    status: str = Field(pattern="^(resolved|dismissed)$")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchResultItem(_Out):
    id: int
    snippet: str | None = None


class SearchResponse(BaseModel):
    items: list[SearchResultItem]
    total: int
    page: int
    page_size: int
    pages: int
    query: str
    match_expression: str


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SettingsOut(BaseModel):
    values: dict[str, Any]
    defaults: dict[str, Any]
    overridden: list[str]


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Dashboard / logs / jobs / system
# ---------------------------------------------------------------------------


class DashboardOut(BaseModel):
    counts: dict[str, Any]
    recent_documents: list[dict[str, Any]]
    recent_sources: list[dict[str, Any]]
    active_jobs: list[dict[str, Any]]
    download_counts: dict[str, int]


class LogOut(_Out):
    id: int
    level: str
    category: str
    message: str


class LogsResponse(BaseModel):
    items: list[LogOut]
    next_before_id: int | None


class JobOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    task_type: str
    status: str


class HealthOut(BaseModel):
    status: str
    version: str
    schema_version: int
    db_path: str
    library_root: str | None = None  # lets the desktop shell open files directly
    time: str


class CapabilitiesOut(BaseModel):
    app_version: str
    fts5: bool
    features: dict[str, Any]
