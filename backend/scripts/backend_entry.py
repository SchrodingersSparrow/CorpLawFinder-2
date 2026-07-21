"""Backend entry point for the packaged desktop app (Stage 8).

PyInstaller freezes THIS file into ``lkm-backend.exe``. It differs from
``run_backend.py`` in one important way: it imports the FastAPI ``app``
object directly (not by "app.main:app" string), so PyInstaller's static
analysis sees and bundles the entire application, and uvicorn never has to
import anything by name at runtime.

Also runnable from source for parity testing:

    python backend/scripts/backend_entry.py --port 8756
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):  # from source: make `app` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="LKM backend (packaged entry)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    import uvicorn

    from app.core.config import get_config
    from app.main import app  # direct object import — see module docstring

    cfg = get_config()
    host = args.host or cfg.backend_host
    port = args.port or cfg.backend_port
    print(f"Legal Knowledge Manager backend → http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
