"""Pure page-analysis functions (standard library only).

Everything here works on strings — no network, no browser — so the whole
analysis brain is unit-testable with fixture HTML. Fetching lives separately
in :mod:`app.services.analysis.fetcher`.

Tuned for the sites this app is used with:

* **RBI** (rbi.org.in) — server-rendered pages; document files live on the
  ``rbidocs.rbi.org.in`` subdomain as plain ``.PDF`` links.
* **SEBI** (sebi.gov.in) — server-rendered; attachments are direct file links.
* **MCA** (mca.gov.in) — the V3 portal is a JavaScript application (needs a
  real browser to render) and several areas sit behind a login. Both cases
  are detected and reported honestly instead of failing confusingly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# HTML → structured page
# ---------------------------------------------------------------------------


@dataclass
class ParsedPage:
    title: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (href, text)
    script_count: int = 0
    has_password_field: bool = False
    text_length: int = 0
    text_sample: str = ""  # first ~2000 chars of visible text, lowercased


class _PageCollector(HTMLParser):
    """One pass over the HTML collecting what analysis needs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = ParsedPage()
        self._in_title = False
        self._skip_depth = 0          # inside <script>/<style>
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._text_parts: list[str] = []
        self._text_length = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: (value or "") for name, value in attrs}
        if tag in ("script", "style"):
            self._skip_depth += 1
            if tag == "script":
                self.page.script_count += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._flush_anchor()
            self._anchor_href = attributes.get("href", "")
            self._anchor_text = []
        elif tag == "input" and attributes.get("type", "").lower() == "password":
            self.page.has_password_field = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "a":
            self._flush_anchor()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.page.title += data
        if self._anchor_href is not None:
            self._anchor_text.append(data)
        stripped = data.strip()
        if stripped:
            self._text_length += len(stripped)
            if len(self._text_parts) < 200:
                self._text_parts.append(stripped)

    def _flush_anchor(self) -> None:
        if self._anchor_href is not None:
            text = " ".join(" ".join(self._anchor_text).split())
            self.page.links.append((self._anchor_href.strip(), text))
        self._anchor_href = None
        self._anchor_text = []

    def close(self) -> "ParsedPage":  # type: ignore[override]
        super().close()
        self._flush_anchor()
        self.page.title = " ".join(unescape(self.page.title).split())
        self.page.text_length = self._text_length
        self.page.text_sample = " ".join(self._text_parts)[:2000].lower()
        return self.page


def parse_page(html: str) -> ParsedPage:
    collector = _PageCollector()
    try:
        collector.feed(html or "")
    except Exception:  # noqa: BLE001 — a broken page should never crash analysis
        pass
    return collector.close()


# ---------------------------------------------------------------------------
# Judgement calls on a parsed page
# ---------------------------------------------------------------------------

_JS_MARKERS = (
    "<app-root",          # Angular (the MCA V3 portal)
    "ng-version=",
    "id=\"__next\"",      # Next.js
    "enable javascript",
    "javascript is required",
    "javascript to run this app",
)


def needs_browser(html: str, page: ParsedPage) -> bool:
    """True when the page is a JavaScript shell that a plain fetch cannot read."""
    lowered = (html or "")[:20000].lower()
    if any(marker in lowered for marker in _JS_MARKERS):
        return True
    return page.script_count >= 3 and len(page.links) <= 2 and page.text_length < 400


_LOGIN_WORDS = ("login", "log in", "sign in", "signin")


def detect_login_wall(final_url: str, page: ParsedPage, http_status: int) -> str | None:
    """A human-readable reason when the page is behind a login, else None."""
    host = urlparse(final_url or "").netloc.lower()
    path = urlparse(final_url or "").path.lower()
    title_and_text = (page.title + " " + page.text_sample).lower()

    walled = False
    if http_status in (401, 403):
        walled = True
    elif any(word in path for word in ("login", "signin", "sign-in")):
        walled = True
    elif page.has_password_field and any(w in title_and_text for w in _LOGIN_WORDS):
        walled = True

    if not walled:
        return None
    if host.endswith("mca.gov.in"):
        return (
            "This MCA page needs a login. The app reads public pages only — "
            "for logged-in MCA documents, download the file in your browser; "
            "it can still be filed and searched here once saved."
        )
    return (
        "This page asks for a login before showing its content, "
        "so it cannot be analysed automatically."
    )


# ---------------------------------------------------------------------------
# Document links
# ---------------------------------------------------------------------------

#: Extensions treated as downloadable documents during DISCOVERY. Deliberately
#: excludes html/htm — otherwise every navigation link would count as a file.
DISCOVERY_EXTENSIONS = ("pdf", "docx", "doc", "xlsx", "xls", "zip")

_MAX_LINKS = 300


def link_extension(url: str) -> str | None:
    """The lowercase file extension of a URL's path, if it has one."""
    path = urlparse(url).path
    if "." not in path.rsplit("/", 1)[-1]:
        return None
    return path.rsplit(".", 1)[-1].lower() or None


def find_document_links(
    base_url: str,
    page: ParsedPage,
    allowed_extensions: tuple[str, ...] = DISCOVERY_EXTENSIONS,
) -> list[dict[str, str]]:
    """Absolute, de-duplicated document links found on the page (in order)."""
    seen: set[str] = set()
    found: list[dict[str, str]] = []
    allowed = {ext.lower().lstrip(".") for ext in allowed_extensions}
    for href, text in page.links:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        extension = link_extension(absolute)
        if extension not in allowed:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append({"url": absolute, "text": text[:300], "kind": extension})
        if len(found) >= _MAX_LINKS:
            break
    return found


# ---------------------------------------------------------------------------
# Authority & document-type guessing
# ---------------------------------------------------------------------------

_DOMAIN_AUTHORITIES: tuple[tuple[str, str], ...] = (
    ("rbi.org.in", "RBI"),            # includes rbidocs.rbi.org.in
    ("sebi.gov.in", "SEBI"),
    ("mca.gov.in", "MCA"),
    ("irdai.gov.in", "IRDAI"),
    ("pfrda.org.in", "PFRDA"),
    ("ifsca.gov.in", "IFSCA"),
    ("incometax.gov.in", "CBDT"),
    ("incometaxindia.gov.in", "CBDT"),
    ("cbic.gov.in", "CBIC"),
    ("npci.org.in", "NPCI"),
    ("fiuindia.gov.in", "FIU-IND"),
    ("meity.gov.in", "MeitY"),
    ("nclt.gov.in", "NCLT"),
    ("nclat.nic.in", "NCLAT"),
)


def guess_authority(url: str, page_title: str, known: list[str]) -> str | None:
    """Domain first (most reliable), then authority names in the page title."""
    host = urlparse(url or "").netloc.lower()
    for domain, authority in _DOMAIN_AUTHORITIES:
        if host == domain or host.endswith("." + domain):
            return authority
    title = " " + (page_title or "").upper() + " "
    for authority in known:
        token = " " + authority.upper() + " "
        if token in title.replace("(", " ").replace(")", " ").replace(",", " "):
            return authority
    return None


def guess_doc_type(text: str, known_types: list[str]) -> str | None:
    """Longest known type mentioned in the text ('Master Circular' beats
    'Circular')."""
    lowered = (text or "").lower()
    for doc_type in sorted(known_types, key=len, reverse=True):
        if doc_type.lower() in lowered:
            return doc_type
    return None
