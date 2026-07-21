"""Prompt construction and answer parsing for the local model (pure).

The model is asked for STRICT JSON with a fixed schema; parsing is tolerant
anyway (models wrap answers in code fences, add chatter, invent casing).
Normalisation maps the model's words back onto the app's known lists — a
"master direction" becomes the canonical "Master Direction" — and anything
unusable simply becomes None rather than an error.
"""

from __future__ import annotations

import json
from typing import Any

from app.utils.dates import parse_date

MAX_TOPICS = 5
MAX_KEYWORDS = 8


def build_prompt(
    *,
    title: str | None,
    authority: str | None,
    doc_type: str | None,
    filename: str,
    text: str,
    max_chars: int,
    known_authorities: list[str],
    known_doc_types: list[str],
    known_topics: list[str],
) -> str:
    """The single prompt used for summary + metadata + classification."""
    body = (text or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n[…document truncated…]"

    known = {
        "authorities": known_authorities,
        "doc_types": known_doc_types,
        "topics": known_topics,
    }
    context_bits = [f"Filename: {filename}"]
    if title:
        context_bits.append(f"Existing title: {title}")
    if authority:
        context_bits.append(f"Existing authority: {authority}")
    if doc_type:
        context_bits.append(f"Existing document type: {doc_type}")

    return (
        "You are an assistant for an Indian transaction lawyer's document "
        "library. Read the document below and answer with ONE JSON object "
        "and nothing else — no prose, no code fences.\n\n"
        "JSON schema (use null when unsure; never invent facts):\n"
        "{\n"
        '  "one_line_summary": string,          // <= 25 words, plain language\n'
        '  "detailed_summary": string,          // ~120-150 words, what it '
        "changes and who it applies to\n"
        '  "title": string|null,                // official document title\n'
        '  "authority": string|null,            // choose from known.authorities '
        "if it matches\n"
        '  "doc_type": string|null,             // choose from known.doc_types '
        "if it matches\n"
        '  "doc_date": string|null,             // date of issue, YYYY-MM-DD\n'
        '  "circular_no": string|null,          // circular/notification number\n'
        '  "language": string|null,             // e.g. "en", "hi"\n'
        '  "topics": [string],                  // 1-'
        f"{MAX_TOPICS} from known.topics when they fit, else short new ones\n"
        '  "keywords": [string],                // up to '
        f"{MAX_KEYWORDS} search keywords\n"
        '  "confidence": number                 // 0.0-1.0, your overall '
        "confidence\n"
        "}\n\n"
        f"known = {json.dumps(known, ensure_ascii=False)}\n\n"
        + "\n".join(context_bits)
        + "\n\nDOCUMENT TEXT:\n"
        + body
    )


# ---------------------------------------------------------------------------
# Parsing what comes back
# ---------------------------------------------------------------------------


def _first_json_object(text: str) -> dict[str, Any] | None:
    """The first balanced {...} in the text, parsed — fences and chatter
    tolerated."""
    cleaned = (text or "").replace("```json", "```").strip()
    if "```" in cleaned:
        cleaned = "".join(part for i, part in enumerate(cleaned.split("```"))
                          if i % 2 == 1) or cleaned.replace("```", "")
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start:index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _match_known(value: Any, known: list[str]) -> str | None:
    """Map the model's wording onto the app's canonical list, else keep a
    short free-text value."""
    text = " ".join(str(value or "").split())
    if not text:
        return None
    for candidate in known:
        if candidate.lower() == text.lower():
            return candidate
    return text[:80] if len(text) <= 80 else None


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item or "").split())[:60]
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
        if len(out) >= limit:
            break
    return out


def parse_response(
    raw: str,
    *,
    known_authorities: list[str],
    known_doc_types: list[str],
) -> dict[str, Any] | None:
    """Normalised fields from the model's answer, or None if unreadable."""
    data = _first_json_object(raw)
    if data is None:
        return None

    def clean_text(value: Any, limit: int) -> str | None:
        text = " ".join(str(value or "").split())
        return text[:limit] if text else None

    confidence: float | None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence"))))
    except (TypeError, ValueError):
        confidence = None

    return {
        "one_line_summary": clean_text(data.get("one_line_summary"), 300),
        "detailed_summary": clean_text(data.get("detailed_summary"), 2000),
        "title": clean_text(data.get("title"), 300),
        "authority": _match_known(data.get("authority"), known_authorities),
        "doc_type": _match_known(data.get("doc_type"), known_doc_types),
        "doc_date": parse_date(clean_text(data.get("doc_date"), 60)),
        "circular_no": clean_text(data.get("circular_no"), 80),
        "language": clean_text(data.get("language"), 20),
        "topics": _string_list(data.get("topics"), MAX_TOPICS),
        "keywords": _string_list(data.get("keywords"), MAX_KEYWORDS),
        "confidence": confidence,
    }
