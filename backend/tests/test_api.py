"""HTTP-layer smoke tests. Need the real dependencies installed::

    pip install -r backend/requirements.txt
    pytest backend/tests/test_api.py

Auto-skipped when FastAPI is absent so `python -m unittest` in a bare
environment still passes. scripts/dev_check.py covers the same ground with
friendlier output — this file exists for people who prefer pytest.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

HAS_STACK = all(
    importlib.util.find_spec(m) is not None
    for m in ("fastapi", "pydantic", "httpx")
)


@unittest.skipUnless(HAS_STACK, "fastapi/pydantic/httpx not installed")
class TestApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="lkm-apitest-")
        os.environ["LKM_HOME"] = self.tmp

        from app.core.config import get_config

        get_config.cache_clear()
        from app.main import create_app

        self.app = create_app()
        self._lifespan = self.app.router.lifespan_context(self.app)
        await self._lifespan.__aenter__()

        import httpx

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self._lifespan.__aexit__(None, None, None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_health_and_error_envelope(self) -> None:
        r = await self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

        r = await self.client.get("/api/sources/999999")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "not_found")

    async def test_source_roundtrip_and_stage_gate(self) -> None:
        r = await self.client.post("/api/sources",
                                   json={"url": "https://rbi.org.in/x"})
        self.assertEqual(r.status_code, 201)
        sid = r.json()["id"]

        r = await self.client.post(f"/api/sources/{sid}/analyze")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "feature_not_available")

        r = await self.client.get("/api/sources")
        self.assertEqual(r.json()["total"], 1)


if __name__ == "__main__":
    unittest.main()
