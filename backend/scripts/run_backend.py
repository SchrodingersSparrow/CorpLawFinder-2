"""Start the Legal Knowledge Manager backend.

Usage (from the project root)::

    python backend/scripts/run_backend.py
    python backend/scripts/run_backend.py --port 9000 --reload
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LKM backend server")
    parser.add_argument("--host", default=None, help="Bind address (default from config)")
    parser.add_argument("--port", type=int, default=None, help="Port (default 8756)")
    parser.add_argument(
        "--reload", action="store_true",
        help="Auto-restart on code changes (development only)",
    )
    args = parser.parse_args()

    try:
        import uvicorn
        from app.core.config import get_config
    except ImportError as exc:
        print(
            f"\nA required package is missing: {exc.name}\n\n"
            "Install the backend dependencies first (one line, from the "
            "project root):\n\n"
            "    pip install -r backend/requirements.txt\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    cfg = get_config()
    host = args.host or cfg.backend_host
    port = args.port or cfg.backend_port

    print(f"Legal Knowledge Manager backend → http://{host}:{port}/docs")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
