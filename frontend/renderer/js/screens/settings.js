/* Settings: preferences (typed, validated by the backend), what's installed
 * on this machine, and the activity log. */

import { h, Fragment, useEffect, useState } from "../h.js";
import { api } from "../api.js";
import { formatDateTime, statusLabel } from "../format.js";
import { setTheme, toast, toastError } from "../store.js";
import { useStore } from "../components/hooks.js";
import { Tabs, Toggle } from "../components/ui.js";

/* Friendly labels per setting key; anything not listed still renders with its
 * raw key, so new backend settings never disappear from the screen. */
const SECTIONS = [
  ["Naming & filing", [
    ["naming.template", "Filename pattern"],
    ["naming.date_format", "Date format in filenames"],
    ["naming.max_length", "Longest allowed filename"],
    ["naming.unknown_placeholder", "Word used when a detail is unknown"],
    ["folders.rule", "File documents into folders by"],
    ["folders.fallback", "Folder for unclassified documents"],
  ]],
  ["Website analysis", [
    ["analysis.auto_download", "Download files automatically after analysing a page"],
    ["analysis.use_browser", "Use the browser engine for JavaScript-built pages"],
  ]],
  ["Downloading", [
    ["download.max_concurrency", "Files fetched at the same time"],
    ["download.retries", "Retry attempts per file"],
    ["download.retry_backoff_seconds", "Wait between retries (seconds)"],
    ["download.timeout_seconds", "Give up on a file after (seconds)"],
    ["download.polite_delay_seconds", "Pause between requests to a site (seconds)"],
    ["download.user_agent", "Browser identity sent to websites"],
    ["download.allowed_extensions", "File types to download"],
  ]],
  ["OCR (reading scanned documents)", [
    ["ocr.engine", "Preferred OCR engine"],
    ["ocr.fallback_engine", "Fallback engine"],
    ["ocr.languages", "Languages to recognise"],
    ["ocr.min_chars_per_page_searchable", "Minimum characters per page to count as searchable"],
    ["ocr.render_dpi", "Scan resolution (DPI)"],
    ["ocr.auto_run", "OCR scanned documents automatically"],
    ["ocr.poppler_path", "Poppler location (empty = find automatically)"],
    ["ocr.tesseract_path", "Tesseract location (empty = find automatically)"],
  ]],
  ["Local AI", [
    ["ai.enabled", "Use the local AI model"],
    ["ai.auto_run", "Summarise new documents automatically"],
    ["ai.ollama_url", "Ollama address"],
    ["ai.model", "Model"],
    ["ai.small_model", "Smaller fallback model"],
    ["ai.max_input_chars", "Longest text sent to the model"],
    ["ai.low_confidence_threshold", "Send to review below this confidence"],
    ["ai.request_timeout_seconds", "Give up on the model after (seconds)"],
  ]],
  ["Search", [
    ["search.snippet_tokens", "Words shown around a match"],
    ["search.page_size", "Results per page"],
  ]],
  ["Registry lists", [
    ["authorities.known", "Known authorities"],
    ["doc_types.known", "Known document types"],
    ["topics.default", "Default topics"],
  ]],
];

const CHOICES = {
  "analysis.use_browser": ["auto", "never", "always"],
  "folders.rule": ["authority", "topic"],
  "ocr.engine": ["paddleocr", "tesseract"],
  "ocr.fallback_engine": ["tesseract", "paddleocr"],
};

export function SettingsScreen() {
  const [tab, setTab] = useState("preferences");
  return h(Fragment, null,
    h("header.screen-head", null, h("h2.screen-title", null, "Settings")),
    h("div.screen-body", null,
      h(Tabs, {
        tabs: [["preferences", "Preferences"],
               ["capabilities", "What's installed"],
               ["logs", "Activity log"]],
        active: tab,
        onSelect: setTab,
      }),
      tab === "preferences" && h(PreferencesTab),
      tab === "capabilities" && h(CapabilitiesTab),
      tab === "logs" && h(LogsTab),
    ),
  );
}

/* ---- Preferences ---------------------------------------------------------- */

