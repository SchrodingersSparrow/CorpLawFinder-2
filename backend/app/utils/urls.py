"""Small URL helpers shared by the sources API and repositories."""

from __future__ import annotations

from urllib.parse import urlparse


def clean_url(raw: str | None) -> str:
    """Trim whitespace and stray surrounding quotes (common in pasted CSVs)."""
    return (raw or "").strip().strip('"').strip("'").strip()


def validate_url(raw: str | None) -> tuple[str | None, str | None]:
    """Return ``(url, None)`` when usable, else ``(None, reason)``.

    Deliberately forgiving: a bare ``rbi.org.in/...`` pasted without a scheme
    is upgraded to ``https://`` rather than rejected — lawyers paste links
    from PDFs and emails all day.
    """
    url = clean_url(raw)
    if not url:
        return None, "URL is empty"
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, f"Only http/https links are supported (got {parsed.scheme!r})"
    if not parsed.netloc or "." not in parsed.netloc:
        return None, "This does not look like a valid web address"
    if any(ch.isspace() for ch in url):
        return None, "The URL contains spaces"
    return url, None
