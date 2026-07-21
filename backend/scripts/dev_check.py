"""One-command health check for Stage 2.

Run AFTER installing dependencies::

    pip install -r backend/requirements.txt
    python backend/scripts/dev_check.py

It spins the real FastAPI app up in-process against a THROWAWAY database in a
temp folder (your real data/ is never touched), exercises every endpoint
group end-to-end over HTTP, prints one [ok] line per check, and cleans up.
If the last line says ALL CHECKS PASSED, Stage 2 is working on this machine.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# The throwaway home MUST be set before any app import reads the config.
TEMP_HOME = tempfile.mkdtemp(prefix="lkm-devcheck-")
os.environ["LKM_HOME"] = TEMP_HOME

try:
    import httpx
    from app.main import app
    from app.repositories.documents import DocumentsRepository
except ImportError as exc:
    print(
        f"\nA required package is missing: {exc.name}\n"
        "Please run:  pip install -r backend/requirements.txt\n",
        file=sys.stderr,
    )
    shutil.rmtree(TEMP_HOME, ignore_errors=True)
    raise SystemExit(1) from None

PASSED = 0


def ok(label: str) -> None:
    global PASSED
    PASSED += 1
    print(f"  [ok] {label}")


def expect(condition: bool, label: str, extra: object = "") -> None:
    if not condition:
        print(f"  [FAIL] {label} {extra}", file=sys.stderr)
        raise SystemExit(1)
    ok(label)


async def run_checks() -> None:
    transport = httpx.ASGITransport(app=app)
    # httpx does not run lifespan events itself — enter them explicitly.
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://check"
        ) as client:

            # -- system -------------------------------------------------
            r = await client.get("/api/health")
            expect(r.status_code == 200 and r.json()["status"] == "ok",
                   "GET /api/health", r.text)
            expect(r.json()["schema_version"] >= 1, "schema is applied")

            r = await client.get("/api/capabilities")
            body = r.json()
            expect(r.status_code == 200 and body["fts5"] is True,
                   "GET /api/capabilities (FTS5 available)", r.text)
            print(f"       features on this machine: "
                  f"{ {k: v['available'] for k, v in body['features'].items()} }")

            # -- sources ------------------------------------------------
            r = await client.post("/api/sources", json={
                "url": "rbi.org.in/notifications",
                "title": "RBI notifications",
            })
            expect(r.status_code == 201, "POST /api/sources (https:// added)", r.text)
            source = r.json()
            expect(source["url"].startswith("https://"), "URL was normalised")
            sid = source["id"]

            r = await client.post("/api/sources", json={"url": source["url"]})
            expect(r.status_code == 409
                   and r.json()["error"]["code"] == "duplicate",
                   "duplicate URL answers 409 with friendly error", r.text)

            r = await client.post("/api/sources/batch", json={"sources": [
                {"url": "https://www.sebi.gov.in/legal/circulars"},
                {"url": "not a url at all"},
                {"url": source["url"]},
            ]})
            b = r.json()
            expect(r.status_code == 200 and len(b["added"]) == 1
                   and len(b["invalid"]) == 1 and len(b["duplicates"]) == 1,
                   "POST /api/sources/batch buckets rows correctly", r.text)

            csv_bytes = ("url,title\n"
                         "https://www.mca.gov.in/content/mca/global/en/acts-rules.html,MCA acts\n"
                         "https://www.mca.gov.in/content/mca/global/en/acts-rules.html,repeat\n"
                         ).encode()
            r = await client.post(
                "/api/sources/import-csv",
                files={"file": ("urls.csv", csv_bytes, "text/csv")},
            )
            b = r.json()
            expect(r.status_code == 200 and len(b["added"]) == 1
                   and len(b["duplicates"]) == 1,
                   "POST /api/sources/import-csv (header + in-batch dupe)", r.text)

            r = await client.get("/api/sources", params={"q": "rbi"})
            expect(r.status_code == 200 and r.json()["total"] == 1,
                   "GET /api/sources?q=rbi filters", r.text)

            r = await client.patch(f"/api/sources/{sid}",
                                   json={"authority": "RBI"})
            expect(r.status_code == 200 and r.json()["authority"] == "RBI",
                   "PATCH /api/sources/{id}", r.text)

            # A dedicated source on a guaranteed-dead host (.invalid is
            # reserved) so the queued background task fails fast and this
            # self-check stays offline-friendly — it never fetches real sites.
            r = await client.post("/api/sources",
                                  json={"url": "https://self-check.invalid/page"})
            expect(r.status_code == 201, "POST /api/sources (analyze fixture)", r.text)
            dead_id = r.json()["id"]
            r = await client.post(f"/api/sources/{dead_id}/analyze")
            b = r.json()
            expect(r.status_code == 202
                   and b["job"]["task_type"] == "analyze_source"
                   and b["job"]["status"] in ("queued", "running"),
                   "analyze queues a real Stage 4 job (202)", r.text)
            r = await client.get(f"/api/sources/{dead_id}")
            expect(r.status_code == 200
                   and r.json()["status"] in ("analyzing", "failed"),
                   "analyze flips the source to analyzing", r.text)

            # -- settings -----------------------------------------------
            r = await client.get("/api/settings")
            expect(r.status_code == 200
                   and r.json()["values"]["download.max_concurrency"] == 3,
                   "GET /api/settings (defaults merged)", r.text)

            r = await client.put("/api/settings", json={
                "values": {"download.max_concurrency": 5}})
            expect(r.status_code == 200
                   and r.json()["values"]["download.max_concurrency"] == 5
                   and "download.max_concurrency" in r.json()["overridden"],
                   "PUT /api/settings stores an override", r.text)

            r = await client.put("/api/settings", json={
                "values": {"no.such.key": 1}})
            expect(r.status_code == 422, "unknown setting key rejected", r.text)

            r = await client.put("/api/settings", json={
                "values": {"download.max_concurrency": "three"}})
            expect(r.status_code == 422, "wrong setting type rejected", r.text)

            r = await client.delete("/api/settings/download.max_concurrency")
            expect(r.status_code == 200
                   and r.json()["values"]["download.max_concurrency"] == 3,
                   "DELETE /api/settings/{key} resets to default", r.text)

            # -- fixture document (repository layer, like Stage 4 will) --
            db = app.state.db
            doc = await DocumentsRepository(db).create({
                "source_id": sid,
                "title": "Master Direction on Foreign Investment",
                "authority": "RBI",
                "doc_type": "Master Direction",
                "doc_date": "2025-01-04",
                "original_filename": "master_direction_fi.pdf",
                "stored_filename": "RBI - Master Direction - Foreign Investment - 2025-01-04.pdf",
                "rel_path": "RBI/RBI - Master Direction - Foreign Investment - 2025-01-04.pdf",
                "file_kind": "pdf",
                "file_size_bytes": 12345,
                "sha256": "a" * 64,
                "text_content": "Foreign investment in Indian entities is regulated under FEMA.",
            })
            ok("fixture document created via repository (as Stage 4 will)")

            r = await client.get("/api/documents")
            expect(r.status_code == 200 and r.json()["total"] == 1,
                   "GET /api/documents lists it", r.text)

            r = await client.get(f"/api/documents/{doc['id']}")
            expect(r.status_code == 200 and r.json()["source_url"],
                   "GET /api/documents/{id} detail composite", r.text)

            r = await client.patch(f"/api/documents/{doc['id']}",
                                   json={"doc_date": "2025-01-05"})
            expect(r.status_code == 200 and r.json()["doc_date"] == "2025-01-05",
                   "PATCH /api/documents/{id} canonical correction", r.text)

            r = await client.get(f"/api/documents/{doc['id']}/text")
            expect(r.status_code == 200 and r.json()["kind"] == "native",
                   "GET /api/documents/{id}/text", r.text)

            r = await client.get(f"/api/documents/{doc['id']}/file")
            expect(r.status_code == 404,
                   "GET /api/documents/{id}/file → 404 (no file on disk yet)",
                   r.status_code)

            # -- search -------------------------------------------------
            r = await client.get("/api/search", params={"q": "FEMA"})
            b = r.json()
            expect(r.status_code == 200 and b["total"] == 1
                   and b["items"][0]["snippet"],
                   "GET /api/search finds body text with snippet", r.text)

            r = await client.get("/api/search",
                                 params={"q": "FEMA", "authority": "SEBI"})
            expect(r.status_code == 200 and r.json()["total"] == 0,
                   "search filter narrows correctly", r.text)

            # -- tags ---------------------------------------------------
            r = await client.post(f"/api/documents/{doc['id']}/tags",
                                  json={"name": "FEMA"})
            expect(r.status_code == 200 and any(
                t["name"] == "FEMA" for t in r.json()),
                   "POST /api/documents/{id}/tags", r.text)

            r = await client.get("/api/search", params={"q": "FEMA",
                                                        "topic": "FEMA"})
            expect(r.status_code == 200 and r.json()["total"] == 1,
                   "topic filter matches the new tag", r.text)

            # -- Stage 7: query language, sort, saved searches ----------
            r = await client.get("/api/search", params={"q": "FEM"})
            expect(r.status_code == 200 and r.json()["total"] == 1,
                   "search-as-you-type prefix finds partial words", r.text)
            r = await client.get("/api/search",
                                 params={"q": "FEMA", "sort": "newest"})
            expect(r.status_code == 200, "search accepts sort=newest", r.text)
            r = await client.get("/api/search",
                                 params={"q": '((("; DROP TABLE--'})
            expect(r.status_code in (200, 422),
                   "hostile query never causes a server error", r.text)

            r = await client.post("/api/saved-searches", json={
                "name": "FEMA watch", "query": "FEMA -draft",
                "filters": {"authority": "RBI", "sort": "newest"},
            })
            expect(r.status_code == 201 and r.json()["name"] == "FEMA watch",
                   "POST /api/saved-searches", r.text)
            saved_id = r.json()["id"]
            r = await client.post("/api/saved-searches", json={
                "name": "fema watch", "query": "FEMA", "filters": {},
            })
            expect(r.status_code == 201 and r.json()["id"] == saved_id,
                   "saving the same name updates instead of duplicating", r.text)
            r = await client.post(f"/api/saved-searches/{saved_id}/use")
            expect(r.status_code == 200 and r.json()["query"] == "FEMA",
                   "POST /api/saved-searches/{id}/use recalls it", r.text)
            r = await client.delete(f"/api/saved-searches/{saved_id}")
            expect(r.status_code == 204, "DELETE /api/saved-searches/{id}", r.text)

            # -- review / dashboard / logs / jobs -----------------------
            from app.repositories.review import ReviewRepository
            item = await ReviewRepository(db).create(
                "other", "dev_check demo item")
            r = await client.post(f"/api/review/{item['id']}/resolve",
                                  json={"status": "resolved"})
            expect(r.status_code == 200 and r.json()["status"] == "resolved",
                   "POST /api/review/{id}/resolve", r.text)

            r = await client.get("/api/dashboard")
            b = r.json()
            expect(r.status_code == 200
                   and b["counts"]["total_documents"] == 1,
                   "GET /api/dashboard counts", r.text)

            r = await client.get("/api/logs")
            expect(r.status_code == 200 and len(r.json()["items"]) > 0,
                   "GET /api/logs shows activity", r.text)

            r = await client.get("/api/jobs")
            expect(r.status_code == 200, "GET /api/jobs", r.text)

            # -- delete -------------------------------------------------
            r = await client.delete(f"/api/documents/{doc['id']}")
            expect(r.status_code == 204, "DELETE /api/documents/{id}", r.text)
            r = await client.get("/api/search", params={"q": "FEMA"})
            expect(r.json()["total"] == 0,
                   "deleted document vanished from search")


def main() -> None:
    print(f"\nStage 2 self-check (throwaway data in {TEMP_HOME})\n")
    try:
        asyncio.run(run_checks())
    finally:
        shutil.rmtree(TEMP_HOME, ignore_errors=True)
    print(f"\nALL CHECKS PASSED ({PASSED} checks). Stage 2 works on this machine.\n")


if __name__ == "__main__":
    main()
