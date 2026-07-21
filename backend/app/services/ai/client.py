"""Talking to Ollama's local HTTP API (standard library only).

Ollama runs entirely on the user's own computer — no document text ever
leaves the machine. This client is deliberately tiny: list the installed
models, and ask one model for a JSON answer. Blocking on purpose; the
service runs it via ``asyncio.to_thread``.

Every failure mode carries the exact next step: Ollama not running →
install/start it; model not pulled → the ``ollama pull`` command to run.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

OLLAMA_HINT = (
    "Ollama is not reachable. Install it from https://ollama.com (free), "
    "start it, and pull a model once, e.g.:  ollama pull qwen2.5:7b-instruct"
)


class OllamaUnavailable(Exception):
    """Ollama isn't running / reachable at the configured address."""


class ModelMissing(Exception):
    """Ollama runs, but the requested model has not been pulled."""

    def __init__(self, model: str) -> None:
        super().__init__(
            f"The model {model!r} is not installed in Ollama. "
            f"Pull it once with:  ollama pull {model}"
        )
        self.model = model


def _request(url: str, payload: dict | None, timeout: float) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:300] if error.fp else ""
        raise OllamaUnavailable(
            f"Ollama answered HTTP {error.code} at {url}. {body}".strip()
        ) from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise OllamaUnavailable(OLLAMA_HINT) from error
    except json.JSONDecodeError as error:
        raise OllamaUnavailable(
            f"Ollama sent an unreadable answer from {url}."
        ) from error


def list_models(base_url: str, *, timeout: float = 10) -> set[str]:
    """Names of the models installed in Ollama (GET /api/tags)."""
    body = _request(base_url.rstrip("/") + "/api/tags", None, timeout)
    models = body.get("models") or []
    names: set[str] = set()
    for entry in models:
        name = (entry or {}).get("name")
        if name:
            names.add(str(name))
            names.add(str(name).split(":")[0])  # 'qwen2.5:7b' also as 'qwen2.5'
    return names


def generate_json(
    base_url: str,
    model: str,
    prompt: str,
    *,
    timeout: float,
) -> str:
    """One non-streaming JSON-mode completion (POST /api/generate)."""
    body = _request(
        base_url.rstrip("/") + "/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        },
        timeout,
    )
    if body.get("error"):
        message = str(body["error"])
        if "not found" in message.lower():
            raise ModelMissing(model)
        raise OllamaUnavailable(f"Ollama reported: {message}")
    return str(body.get("response") or "")
