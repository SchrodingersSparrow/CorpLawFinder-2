"""Smart file naming (requirement 4) — standard library only.

Builds the stored filename from the user's naming template
(``naming.template`` setting, e.g. ``{authority} - {doc_type} - {title} -
{date}``), makes it safe for Windows, keeps it under the configured length,
and finds a free name when a file with the same name already exists.
"""

from __future__ import annotations

import re
from pathlib import Path

# Characters Windows forbids in filenames, plus control characters.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Device names Windows reserves regardless of extension.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_PLACEHOLDER = re.compile(r"\{(authority|doc_type|title|date|circular_no)\}")
_SEPARATOR_RUN = re.compile(r"(?:\s*-\s*){2,}")  # "- - -" left by empty fields


def sanitize_component(value: str | None, fallback: str = "Unknown") -> str:
    """One template field / folder name, made Windows-safe."""
    text = _FORBIDDEN.sub(" ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(". ")            # Windows drops trailing dots/spaces
    if not text:
        return fallback
    if text.upper() in _RESERVED:
        return f"{text} file"
    return text


def render_filename(
    template: str,
    fields: dict[str, str | None],
    *,
    extension: str,
    max_length: int = 150,
    unknown: str = "Unknown",
) -> str:
    """Apply the naming template and return a safe ``stem + extension``.

    Fields that are missing become the ``unknown`` placeholder, and separator
    runs that empty fields leave behind (``A -  - B``) are collapsed so names
    stay tidy.
    """

    def substitute(match: re.Match[str]) -> str:
        raw = fields.get(match.group(1))
        return sanitize_component(raw, fallback=unknown)

    stem = _PLACEHOLDER.sub(substitute, template)
    stem = sanitize_component(stem, fallback="Document")
    stem = _SEPARATOR_RUN.sub(" - ", stem).strip(" -")
    if not stem:
        stem = "Document"

    extension = "." + extension.lstrip(".").lower() if extension else ""
    room = max(20, int(max_length)) - len(extension)
    if len(stem) > room:
        stem = stem[: room - 1].rstrip(" -") + "…"
    return stem + extension


def unique_path(directory: Path, filename: str) -> Path:
    """First free path for ``filename`` in ``directory`` — 'name (2).pdf' style."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    for n in range(2, 1000):
        suffixed = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
        candidate = directory / suffixed
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a free name for {filename!r}")
