/* Sources: the URLs the library grows from. */

import { h, Fragment, useEffect, useRef, useState } from "../h.js";
import { api } from "../api.js";
import { formatDateTime, hostOf, truncate } from "../format.js";
import { toast, toastError } from "../store.js";
import {
  Confirm, EmptyState, Field, Modal, Pager, StatusPill, Tabs,
} from "../components/ui.js";

export function SourcesScreen() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [result, setResult] = useState(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);

  const load = async () => {
    try {
      setResult(await api.get("/api/sources", { q, status, page, page_size: 25 }));
    } catch (err) {
      toastError(err);
    }
  };

  useEffect(() => { load(); }, [q, status, page]);

  const act = (source, verb) => async () => {
    try {
      await api.post(`/api/sources/${source.id}/${verb}`);
      toast(verb === "analyze" ? "Analysis queued" : "Download queued");
      load();
    } catch (err) {
      toastError(err);
    }
  };

  return h(Fragment, null,
    h("header.screen-head", null,
      h("h2.screen-title", null, "Sources"),
      h("div.head-actions", null,
        h("button.btn.primary", { onClick: () => setAdding(true) }, "Add URLs"),
      ),
    ),
    h("div.screen-body", null,
      h("div.toolbar", null,
        h("input.input.grow", {
          placeholder: "Filter by URL or title…",
          value: q,
          onChange: (e) => { setQ(e.target.value); setPage(1); },
        }),
        h("select.input", {
          value: status,
          onChange: (e) => { setStatus(e.target.value); setPage(1); },
          "aria-label": "Filter by status",
        },
          h("option", { value: "" }, "All statuses"),
          ["pending", "analyzing", "analyzed", "downloading", "completed", "failed"]
            .map((s) => h("option", { key: s, value: s }, s)),
        ),
      ),

      result && result.total === 0 && !q && !status
        ? h(EmptyState, { stamp: "No sources on file" },
            h("p", null, "Save the pages you work from — RBI notifications, SEBI circulars, MCA updates. One at a time, pasted as a list, or from a CSV file."),
            h("button.btn.primary", { onClick: () => setAdding(true) }, "Add URLs"),
          )
        : h(Fragment, null,
            h("div.table-wrap", null,
              h("table.table", null,
                h("thead", null, h("tr", null,
                  h("th", null, "Source"),
                  h("th", null, "Site"),
                  h("th", null, "Status"),
                  h("th", { className: "num" }, "Docs"),
                  h("th", null, "Added"),
                  h("th", null, ""),
                )),
                h("tbody", null,
                  (result ? result.items : []).map((src) =>
                    h("tr", { key: src.id },
                      h("td", null,
                        h("div", null, truncate(src.title || src.url, 60)),
                        src.title && h("div.mono.dim", null, truncate(src.url, 70)),
                        src.error_message && h("div", {
                          className: "muted-note",
                          style: { color: "var(--tape)" },
                        }, truncate(src.error_message, 90)),
                      ),
                      h("td.mono.dim", null, hostOf(src.url)),
                      h("td", null, h(StatusPill, { status: src.status })),
                      h("td.num", null, src.document_count || 0),
                      h("td.mono.dim", null, formatDateTime(src.date_added)),
                      h("td", null,
                        h("div", { style: { display: "flex", gap: "4px", justifyContent: "flex-end" } },
                          h("button.btn.sm", { onClick: act(src, "analyze") }, "Analyse"),
                          h("button.btn.sm", { onClick: act(src, "download") }, "Download"),
                          h("button.btn.sm", { onClick: () => setEditing(src) }, "Edit"),
                          h("button.btn.sm.danger", { onClick: () => setDeleting(src) }, "Delete"),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
            result && h(Pager, {
              page: result.page, pages: result.pages, total: result.total,
              onPage: setPage,
            }),
          ),
    ),

    adding && h(AddSourcesModal, {
      onClose: () => setAdding(false),
      onDone: () => { setAdding(false); load(); },
    }),
    editing && h(EditSourceModal, {
      source: editing,
      onClose: () => setEditing(null),
      onDone: () => { setEditing(null); load(); },
    }),
    deleting && h(Confirm, {
      title: "Delete source",
      danger: true,
      confirmLabel: "Delete source",
      message: `Remove “${truncate(deleting.title || deleting.url, 60)}” from your sources? Documents already downloaded from it stay in the library.`,
      onClose: () => setDeleting(null),
      onConfirm: async () => {
        try {
          await api.del(`/api/sources/${deleting.id}`);
          toast("Source deleted");
          setDeleting(null);
          load();
        } catch (err) {
          toastError(err);
        }
      },
    }),
  );
}

/* ---- Add URLs: single / paste list / CSV file ---------------------------- */

function AddSourcesModal({ onClose, onDone }) {
  const [tab, setTab] = useState("single");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);

  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [pasted, setPasted] = useState("");
  const fileRef = useRef(null);

  const submit = async () => {
    setBusy(true);
    try {
      if (tab === "single") {
        await api.post("/api/sources", {
          url, title: title || null, notes: notes || null,
        });
        toast("Source added");
        onDone();
      } else if (tab === "paste") {
        const entries = pasted.split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean)
          .map((line) => ({ url: line }));
        if (entries.length === 0) {
          toast("Paste at least one URL first", "error");
          return;
        }
        setReport(await api.post("/api/sources/batch", { sources: entries }));
      } else {
        const file = fileRef.current && fileRef.current.files[0];
        if (!file) {
          toast("Choose a CSV file first", "error");
          return;
        }
        const form = new FormData();
        form.append("file", file);
        setReport(await api.upload("/api/sources/import-csv", form));
      }
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  if (report) {
    return h(Modal, {
      title: "Import report",
      onClose: onDone,
      footer: h("button.btn.primary", { onClick: onDone }, "Done"),
    },
      h("p", { style: { margin: 0 } },
        `${report.added.length} added · ${report.duplicates.length} already saved · ${report.invalid.length} not valid`),
      report.duplicates.length > 0 && h(Field, { label: "Already saved (skipped)" },
        h("div.text-preview", null,
          report.duplicates.map((d) => `${d.url} — ${d.reason}`).join("\n")),
      ),
      report.invalid.length > 0 && h(Field, { label: "Not valid (skipped)" },
        h("div.text-preview", null,
          report.invalid.map((d) => `${d.url || "(empty)"} — ${d.reason}`).join("\n")),
      ),
    );
  }

  return h(Modal, {
    title: "Add URLs",
    onClose,
    footer: h(Fragment, null,
      h("button.btn", { onClick: onClose, disabled: busy }, "Cancel"),
      h("button.btn.primary", { onClick: submit, disabled: busy },
        busy ? "Adding…" : tab === "single" ? "Add source" : "Import"),
    ),
  },
    h(Tabs, {
      tabs: [["single", "One URL"], ["paste", "Paste a list"], ["csv", "CSV file"]],
      active: tab,
      onSelect: setTab,
    }),

    tab === "single" && h(Fragment, null,
      h(Field, { label: "URL", hint: "https:// is added automatically if missing" },
        h("input.input.mono", {
          value: url,
          onChange: (e) => setUrl(e.target.value),
          placeholder: "rbi.org.in/notifications",
        }),
      ),
      h(Field, { label: "Title (optional)" },
        h("input.input", {
          value: title,
          onChange: (e) => setTitle(e.target.value),
          placeholder: "RBI — Notifications",
        }),
      ),
      h(Field, { label: "Notes (optional)" },
        h("textarea.input", {
          value: notes,
          onChange: (e) => setNotes(e.target.value),
          rows: 2,
        }),
      ),
    ),

    tab === "paste" && h(Field, {
      label: "One URL per line",
      hint: "Duplicates and invalid lines are skipped and reported — nothing is lost silently.",
    },
      h("textarea.input", {
        value: pasted,
        onChange: (e) => setPasted(e.target.value),
        rows: 8,
        placeholder: "https://rbi.org.in/notifications\nhttps://www.sebi.gov.in/legal/circulars\n…",
      }),
    ),

    tab === "csv" && h(Field, {
      label: "CSV file",
      hint: "First column is the URL. A header row with url, title, notes columns also works.",
    },
      h("input.input", { type: "file", accept: ".csv,text/csv", ref: fileRef }),
    ),
  );
}

/* ---- Edit ---------------------------------------------------------------- */

function EditSourceModal({ source, onClose, onDone }) {
  const [title, setTitle] = useState(source.title || "");
  const [notes, setNotes] = useState(source.notes || "");
  const [authority, setAuthority] = useState(source.authority || "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await api.patch(`/api/sources/${source.id}`, {
        title: title || null,
        notes: notes || null,
        authority: authority || null,
      });
      toast("Source updated");
      onDone();
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  return h(Modal, {
    title: "Edit source",
    onClose,
    footer: h(Fragment, null,
      h("button.btn", { onClick: onClose, disabled: busy }, "Cancel"),
      h("button.btn.primary", { onClick: save, disabled: busy },
        busy ? "Saving…" : "Save changes"),
    ),
  },
    h(Field, { label: "URL" },
      h("input.input.mono", { value: source.url, disabled: true }),
    ),
    h(Field, { label: "Title" },
      h("input.input", { value: title, onChange: (e) => setTitle(e.target.value) }),
    ),
    h(Field, { label: "Authority", hint: "e.g. RBI, SEBI, MCA — colours the docket spine" },
      h("input.input", { value: authority, onChange: (e) => setAuthority(e.target.value) }),
    ),
    h(Field, { label: "Notes" },
      h("textarea.input", { value: notes, onChange: (e) => setNotes(e.target.value), rows: 3 }),
    ),
  );
}
