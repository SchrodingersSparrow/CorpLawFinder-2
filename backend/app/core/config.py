"""Boot-level runtime configuration (standard library only).

Two layers of configuration exist in this application:

1. **Boot config (this module)** — paths, host/port, log level. Read once at
   startup from environment variables prefixed ``LKM_`` (e.g. ``LKM_HOME``,
   ``LKM_BACKEND_PORT``) or from a simple ``.env`` file in the project root.
   These are things that must be known *before* the database is open.

2. **User settings (`settings` table)** — naming template, folder rules, OCR
   engine, Ollama model, etc. Editable live from the Settings screen through
   the SettingsRepository. Initial values come from ``app/core/defaults.py``.

Stage 2 change: this module originally used the ``pydantic-settings`` package.
Reading four environment variables does not need a dependency, so it is now a
plain dataclass — one less thing to install, and testable without pip.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.core import defaults


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` .env file (comments and blanks ignored)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env(name: str, file_values: dict[str, str], fallback: str) -> str:
    """Real environment wins over .env file, which wins over the default."""
    return os.environ.get(name, file_values.get(name, fallback))


@dataclass(frozen=True)
class AppConfig:
    """Environment-driven boot configuration."""

    home: Path = defaults.APP_HOME
    schema_path: Path = defaults.SCHEMA_PATH
    migrations_dir: Path = defaults.MIGRATIONS_DIR
    backend_host: str = defaults.BACKEND_HOST
    backend_port: int = defaults.BACKEND_PORT
    log_level: str = "INFO"
    app_version: str = field(default=defaults.APP_VERSION)

    # Derived paths ---------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.home / "db" / "lkm.sqlite3"

    @property
    def library_root(self) -> Path:
        return self.home / "library"

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    def ensure_directories(self) -> None:
        """Create runtime directories if they do not exist."""
        for path in (self.home, self.db_path.parent, self.library_root, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    """Build an :class:`AppConfig` from environment / .env, with validation."""
    file_values = _read_env_file(defaults.PROJECT_ROOT / ".env")
    port_raw = _env("LKM_BACKEND_PORT", file_values, str(defaults.BACKEND_PORT))
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError(
            f"LKM_BACKEND_PORT must be a number, got {port_raw!r}"
        ) from exc
    return AppConfig(
        home=Path(_env("LKM_HOME", file_values, str(defaults.PROJECT_ROOT / "data"))).resolve(),
        backend_host=_env("LKM_BACKEND_HOST", file_values, defaults.BACKEND_HOST),
        backend_port=port,
        log_level=_env("LKM_LOG_LEVEL", file_values, "INFO").upper(),
    )


@lru_cache
def get_config() -> AppConfig:
    """Singleton accessor, suitable for FastAPI dependency injection."""
    config = load_config()
    config.ensure_directories()
    return config
