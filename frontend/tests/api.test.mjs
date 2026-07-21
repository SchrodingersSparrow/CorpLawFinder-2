/* Tests for renderer/js/api.js — the backend HTTP client.
 * globalThis.fetch is replaced per test; every assertion inspects either the
 * request the client built or the error it raised. */

import test from "node:test";
import assert from "node:assert/strict";

const { api, ApiError, BASE_URL } = await import("../renderer/js/api.js");

function stubFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  return calls;
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
  };
}

test("BASE_URL falls back to the local port when no preload bridge exists", () => {
  assert.equal(BASE_URL, "http://127.0.0.1:8756");
});

test("get() serialises params and skips empty ones, keeping 0 and false", async () => {
  const calls = stubFetch(() => jsonResponse(200, { items: [] }));
  await api.get("/api/documents", {
    q: "fema", page: 0, skip1: null, skip2: undefined, skip3: "", flag: false,
  });
  const url = new URL(calls[0].url);
  assert.equal(url.pathname, "/api/documents");
  assert.equal(url.searchParams.get("q"), "fema");
  assert.equal(url.searchParams.get("page"), "0");
  assert.equal(url.searchParams.get("flag"), "false");
  assert.ok(!url.searchParams.has("skip1"));
  assert.ok(!url.searchParams.has("skip2"));
  assert.ok(!url.searchParams.has("skip3"));
});

test("post() sends a JSON body with the right header", async () => {
  const calls = stubFetch(() => jsonResponse(201, { id: 1 }));
  const out = await api.post("/api/sources", { url: "https://rbi.org.in" });
  assert.equal(out.id, 1);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].init.body), { url: "https://rbi.org.in" });
});

test("the backend's error envelope becomes an ApiError", async () => {
  stubFetch(() => jsonResponse(409, {
    error: { code: "duplicate", message: "This source already exists.", detail: { id: 7 } },
  }));
  await assert.rejects(api.post("/api/sources", { url: "x" }), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.code, "duplicate");
    assert.equal(err.status, 409);
    assert.equal(err.message, "This source already exists.");
    assert.deepEqual(err.detail, { id: 7 });
    return true;
  });
});

test("a non-JSON error body still produces a readable ApiError", async () => {
  stubFetch(() => ({ ok: false, status: 502, text: async () => "<html>bad gateway</html>" }));
  await assert.rejects(api.get("/api/health"), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.status, 502);
    assert.ok(err.message.includes("502"));
    return true;
  });
});

test("HTTP 204 resolves to null", async () => {
  stubFetch(() => ({ ok: true, status: 204, text: async () => "" }));
  assert.equal(await api.del("/api/sources/3"), null);
});

test("a network failure becomes a friendly backend_unreachable error", async () => {
  globalThis.fetch = async () => { throw new TypeError("fetch failed"); };
  await assert.rejects(api.get("/api/health"), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.code, "backend_unreachable");
    assert.equal(err.status, 0);
    return true;
  });
});

test("upload() passes FormData through without forcing a content type", async () => {
  const calls = stubFetch(() => jsonResponse(200, { added: 2 }));
  const fd = { fake: "formdata" };                 // request() must not touch it
  await api.upload("/api/sources/import-csv", fd);
  assert.equal(calls[0].init.body, fd);
  assert.equal(calls[0].init.headers["Content-Type"], undefined);
});

test("fileUrl() points at the document file endpoint", () => {
  assert.equal(api.fileUrl(12), `${BASE_URL}/api/documents/12/file`);
});
