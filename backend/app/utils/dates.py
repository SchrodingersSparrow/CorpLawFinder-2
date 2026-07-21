"""Date parsing tuned for Indian regulatory documents (standard library only).

Indian authorities write dates every way imaginable: ``July 15, 2026``,
``15 July 2026``, ``15th July, 2026``, ``15/07/2026`` (day first — the Indian
convention), ``15-07-2026``, ``15.07.2026`` and ISO ``2026-07-15``. These
helpers normalise all of them to ISO ``YYYY-MM-DD`` strings, or ``None`` when
a value cannot be read confidently. Ambiguity is resolved the Indian way:
``05/07/2026`` is 5 July, never 7 May.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))

# 15 July 2026 / 15th July, 2026 / 15-Jul-2026
_DAY_FIRST_NAMED = re.compile(
    rf"\b(\d{{1,2}})\s*(?:st|nd|rd|th)?[\s\-.,]+({_MONTH_NAMES})[\s\-.,]+(\d{{4}})\b",
    re.IGNORECASE,
)
# July 15, 2026 / Jul 15 2026
_MONTH_FIRST_NAMED = re.compile(
    rf"\b({_MONTH_NAMES})[\s\-.,]+(\d{{1,2}})\s*(?:st|nd|rd|th)?[\s\-.,]+(\d{{4}})\b",
    re.IGNORECASE,
)
# 2026-07-15 (ISO)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# 15/07/2026, 15-07-2026, 15.07.2026 — day first (Indian convention)
_DAY_FIRST_NUMERIC = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")

_YEAR_MIN, _YEAR_MAX = 1947, 2059  # nothing older than the Republic's paperwork


def _build(year: int, month: int, day: int) -> str | None:
    if not (_YEAR_MIN <= year <= _YEAR_MAX):
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_date(text: str | None) -> str | None:
    """Parse a string that IS a date (possibly with light decoration)."""
    if not text:
        return None
    value = text.strip()
    if not value or len(value) > 60:
        return None
    return extract_date(value)


def extract_date(text: str | None) -> str | None:
    """Find the first confident date anywhere inside ``text``.

    Match order encodes trust: an ISO date is unambiguous, then named-month
    forms, then day-first numeric (which is taken as DD/MM/YYYY, the Indian
    convention).
    """
    if not text:
        return None

    match = _ISO.search(text)
    if match:
        result = _build(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if result:
            return result

    match = _DAY_FIRST_NAMED.search(text)
    if match:
        result = _build(
            int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1))
        )
        if result:
            return result

    match = _MONTH_FIRST_NAMED.search(text)
    if match:
        result = _build(
            int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2))
        )
        if result:
            return result

    match = _DAY_FIRST_NUMERIC.search(text)
    if match:
        result = _build(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        if result:
            return result

    return None
