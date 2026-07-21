"""Freeze the backend into ``backend-dist/lkm-backend/`` (Stage 8).

Run on the machine that will build the installer (normally Windows)::

    pip install -r backend/requirements.txt
    pip install pyinstaller
    python backend/scripts/freeze_backend.py

The result is a self-contained folder with ``lkm-backend.exe`` inside —
Python not required on the machines the installer is later run on. The
Electron build (``npm run dist`` in ``frontend/``) picks the folder up
automatically as a resource.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC = PROJECT_ROOT / "backend" / "build" / "backend.spec"
DIST = PROJECT_ROOT / "backend-dist"
WORK = PROJECT_ROOT / "backend-dist" / ".work"


def main() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is not installed. One line fixes it:\n\n"
            "    pip install pyinstaller\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if (DIST / "lkm-backend").exists():
        shutil.rmtree(DIST / "lkm-backend")

    print("Freezing the backend (this takes a few minutes the first time)…")
    completed = subprocess.run([
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--noconfirm",
        "--distpath", str(DIST),
        "--workpath", str(WORK),
    ], cwd=PROJECT_ROOT)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)

    exe = DIST / "lkm-backend" / (
        "lkm-backend.exe" if sys.platform == "win32" else "lkm-backend"
    )
    schema = DIST / "lkm-backend" / "_internal" / "db" / "schema.sql"
    problems = [p for p in (exe, schema) if not p.exists()]
    if problems:
        for p in problems:
            print(f"MISSING after build: {p}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\nDone: {exe}")
    print("Quick self-test:   " + str(exe) + " --port 8901   (then Ctrl+C)")
    print("Next step:         cd frontend && npm install && npm run dist")


if __name__ == "__main__":
    main()