function PreferencesTab() {
  const { theme } = useStore();
  const [data, setData] = useState(null);
  const [dirty, setDirty] = useState({});
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setData(await api.get("/api/settings"));
      setDirty({});
    } catch (err) {
      toastError(err);
    }
  };

  useEffect(() => { load(); }, []);

  if (!data) return h("p.muted-note", null, "Loading…");

  const valueOf = (key) =>
    key in dirty ? dirty[key] : data.values[key];

  const setValue = (key, value) => setDirty({ ...dirty, [key]: value });

  const save = async () => {
    setBusy(true);
    try {
      setData(await api.put("/api/settings", { values: dirty }));
      setDirty({});
      toast("Settings saved");
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  const resetKey = (key) => async () => {
    try {
      setData(await api.del(`/api/settings/${encodeURIComponent(key)}`));
      const next = { ...dirty };
      delete next[key];
      setDirty(next);
      toast("Reset to default");
    } catch (err) {
      toastError(err);
    }
  };

  const dirtyCount = Object.keys(dirty).length;

  return h(Fragment, null,
    h("div.settings-section", null,
      h("h3", null, "Appearance"),
      h("div.setting-row", null,
        h("div.s-label", null, "Theme",
          h("span.s-key", null, "ui.theme")),
        h("select.input", {
          value: theme,
          onChange: (e) => setTheme(e.target.value),
          style: { maxWidth: "220px" },
          "aria-label": "Theme",
        },
          h("option", { value: "system" }, "Follow Windows setting"),
          h("option", { value: "light" }, "Light — green legal paper"),
          h("option", { value: "dark" }, "Dark — ink well"),
        ),
        h("span"),
      ),
    ),

    SECTIONS.map(([sectionTitle, keys]) =>
      h("div.settings-section", { key: sectionTitle },
        h("h3", null, sectionTitle),
        keys.filter(([key]) => key in data.defaults).map(([key, label]) =>
          h("div.setting-row", { key },
            h("div.s-label", null,
              label,
              h("span.s-key", null, key,
                data.overridden.includes(key) &&
                  h("span.overridden", null, " · changed"),
              ),
            ),
            h(SettingEditor, {
              value: valueOf(key),
              defaultValue: data.defaults[key],
              choices: CHOICES[key],
              onChange: (v) => setValue(key, v),
              label,
            }),
            data.overridden.includes(key)
              ? h("button.btn.sm", { onClick: resetKey(key) }, "Reset")
              : h("span"),
          ),
        ),
      ),
    ),

    dirtyCount > 0 && h("div", {
      style: {
        position: "sticky", bottom: 0, padding: "12px 0",
        background: "var(--paper)", display: "flex", gap: "8px",
        alignItems: "center", maxWidth: "760px",
      },
    },
      h("span.muted-note", null,
        `${dirtyCount} setting${dirtyCount === 1 ? "" : "s"} changed`),
      h("button.btn", {
        style: { marginLeft: "auto" },
        onClick: () => setDirty({}),
        disabled: busy,
      }, "Discard"),
      h("button.btn.primary", { onClick: save, disabled: busy },
        busy ? "Saving…" : "Save changes"),
    ),
  );
}

/* Typed editor chosen from the DEFAULT's type — the backend validates too. */
function SettingEditor({ value, defaultValue, choices, onChange, label }) {
  if (typeof defaultValue === "boolean") {
    return h("div", null, h(Toggle, { checked: !!value, onChange, label }));
  }
  if (choices) {
    return h("select.input", {
      value: String(value),
      onChange: (e) => onChange(e.target.value),
      style: { maxWidth: "220px" },
      "aria-label": label,
    }, choices.map((c) => h("option", { key: c, value: c }, c)));
  }
  if (typeof defaultValue === "number") {
    const isInt = Number.isInteger(defaultValue);
    return h("input.input.mono", {
      type: "number",
      step: isInt ? 1 : 0.1,
      value: value,
      style: { maxWidth: "140px" },
      onChange: (e) => {
        const n = isInt ? parseInt(e.target.value, 10) : parseFloat(e.target.value);
        if (!isNaN(n)) onChange(n);
      },
      "aria-label": label,
    });
  }
  if (Array.isArray(defaultValue)) {
    return h("textarea.input", {
      rows: Math.min(6, Math.max(2, (value || []).length)),
      value: (value || []).join("\n"),
      onChange: (e) =>
        onChange(e.target.value.split("\n").map((s) => s.trim()).filter(Boolean)),
      "aria-label": label,
    });
  }
  return h("input.input", {
    value: value === null || value === undefined ? "" : String(value),
    onChange: (e) => onChange(e.target.value),
    "aria-label": label,
  });
}

/* ---- Capabilities --------------------------------------------------------- */

const FEATURE_LABELS = {
  backend_api: "Backend API",
  website_analysis: "Website analysis",
  downloading: "Downloading",
  ocr: "OCR",
  local_ai: "Local AI",
  search: "Full-text search",
};

function CapabilitiesTab() {
  const { capabilities } = useStore();
  const [fresh, setFresh] = useState(capabilities);

  useEffect(() => {
    api.get("/api/capabilities").then(setFresh).catch(() => {});
  }, []);

  if (!fresh) return h("p.muted-note", null, "Checking this machine…");

  return h("div.settings-section", null,
    h("h3", null, "What's installed on this machine"),
    h("p.muted-note", { style: { marginTop: 0 } },
      "Each feature arrives in its own stage; a grey badge just means that stage's software isn't installed yet."),
    h("div.cap-grid", null,
      Object.entries(fresh.features).map(([key, feature]) =>
        h("div.cap-row", { key },
          h("span", {
            className: `pill ${feature.available ? "good" : ""}`,
          }, feature.available ? "ready" : `stage ${feature.stage}`),
          h("span", null, FEATURE_LABELS[key] || statusLabel(key)),
          feature.note && h("span.cap-note", null, feature.note),
          !feature.available && feature.missing && feature.missing.length > 0 &&
            h("span.cap-note.mono", null, `needs: ${feature.missing.join(", ")}`),
        ),
      ),
    ),
  );
}

/* ---- Activity log --------------------------------------------------------- */

function LogsTab() {
  const [items, setItems] = useState([]);
  const [nextBefore, setNextBefore] = useState(null);
  const [category, setCategory] = useState("");
  const [level, setLevel] = useState("");

  const load = async (before) => {
    try {
      const data = await api.get("/api/logs", {
        category, level, limit: 50, before_id: before,
      });
      if (before) {
        setItems((prev) => [...prev, ...data.items]);
      } else {
        setItems(data.items);
      }
      setNextBefore(data.next_before_id);
    } catch (err) {
      toastError(err);
    }
  };

  useEffect(() => { load(null); }, [category, level]);

  return h(Fragment, null,
    h("div.toolbar", null,
      h("select.input", {
        value: category,
        onChange: (e) => setCategory(e.target.value),
        "aria-label": "Category",
      },
        h("option", { value: "" }, "All categories"),
        ["system", "analysis", "download", "ocr", "ai", "search"]
          .map((c) => h("option", { key: c, value: c }, c)),
      ),
      h("select.input", {
        value: level,
        onChange: (e) => setLevel(e.target.value),
        "aria-label": "Level",
      },
        h("option", { value: "" }, "All levels"),
        ["INFO", "WARNING", "ERROR", "DEBUG"]
          .map((l) => h("option", { key: l, value: l }, l)),
      ),
    ),
    h("div.table-wrap", null,
      h("table.table", null,
        h("tbody", null,
          items.map((row) =>
            h("tr.log-row", { key: row.id },
              h("td.dim", null, formatDateTime(row.created_at)),
              h("td", null,
                h("span", {
                  className: `pill ${row.level === "ERROR" ? "bad" : row.level === "WARNING" ? "wait" : ""}`,
                }, row.level),
              ),
              h("td.dim", null, row.category),
              h("td", null, row.message),
            ),
          ),
          items.length === 0 && h("tr", null,
            h("td.dim", { colSpan: 4, style: { textAlign: "center", padding: "24px" } },
              "No log entries match."),
          ),
        ),
      ),
    ),
    nextBefore && h("div", { style: { textAlign: "center", marginTop: "12px" } },
      h("button.btn", { onClick: () => load(nextBefore) }, "Show earlier entries"),
    ),
  );
}
