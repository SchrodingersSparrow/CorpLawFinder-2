"""Application-wide exceptions.

Repositories and services raise these (framework-free) exceptions; the FastAPI
layer translates them into consistent JSON error responses in ``app/main.py``.
Keeping them free of FastAPI imports means the repository layer can be tested
without the web framework installed.

The JSON shape produced for every error is::

    {"error": {"code": "not_found", "message": "…", "detail": {...}}}
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected application errors."""

    code: str = "app_error"
    http_status: int = 400

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class NotFoundError(AppError):
    """A row (source, document, tag…) does not exist."""

    code = "not_found"
    http_status = 404

    def __init__(self, resource: str, resource_id: Any) -> None:
        super().__init__(
            f"{resource.capitalize()} {resource_id} was not found.",
            {"resource": resource, "id": resource_id},
        )


class DuplicateError(AppError):
    """A uniqueness rule was violated (e.g. the URL is already saved)."""

    code = "duplicate"
    http_status = 409

    def __init__(self, message: str, existing: dict[str, Any] | None = None) -> None:
        super().__init__(message, {"existing": existing} if existing else {})


class InvalidInputError(AppError):
    """Input failed a rule that Pydantic could not check (e.g. unknown setting key)."""

    code = "invalid_input"
    http_status = 422


class FeatureNotAvailableError(AppError):
    """The endpoint exists but its worker arrives in a later stage.

    Stage 2 wires every route; browser automation (Stage 4), OCR (Stage 5) and
    local AI (Stage 6) register their task handlers later. Until then the
    affected endpoints answer with a friendly, honest message instead of a
    confusing 500.
    """

    code = "feature_not_available"
    http_status = 409

    def __init__(self, feature: str, stage: int) -> None:
        super().__init__(
            f"{feature} is part of Stage {stage} and is not installed yet. "
            f"The button is already wired up — it will start working as soon as "
            f"Stage {stage} is added.",
            {"feature": feature, "stage": stage},
        )


class ConflictError(AppError):
    """The action does not apply to the row's current state."""

    code = "conflict"
    http_status = 409
