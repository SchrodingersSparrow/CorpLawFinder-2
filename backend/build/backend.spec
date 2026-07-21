# PyInstaller spec for the packaged backend (Stage 8).
# Built by backend/scripts/freeze_backend.py — do not run PyInstaller on the
# entry .py directly, or the schema/migrations data files will be missed.
#
# Output: backend-dist/lkm-backend/  (folder mode: faster startup, friendlier
# to antivirus than a self-extracting single file). The Electron installer
# ships this whole folder as a resource.

from pathlib import Path

BACKEND_DIR = Path(SPECPATH).resolve().parent  # backend/build -> backend

a = Analysis(
    [str(BACKEND_DIR / "scripts" / "backend_entry.py")],
    pathex=[str(BACKEND_DIR)],
    datas=[
        (str(BACKEND_DIR / "db" / "schema.sql"), "db"),
        (str(BACKEND_DIR / "db" / "migrations"), "db/migrations"),
    ],
    hiddenimports=[
        # uvicorn resolves these lazily by name at startup:
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ],
    excludes=[
        # PaddleOCR's stack is ~1 GB and optional by design — the packaged
        # app uses Tesseract, exactly like a machine without PaddleOCR.
        "paddle", "paddleocr", "paddlepaddle", "torch",
        # Development-only:
        "pytest", "httpx",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lkm-backend",
    console=True,          # stdout/stderr feed the Electron log capture
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="lkm-backend",
)
