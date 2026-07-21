"""File-system helpers for the document library.

Every path that reaches the file system goes through :func:`resolve_in_library`
so a corrupted ``rel_path`` value (or a crafted request) can never read or
delete anything outside the library folder.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("lkm.system")

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
}


def resolve_in_library(library_root: Path, rel_path: str | None) -> Path | None:
    """Resolve ``rel_path`` inside the library; ``None`` if unsafe or unset."""
    if not rel_path:
        return None
    try:
        root = library_root.resolve()
        candidate = (root / rel_path).resolve()
        candidate.relative_to(root)  # raises ValueError if it escaped the root
    except (ValueError, OSError):
        logger.warning("Refusing path outside library: %r", rel_path)
        return None
    return candidate


def media_type_for(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def delete_library_file(library_root: Path, rel_path: str | None) -> bool:
    """Delete a stored file and prune now-empty parent folders. True if removed."""
    path = resolve_in_library(library_root, rel_path)
    if path is None or not path.is_file():
        return False
    path.unlink()
    # Tidy empty authority/topic folders left behind, stopping at the root.
    parent = path.parent
    root = library_root.resolve()
    while parent != root and parent.is_dir():
        try:
            parent.rmdir()  # only succeeds when empty
        except OSError:
            break
        parent = parent.parent
    return True


def human_size(num_bytes: int | None) -> str:
    """1234567 -> '1.2 MB' (used in log messages)."""
    if num_bytes is None:
        return "unknown size"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
