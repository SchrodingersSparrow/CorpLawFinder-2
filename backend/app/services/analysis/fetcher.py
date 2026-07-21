"""Fetching pages for analysis.

Two ways to get a page:

* :func:`fetch_static` — plain HTTP with the standard library. Enough for
  RBI and SEBI, whose pages are rendered on the server. Blocking; callers
  run it via ``asyncio.to_thread``.
* :func:`fetch_with_browser` — a real headless browser (Playwright/Chromium)
  for JavaScript-built pages such as the MCA V3 portal. Playwright is
  imported lazily so the whole backend runs fine without it; when it is
  missing, :class:`BrowserUnavailable` explains the one-time install.
"""

from __future__ import annotations

from dataclasses import dataclass

_PAGE_BYTE_CAP = 5 * 1024 * 1024  # analysis reads pages, not archives

#: Content types treated as "this URL *is* the document, not a page about it".
FILE_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
}


class FetchFailure(Exception):
    """The page could not be fetched at all (DNS, refused, timeout…)."""

    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class BrowserUnavailable(Exception):
    """Playwright (or its Chromium) is not installed."""

    INSTALL_HINT = (
        "This page builds itself with JavaScript, so analysing it needs the "
        "browser engine. One-time install, from the project folder:\n"
        "    pip install playwright\n"
        "    playwright install chromium"
    )


@dataclass
class FetchResult:
    url: str                 # final URL after redirects
    http_status: int
    content_type: str        # lowercased media type, no parameters
    text: str                # decoded body (pages) — empty for file responses
    used_browser: bool = False

    @property
    def file_kind(self) -> str | None:
        """Set when the response is a document file rather than a page."""
        return FILE_CONTENT_TYPES.get(self.content_type)


def _decode(body: bytes, content_type_header: str) -> str:
    charset = "utf-8"
    for part in content_type_header.split(";")[1:]:
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip("\"' ") or "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def fetch_static(url: str, *, timeout: float, user_agent: str) -> FetchResult:
    """Plain-HTTP fetch (blocking — run in a thread). Redirects followed.

    HTTP error pages (401/403/404…) are returned as results, not exceptions:
    their bodies matter (login pages are how login walls get detected).
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    except urllib.error.HTTPError as error:
        body = error.read(_PAGE_BYTE_CAP) if error.fp else b""
        header = error.headers.get("Content-Type", "") if error.headers else ""
        content_type = header.split(";")[0].strip().lower()
        return FetchResult(
            url=error.url or url,
            http_status=error.code,
            content_type=content_type,
            text=_decode(body, header) if content_type.startswith("text") else "",
        )
    except urllib.error.URLError as error:
        raise FetchFailure(f"Could not reach the site: {error.reason}") from error
    except TimeoutError as error:
        raise FetchFailure("The site took too long to answer.") from error

    with response:
        header = response.headers.get("Content-Type", "")
        content_type = header.split(";")[0].strip().lower()
        if content_type in FILE_CONTENT_TYPES:
            return FetchResult(
                url=response.url, http_status=response.status,
                content_type=content_type, text="",
            )
        body = response.read(_PAGE_BYTE_CAP)
    return FetchResult(
        url=response.url,
        http_status=response.status,
        content_type=content_type,
        text=_decode(body, header),
    )


async def fetch_with_browser(url: str, *, timeout: float, user_agent: str) -> FetchResult:
    """Render the page in headless Chromium and return the resulting HTML."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise BrowserUnavailable(BrowserUnavailable.INSTALL_HINT) from error

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=user_agent)
                response = await page.goto(
                    url, timeout=timeout * 1000, wait_until="domcontentloaded"
                )
                # Give client-side rendering a moment to paint real content.
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:  # noqa: BLE001 — busy pages never go idle; proceed
                    pass
                html = await page.content()
                return FetchResult(
                    url=page.url,
                    http_status=response.status if response else 200,
                    content_type="text/html",
                    text=html,
                    used_browser=True,
                )
            finally:
                await browser.close()
    except BrowserUnavailable:
        raise
    except Exception as error:  # noqa: BLE001 — launch/goto failures, missing chromium
        message = str(error)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise BrowserUnavailable(BrowserUnavailable.INSTALL_HINT) from error
        raise FetchFailure(f"The browser could not open the page: {message}") from error
