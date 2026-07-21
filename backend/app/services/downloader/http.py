"""File downloading over plain HTTP (standard library only).

Blocking on purpose — the service runs it via ``asyncio.to_thread``. Streams
to disk in chunks while hashing, so duplicate detection (SHA-256, req. 11)
costs nothing extra and huge files never sit in memory. Retries transient
failures (network errors, 5xx, 429) with backoff; permanent failures (404,
403…) fail immediately — retrying those only hammers the site.
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_CHUNK = 64 * 1024


class DownloadCancelled(Exception):
    """Raised mid-stream when the user pressed Cancel."""


class DownloadFailure(Exception):
    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


@dataclass
class DownloadOutcome:
    http_status: int
    size_bytes: int
    sha256: str
    content_type: str
    final_url: str
    filename_hint: str | None  # from Content-Disposition, if the server sent one


_DISPOSITION_UTF8 = re.compile(r"filename\*\s*=\s*(?:utf-8|UTF-8)''([^;]+)")
_DISPOSITION_PLAIN = re.compile(r'filename\s*=\s*"?([^";]+)"?')


def _last_path_segment(name: str) -> str | None:
    """The filename with any /- or \\-separated path components removed —
    explicit, so a hostile header behaves the same on Linux and Windows."""
    segment = re.split(r"[\\/]", name.strip())[-1].strip()
    return segment or None


def filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    match = _DISPOSITION_UTF8.search(header)
    if match:
        from urllib.parse import unquote

        return _last_path_segment(unquote(match.group(1)))
    match = _DISPOSITION_PLAIN.search(header)
    if match:
        return _last_path_segment(match.group(1))
    return None


def _interruptible_sleep(seconds: float, cancel_check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancel_check():
            raise DownloadCancelled()
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))


def download_to_file(
    url: str,
    destination: Path,
    *,
    user_agent: str,
    timeout: float = 120,
    max_retries: int = 3,
    backoff_seconds: float = 5,
    cancel_check: Callable[[], bool] = lambda: False,
) -> DownloadOutcome:
    """Download ``url`` into ``destination``, returning facts about the file.

    ``max_retries`` counts ATTEMPTS AFTER the first (3 retries = up to 4
    tries). The partially written file is removed on failure or cancel.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: DownloadFailure | None = None

    for attempt in range(1 + max(0, int(max_retries))):
        if cancel_check():
            raise DownloadCancelled()
        if attempt:
            _interruptible_sleep(backoff_seconds * attempt, cancel_check)
        try:
            return _attempt(url, destination, user_agent, timeout, cancel_check)
        except DownloadFailure as error:
            last_error = error
            retryable = error.http_status is None or error.http_status in (429,) or (
                error.http_status >= 500
            )
            if not retryable:
                break

    assert last_error is not None
    raise last_error


def _attempt(
    url: str,
    destination: Path,
    user_agent: str,
    timeout: float,
    cancel_check: Callable[[], bool],
) -> DownloadOutcome:
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "*/*"}
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    except urllib.error.HTTPError as error:
        raise DownloadFailure(
            f"The site answered HTTP {error.code} for this file.", error.code
        ) from error
    except urllib.error.URLError as error:
        raise DownloadFailure(f"Could not reach the site: {error.reason}") from error
    except TimeoutError as error:
        raise DownloadFailure("The download timed out.") from error

    digest = hashlib.sha256()
    size = 0
    try:
        with response, destination.open("wb") as output:
            while True:
                if cancel_check():
                    raise DownloadCancelled()
                try:
                    chunk = response.read(_CHUNK)
                except TimeoutError as error:
                    raise DownloadFailure("The download timed out mid-file.") from error
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except (DownloadCancelled, DownloadFailure):
        destination.unlink(missing_ok=True)
        raise
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise DownloadFailure(f"Could not write the file to disk: {error}") from error

    header = response.headers.get("Content-Type", "")
    return DownloadOutcome(
        http_status=response.status,
        size_bytes=size,
        sha256=digest.hexdigest(),
        content_type=header.split(";")[0].strip().lower(),
        final_url=response.url,
        filename_hint=filename_from_disposition(
            response.headers.get("Content-Disposition")
        ),
    )
