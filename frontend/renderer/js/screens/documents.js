/* Documents: the library itself. Filterable docket list on the left, a
 * resizable detail panel on the right. */

import { h, Fragment, useEffect, useRef, useState } from "../h.js";
import { api } from "../api.js";
import {
  formatDate, formatDateTime, formatSize, spineClass, statusLabel, truncate,
} from "../format.js";
import { toast, toastError } from "../store.js";
import { useStore } from "../components/hooks.js";
import {
  AuthorityChip, Confirm, EmptyState, Field, Modal, Pager, StatusPill,
} from "../components/ui.js";

export function DocumentsScreen() {
  const { screenParams } = useStore();
  const [filters, setFilters] = useState({
    q: "", authority: "", doc_type: "", file_kind: "",
    ocr_status: screenParams.ocr_status || "",
  });
  const [page, setPage] = useState(1);
  const [result, setResult] = useState(null);
  const [facets, setFacets] = useState({ authorities: [], doc_types: [], file_kinds: [] });
  const [selectedId, setSelectedId] = useState(screenParams.select || null);

  const setFilter = (key) => (e) => {
    setFilters({ ...filters, [key]: e.target.value });
    setPage(1);
  };

  const load = async () => {
    try {
      setResult(await api.get("/api/documents", { ...filters, page, page_size: 25 }));
    } catch (err) {
      toastError(err);
    }
  };

  useEffect(() => { load(); }, [JSON.stringify(filters), page]);
  useEffect(() => {
    api.get("/api/documents/facets").then(setFacets).catch(() => {});
  }, []);
  useEffect(() => {
    if (screenParams.select) setSelectedId(screenParams.select);
    if (screenParams.ocr_status !== undefined) {
      setFilters((f) => ({ ...f, ocr_status: screenParams.ocr_status }));
    }
  }, [screenParams]);

  const select = (id) => setSelectedId(id === selectedId ? null : id);

  return h(Fragment, null,
    h("header.screen-head", null,
      h("h2.screen-title", null, "Documents"),
      h("div.head-actions", null,
        result && h("span.muted-note", null,
          `${result.total} document${result.total === 1 ? "" : "s"}`),
      ),
    ),
    h("div.screen-body.flush", null,
      h(SplitPanels, {
        detailOpen: selectedId !== null,
        list: h("div", null,
          h("div.toolbar", null,
            h("input.input.grow", {
              placeholder: "Filter by title or filename…",
              value: filters.q,
              onChange: setFilter("q"),
            }),
            h(FacetSelect, {
              value: filters.authority, onChange: setFilter("authority"),
              options: facets.authorities, placeholder: "Authority",
            }),
            h(FacetSelect, {
              value: filters.doc_type, onChange: setFilter("doc_type"),
              options: facets.doc_types, placeholder: "Type",
            }),
            h(FacetSelect, {
              value: filters.file_kind, onChange: setFilter("file_kind"),
              options: facets.file_kinds, placeholder: "Format",
            }),
            h("select.input", {
              value: filters.ocr_status, onChange: setFilter("ocr_status"),
              "aria-label": "OCR status",
            },
              h("option", { value: "" }, "OCR: any"),
              ["not_required", "required", "queued", "running", "completed", "failed"]
                .map((s) => h("option", { key: s, value: s }, `OCR: ${statusLabel(s)}`)),
            ),
          ),

          result && result.total === 0
            ? h(EmptyState, { stamp: "Shelf empty" },
                h("p", null, Object.values(filters).some(Boolean)
                  ? "No documents match these filters."
                  : "Documents appear here once downloading begins in Stage 4."),
              )
            : h(Fragment, null,
                h("div.table-wrap", null,
                  h("table.table", null,
                    h("tbody", null,
                      (result ? result.items : []).map((doc) =>
                        h("tr.selectable", {
                          key: doc.id,
                          className: `selectable ${doc.id === selectedId ? "selected" : ""}`,
                          onClick: () => select(doc.id),
                        },
                          h("td", { className: `spined ${spineClass(doc.authority)}` },
                            h("div.doc-title", null,
                              truncate(doc.title || doc.original_filename, 64)),
                            h("div.mono.dim", null,
                              [doc.doc_type, formatDate(doc.doc_date)]
                                .filter((v) => v && v !== "—").join(" · ") || doc.file_kind),
                          ),
                          h("td", null, h(AuthorityChip, { authority: doc.authority })),
                          h("td.mono.dim", null, doc.file_kind),
                          h("td.num", null, formatSize(doc.file_size_bytes)),
                          h("td", null,
                            doc.ocr_status !== "not_required" &&
                              h(StatusPill, { status: doc.ocr_status }),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
                result && h("div", { style: { padding: "0 4px" } },
                  h(Pager, {
                    page: result.page, pages: result.pages, total: result.total,
                    onPage: setPage,
                  }),
                ),
              ),
        ),
        detail: selectedId !== null && h(DocumentDetail, {
          documentId: selectedId,
          onClose: () => setSelectedId(null),
          onChanged: load,
        }),
      }),
    ),
  );
}

function FacetSelect({ value, onChange, options, placeholder }) {
  return h("select.input", { value, onChange, "aria-label": placeholder },
    h("option", { value: "" }, placeholder + ": any"),
    (options || []).map((o) => h("option", { key: o, value: o }, o)),
  );
}

/* ---- Split panels with a draggable, persisted divider -------------------- */

function SplitPanels({ list, detail, detailOpen }) {
  const containerRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [width, setWidth] = useState(() => {
    try {
      return parseInt(localStorage.getItem("lkm.detailWidth"), 10) || 460;
    } catch {
      return 460;
    }
  });

  const onDrag = (e) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const next = Math.min(Math.max(rect.right - e.clientX, 300), rect.width * 0.7);
    setWidth(next);
  };

  const stop = () => {
    setDragging(false);
    try { localStorage.setItem("lkm.detailWidth", String(Math.round(width))); } catch { /* fine */ }
  };

  useEffect(() => {
    if (!dragging) return;
    window.addEventListener("mousemove", onDrag);
    window.addEventListener("mouseup", stop);
    return () => {
      window.removeEventListener("mousemove", onDrag);
      window.removeEventListener("mouseup", stop);
    };
  }, [dragging, width]);

  return h("div.split", { ref: containerRef },
    h("div.split-list", null, list),
    detailOpen && h(Fragment, null,
      h("div", {
        className: `split-divider ${dragging ? "dragging" : ""}`,
        role: "separator",
        "aria-orientation": "vertical",
        onMouseDown: (e) => { e.preventDefault(); setDragging(true); },
      }),
      h("div.split-detail", { style: { "--detail-w": width + "px" } }, detail),
    ),
  );
}

/* ---- Detail panel -------------------------------------------------------- */

function DocumentDetail({ documentId, onClose, onChanged }) {
  const [doc, setDoc] = useState(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteFile, setDeleteFile] = useState(true);
  const [text, setText] = useState(null);
  const [tagInput, setTagInput] = useState("");

  const load = async () => {
    try {
      setDoc(await api.get(`/api/documents/${documentId}`));
    } catch (err) {
      toastError(err);
      onClose();
    }
  };

  useEffect(() => { setDoc(null); setText(null); setEditing(false); load(); },
    [documentId]);

  if (!doc) return h("p.muted-note", null, "Loading…");

  const run = (verb, okMessage) => async () => {
    try {
      await api.post(`/api/documents/${documentId}/${verb}`);
      toast(okMessage);
    } catch (err) {
      toastError(err);
    }
  };

  const openFile = () => {
    if (globalThis.lkm && globalThis.lkm.openDocument) {
      globalThis.lkm.openDocument(documentId).catch(() => {
        toast("The file could not be opened — it may not exist yet.", "error");
      });
    } else {
      window.open(api.fileUrl(documentId));
    }
  };

  const addTag = async () => {
    const name = tagInput.trim();
    if (!name) return;
    try {
      const tags = await api.post(`/api/documents/${documentId}/tags`, { name });
      setDoc({ ...doc, tags });
      setTagInput("");
      onChanged();
    } catch (err) {
      toastError(err);
    }
  };

  const removeTag = (tag) => async () => {
    try {
      const tags = await api.del(`/api/documents/${documentId}/tags/${tag.id}`);
      setDoc({ ...doc, tags });
      onChanged();
    } catch (err) {
      toastError(err);
    }
  };

  const loadText = async () => {
    try {
      setText(await api.get(`/api/documents/${documentId}/text`));
    } catch (err) {
      toastError(err);
    }
  };

  return h(Fragment, null,
    h("div", { style: { display: "flex", alignItems: "flex-start", gap: "8px" } },
      h("h3.detail-title", { style: { flex: 1 } },
        doc.title || doc.original_filename),
      h("button.btn.sm", { onClick: onClose, "aria-label": "Close details" }, "✕"),
    ),
    h("div.tag-row", null,
      h(AuthorityChip, { authority: doc.authority }),
      doc.doc_type && h("span.pill", null, doc.doc_type),
      doc.ocr_status !== "not_required" && h(StatusPill, { status: doc.ocr_status }),
    ),

    h("dl.kv", null,
      h("dt", null, "Date"), h("dd", null, formatDate(doc.doc_date)),
      h("dt", null, "File"), h("dd.mono", null, doc.stored_filename || doc.original_filename),
      h("dt", null, "Size"), h("dd.mono", null, formatSize(doc.file_size_bytes)),
      h("dt", null, "Downloaded"), h("dd.mono", null, formatDateTime(doc.downloaded_at)),
      doc.source_url && h(Fragment, null,
        h("dt", null, "From"), h("dd.mono", null, truncate(doc.source_url, 60)),
      ),
      h("dt", null, "SHA-256"), h("dd.mono", null, truncate(doc.sha256, 24)),
    ),

    h("div", { style: { display: "flex", flexWrap: "wrap", gap: "6px" } },
      h("button.btn.sm", { onClick: openFile }, "Open file"),
      globalThis.lkm && globalThis.lkm.revealDocument &&
        h("button.btn.sm", {
          onClick: () => globalThis.lkm.revealDocument(documentId).catch(() => {}),
        }, "Show in folder"),
      h("button.btn.sm", { onClick: () => setEditing(true) }, "Correct details"),
      h("button.btn.sm", { onClick: run("ocr", "OCR queued") }, "Run OCR"),
      h("button.btn.sm", { onClick: run("summarize", "Summary queued") }, "Summarise"),
      h("button.btn.sm.danger", { onClick: () => setDeleting(true) }, "Delete"),
    ),

    h("hr.rule"),

    h("h4", { style: { margin: "0 0 8px", fontSize: "var(--text-sm)" } }, "Tags"),
    h("div.tag-row", null,
      (doc.tags || []).map((tag) =>
        h("span.tag", { key: tag.id },
          tag.name,
          h("button", { onClick: removeTag(tag), "aria-label": `Remove tag ${tag.name}` }, "✕"),
        ),
      ),
      h("input.input", {
        style: { width: "140px" },
        placeholder: "Add tag…",
        value: tagInput,
        onChange: (e) => setTagInput(e.target.value),
        onKeyDown: (e) => { if (e.key === "Enter") addTag(); },
      }),
    ),

    h("hr.rule"),

    h("h4", { style: { margin: "0 0 8px", fontSize: "var(--text-sm)" } }, "Summary"),
    doc.summaries && doc.summaries.length > 0 && doc.summaries[0].one_line_summary
      ? h(Fragment, null,
          h("p", { style: { marginTop: 0 } }, doc.summaries[0].one_line_summary),
          doc.summaries[0].detailed_summary &&
            h("p.dim", { style: { fontSize: "var(--text-sm)" } },
              doc.summaries[0].detailed_summary),
        )
      : h("p.muted-note", null,
          "No summary yet — press Summarise, or let auto-summarising fill this in."),

    h("hr.rule"),

    h("h4", { style: { margin: "0 0 8px", fontSize: "var(--text-sm)" } }, "Extracted text"),
    text === null
      ? h("button.btn.sm", { onClick: loadText }, "Show extracted text")
      : text.kind === "none"
        ? h("p.muted-note", null,
            "No text extracted yet. If this is a scanned document, Run OCR will read it.")
        : h(Fragment, null,
            h("p.muted-note", null,
              `${text.kind === "ocr" ? "From OCR" : "Native text"} · ${text.length.toLocaleString()} characters`),
            h("div.text-preview", null, truncate(text.text, 20000)),
          ),

    doc.metadata && doc.metadata.length > 0 && h(Fragment, null,
      h("hr.rule"),
      h("h4", { style: { margin: "0 0 8px", fontSize: "var(--text-sm)" } },
        "Field history"),
      h("div.table-wrap", null,
        h("table.table", null,
          h("thead", null, h("tr", null,
            h("th", null, "Field"), h("th", null, "Value"),
            h("th", null, "By"), h("th", { className: "num" }, "Confidence"),
          )),
          h("tbody", null,
            doc.metadata.map((m) =>
              h("tr", { key: m.field },
                h("td.mono", null, m.field),
                h("td", null, truncate(m.value, 48)),
                h("td.dim", null, m.extractor),
                h("td.num", null,
                  m.confidence !== null && m.confidence !== undefined
                    ? Math.round(m.confidence * 100) + "%" : "—"),
              ),
            ),
          ),
        ),
      ),
    ),

    editing && h(EditDocumentModal, {
      doc,
      onClose: () => setEditing(false),
      onDone: () => { setEditing(false); load(); onChanged(); },
    }),

    deleting && h(Confirm, {
      title: "Delete document",
      danger: true,
      confirmLabel: "Delete document",
      message: `Remove “${truncate(doc.title || doc.original_filename, 60)}” from the library?`,
      onClose: () => setDeleting(false),
      onConfirm: async () => {
        try {
          await api.del(`/api/documents/${documentId}`, { delete_file: deleteFile });
          toast("Document deleted");
          setDeleting(false);
          onClose();
          onChanged();
        } catch (err) {
          toastError(err);
        }
      },
    },
      h("label", {
        style: { display: "flex", gap: "8px", alignItems: "center",
                 fontSize: "var(--text-sm)", marginTop: "12px" },
      },
        h("input", {
          type: "checkbox",
          checked: deleteFile,
          onChange: (e) => setDeleteFile(e.target.checked),
        }),
        "Also delete the file from the library folder",
      ),
    ),
  );
}

function EditDocumentModal({ doc, onClose, onDone }) {
  const [fields, setFields] = useState({
    title: doc.title || "",
    authority: doc.authority || "",
    doc_type: doc.doc_type || "",
    doc_date: doc.doc_date || "",
    language: doc.language || "",
  });
  const [busy, setBusy] = useState(false);

  const set = (key) => (e) => setFields({ ...fields, [key]: e.target.value });

  const save = async () => {
    setBusy(true);
    try {
      const changed = {};
      const original = {
        title: doc.title || "", authority: doc.authority || "",
        doc_type: doc.doc_type || "", doc_date: doc.doc_date || "",
        language: doc.language || "",
      };
      for (const [key, value] of Object.entries(fields)) {
        if (value !== original[key]) changed[key] = value || null;
      }
      if (Object.keys(changed).length === 0) {
        onClose();
        return;
      }
      await api.patch(`/api/documents/${doc.id}`, changed);
      toast("Details corrected");
      onDone();
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  return h(Modal2, {
    title: "Correct details",
    onClose,
    busy,
    onSave: save,
  },
    h(Field, { label: "Title" },
      h("input.input", { value: fields.title, onChange: set("title") })),
    h(Field, { label: "Authority" },
      h("input.input", { value: fields.authority, onChange: set("authority") })),
    h(Field, { label: "Document type" },
      h("input.input", { value: fields.doc_type, onChange: set("doc_type") })),
    h(Field, { label: "Date", hint: "YYYY-MM-DD" },
      h("input.input.mono", {
        value: fields.doc_date, onChange: set("doc_date"),
        placeholder: "2025-01-04",
      })),
    h(Field, { label: "Language" },
      h("input.input", { value: fields.language, onChange: set("language") })),
    h("p.muted-note", { style: { margin: 0 } },
      "Corrections are recorded in the field history as made by you, and search is updated immediately."),
  );
}

/* Small wrapper so the edit modal reuses Modal with a standard footer. */

function Modal2({ title, onClose, onSave, busy, children }) {
  return h(Modal, {
    title,
    onClose,
    footer: h(Fragment, null,
      h("button.btn", { onClick: onClose, disabled: busy }, "Cancel"),
      h("button.btn.primary", { onClick: onSave, disabled: busy },
        busy ? "Saving…" : "Save changes"),
    ),
  }, children);
}
