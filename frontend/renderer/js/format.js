/* Pure formatting helpers (unit-tested in frontend/tests). */

export function formatSize(bytes) {
  if (bytes === null || bytes === undefined || isNaN(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes;
  let unit = "B";
  for (const u of units) {
    if (value < 1024) break;
    value /= 1024;
    unit = u;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${unit}`;
}

/* Backend timestamps are UTC "YYYY-MM-DD HH:MM:SS" strings. */
export function parseUtc(ts) {
  if (!ts) return null;
  const iso = ts.includes("T") ? ts : ts.replace(" ", "T");
  const date = new Date(iso + (iso.endsWith("Z") ? "" : "Z"));
  return isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(ts) {
  const d = parseUtc(ts);
  if (!d) return "—";
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function formatDate(value) {
  if (!value) return "—";
  // doc_date is a plain YYYY-MM-DD with no timezone
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined, {
      day: "2-digit", month: "short", year: "numeric",
    });
  }
  return formatDateTime(value);
}

export function timeAgo(ts, now = new Date()) {
  const d = parseUtc(ts);
  if (!d) return "—";
  const seconds = Math.max(0, Math.floor((now - d) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} d ago`;
  return formatDate(ts);
}

/* Status → pill tone. Colour states the fact: green = done, red = needs a
 * human, amber = in flight, blue = informational. */
const TONES = {
  good: new Set(["completed", "succeeded", "ready", "analyzed", "resolved", "ok"]),
  bad: new Set(["failed", "error"]),
  wait: new Set(["pending", "queued", "running", "analyzing", "downloading",
                 "processing", "required", "open"]),
  info: new Set(["new", "dismissed", "skipped_duplicate", "not_required", "cancelled"]),
};

export function toneFor(status) {
  if (!status) return "";
  for (const [tone, set] of Object.entries(TONES)) {
    if (set.has(status)) return tone;
  }
  return "";
}

export function statusLabel(status) {
  if (!status) return "—";
  return String(status).replace(/_/g, " ");
}

/* Authority → docket-spine class. The registry of known hues lives in
 * tokens.css; everything else gets the neutral spine. */
export function spineClass(authority) {
  const a = (authority || "").toUpperCase();
  if (a.includes("RBI")) return "spine-rbi";
  if (a.includes("SEBI")) return "spine-sebi";
  if (a.includes("MCA") || a.includes("NCLT") || a.includes("NCLAT")) return "spine-mca";
  if (a.includes("CBDT") || a.includes("CBIC") || a.includes("TAX")) return "spine-tax";
  if (a.includes("IRDAI")) return "spine-irdai";
  return "";
}

export function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return url || "—";
  }
}

export function truncate(text, max = 90) {
  if (!text) return "";
  return text.length <= max ? text : text.slice(0, max - 1) + "…";
}

/* FTS snippets arrive with [ ] around hits; split for safe rendering
 * (returns [{text, hit}] — never raw HTML). */
export function splitSnippet(snippet) {
  if (!snippet) return [];
  const parts = [];
  const re = /\[([^\]]*)\]/g;
  let last = 0;
  let match;
  while ((match = re.exec(snippet)) !== null) {
    if (match.index > last) parts.push({ text: snippet.slice(last, match.index), hit: false });
    parts.push({ text: match[1], hit: true });
    last = re.lastIndex;
  }
  if (last < snippet.length) parts.push({ text: snippet.slice(last), hit: false });
  return parts;
}

export function gazetteDate(now = new Date()) {
  return now.toLocaleDateString(undefined, {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });
}
