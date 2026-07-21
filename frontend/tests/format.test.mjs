/* Tests for renderer/js/format.js — pure helpers, no React involved.
 * Date-display assertions avoid exact locale strings (they vary by machine)
 * and check structure instead. */

import test from "node:test";
import assert from "node:assert/strict";

import {
  formatSize, parseUtc, formatDate, formatDateTime, timeAgo,
  toneFor, statusLabel, spineClass, hostOf, truncate, splitSnippet, gazetteDate,
} from "../renderer/js/format.js";

/* ---- sizes -------------------------------------------------------------- */

test("formatSize handles missing values and each unit band", () => {
  assert.equal(formatSize(null), "—");
  assert.equal(formatSize(undefined), "—");
  assert.equal(formatSize(NaN), "—");
  assert.equal(formatSize(0), "0 B");
  assert.equal(formatSize(512), "512 B");
  assert.equal(formatSize(2048), "2.0 KB");
  assert.equal(formatSize(5 * 1024 * 1024), "5.0 MB");
  assert.equal(formatSize(150 * 1024 * 1024), "150 MB");   // ≥100 rounds
  assert.equal(formatSize(3 * 1024 * 1024 * 1024), "3.0 GB");
});

/* ---- timestamps --------------------------------------------------------- */

test("parseUtc reads the backend's 'YYYY-MM-DD HH:MM:SS' as UTC", () => {
  const d = parseUtc("2026-07-20 10:30:00");
  assert.ok(d instanceof Date);
  assert.equal(d.toISOString(), "2026-07-20T10:30:00.000Z");
});

test("parseUtc accepts ISO input and rejects garbage", () => {
  assert.equal(parseUtc("2026-07-20T10:30:00Z").toISOString(), "2026-07-20T10:30:00.000Z");
  assert.equal(parseUtc(""), null);
  assert.equal(parseUtc(null), null);
  assert.equal(parseUtc("not a date"), null);
});

test("formatDate renders plain YYYY-MM-DD without timezone shifts", () => {
  const out = formatDate("2026-01-05");
  assert.notEqual(out, "—");
  assert.ok(out.includes("2026"), out);
  assert.ok(/jan/i.test(out), out);          // month stays January in any locale offset
  assert.equal(formatDate(null), "—");
});

test("formatDateTime falls back to a dash on bad input", () => {
  assert.equal(formatDateTime("nope"), "—");
  assert.ok(formatDateTime("2026-07-20 10:30:00").includes("2026"));
});

test("timeAgo buckets with an injected 'now'", () => {
  const now = new Date("2026-07-20T12:00:00Z");
  assert.equal(timeAgo("2026-07-20 11:59:40", now), "just now");
  assert.equal(timeAgo("2026-07-20 11:55:00", now), "5 min ago");
  assert.equal(timeAgo("2026-07-20 09:00:00", now), "3 h ago");
  assert.equal(timeAgo("2026-07-18 12:00:00", now), "2 d ago");
  assert.ok(timeAgo("2025-01-01 00:00:00", now).includes("2025")); // ≥30 d → date
  assert.equal(timeAgo(null, now), "—");
});

/* ---- status pills ------------------------------------------------------- */

test("toneFor maps statuses onto the four pill tones", () => {
  assert.equal(toneFor("completed"), "good");
  assert.equal(toneFor("resolved"), "good");
  assert.equal(toneFor("failed"), "bad");
  assert.equal(toneFor("running"), "wait");
  assert.equal(toneFor("open"), "wait");
  assert.equal(toneFor("skipped_duplicate"), "info");
  assert.equal(toneFor("cancelled"), "info");
  assert.equal(toneFor("mystery"), "");
  assert.equal(toneFor(null), "");
});

test("statusLabel humanises underscores", () => {
  assert.equal(statusLabel("skipped_duplicate"), "skipped duplicate");
  assert.equal(statusLabel(null), "—");
});

/* ---- docket spines ------------------------------------------------------ */

test("spineClass recognises authorities case-insensitively", () => {
  assert.equal(spineClass("RBI"), "spine-rbi");
  assert.equal(spineClass("Reserve Bank of India (RBI)"), "spine-rbi");
  assert.equal(spineClass("sebi"), "spine-sebi");
  assert.equal(spineClass("MCA"), "spine-mca");
  assert.equal(spineClass("NCLT Delhi"), "spine-mca");
  assert.equal(spineClass("NCLAT"), "spine-mca");
  assert.equal(spineClass("CBDT"), "spine-tax");
  assert.equal(spineClass("Income Tax Dept"), "spine-tax");
  assert.equal(spineClass("IRDAI"), "spine-irdai");
  assert.equal(spineClass("Ministry of Finance"), "");
  assert.equal(spineClass(null), "");
});

/* ---- small string helpers ----------------------------------------------- */

test("hostOf extracts the host and survives bad urls", () => {
  assert.equal(hostOf("https://rbi.org.in/Scripts/bs.aspx?id=1"), "rbi.org.in");
  assert.equal(hostOf("not a url"), "not a url");
  assert.equal(hostOf(""), "—");
});

test("truncate keeps short text, trims long text to the max with an ellipsis", () => {
  assert.equal(truncate("short"), "short");
  const ninety = "x".repeat(90);
  assert.equal(truncate(ninety), ninety);
  const long = "y".repeat(91);
  const out = truncate(long);
  assert.equal(out.length, 90);
  assert.ok(out.endsWith("…"));
  assert.equal(truncate("abcdef", 4), "abc…");
  assert.equal(truncate(null), "");
});

/* ---- search snippets ---------------------------------------------------- */

test("splitSnippet turns [hits] into parts, never HTML", () => {
  assert.deepEqual(splitSnippet(""), []);
  assert.deepEqual(splitSnippet(null), []);
  assert.deepEqual(splitSnippet("plain text"), [{ text: "plain text", hit: false }]);
  assert.deepEqual(splitSnippet("a [b] c"), [
    { text: "a ", hit: false }, { text: "b", hit: true }, { text: " c", hit: false },
  ]);
  assert.deepEqual(splitSnippet("[start] tail"), [
    { text: "start", hit: true }, { text: " tail", hit: false },
  ]);
  assert.deepEqual(splitSnippet("head [end]"), [
    { text: "head ", hit: false }, { text: "end", hit: true },
  ]);
  assert.deepEqual(splitSnippet("[a][b]"), [
    { text: "a", hit: true }, { text: "b", hit: true },
  ]);
});

test("gazetteDate is a long-form date containing the year", () => {
  const out = gazetteDate(new Date(2026, 6, 20));
  assert.ok(out.includes("2026"), out);
});
