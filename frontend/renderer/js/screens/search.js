/* Search: full-text over titles, document text, OCR text, summaries, tags. */

import { h, Fragment, useEffect, useRef, useState } from "../h.js";
import { api } from "../api.js";
import { formatDate, spineClass, splitSnippet, truncate } from "../format.js";
import { navigate, toast, toastError } from "../store.js";
import { AuthorityChip, EmptyState, Pager } from "../components/ui.js";

export function SearchScreen() {
  const [q, setQ] = useState("");
  const [authority, setAuthority] = useState("");
  const [fileKind, setFileKind] = useState("");
  const [sort, setSort] = useState("relevance");
  const [page, setPage] = useState(1);
  const [result, setResult] = useState(null);
  const [facets, setFacets] = useState({ authorities: [], file_kinds: [] });
  const [saved, setSaved] = useState([]);
  const [savingName, setSavingName] = useState(null); // null = closed
  const debounceRef = useRef(null);

  const loadSaved = () =>
    api.get("/api/saved-searches").then((r) => setSaved(r.items)).catch(() => {});

  useEffect(() => {
    api.get("/api/documents/facets").then(setFacets).catch(() => {});
    loadSaved();
  }, []);

  const saveCurrent = async () => {
    const name = (savingName || "").trim();
    if (!name) return;
    try {
      await api.post("/api/saved-searches", {
        name, query: q,
        filters: { authority, file_kind: fileKind, sort },
      });
      setSavingName(null);
      toast("Search saved");
      loadSaved();
    } catch (err) { toastError(err); }
  };

  const recallSaved = async (item) => {
    try {
      const full = await api.post(`/api/saved-searches/${item.id}/use`);
      setQ(full.query);
      setAuthority(full.filters.authority || "");
      setFileKind(full.filters.file_kind || "");
      setSort(full.filters.sort || "relevance");
      setPage(1);
      loadSaved(); // re-order by recency
    } catch (err) { toastError(err); }
  };

  const deleteSaved = async (item) => {
    try {
      await api.del(`/api/saved-searches/${item.id}`);
      toast(`Deleted “${item.name}”`);
      loadSaved();
    } catch (err) { toastError(err); }
  };

  const search = async (query, pageNumber) => {
    if (!query.trim()) {
      setResult(null);
      return;
    }
    try {
      setResult(await api.get("/api/search", {
        q: query,
        authority,
        file_kind: fileKind,
        sort,
        page: pageNumber,
        page_size: 25,
      }));
    } catch (err) {
      if (err.code !== "invalid_input") toastError(err);
    }
  };

  /* Debounced search-as-you-type */
  useEffect(() => {
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(q, page), 300);
    return () => clearTimeout(debounceRef.current);
  }, [q, authority, fileKind, sort, page]);

  return h(Fragment, null,
    h("header.screen-head", null, h("h2.screen-title", null, "Search")),
    h("div.screen-body", null,
      h("div.search-hero", null,
        h("input.input", {
          placeholder: "Search your whole library…",
          value: q,
          autoFocus: true,
          onChange: (e) => { setQ(e.target.value); setPage(1); },
          "aria-label": "Search query",
        }),
        h("div.toolbar", { style: { marginTop: "10px", marginBottom: 0 } },
          h("select.input", {
            value: authority,
            onChange: (e) => { setAuthority(e.target.value); setPage(1); },
            "aria-label": "Authority filter",
          },
            h("option", { value: "" }, "Authority: any"),
            facets.authorities.map((a) => h("option", { key: a, value: a }, a)),
          ),
          h("select.input", {
            value: fileKind,
            onChange: (e) => { setFileKind(e.target.value); setPage(1); },
            "aria-label": "Format filter",
          },
            h("option", { value: "" }, "Format: any"),
            facets.file_kinds.map((k) => h("option", { key: k, value: k }, k)),
          ),
          h("select.input", {
            value: sort,
            onChange: (e) => { setSort(e.target.value); setPage(1); },
            "aria-label": "Sort order",
          },
            h("option", { value: "relevance" }, "Sort: relevance"),
            h("option", { value: "newest" }, "Sort: newest first"),
            h("option", { value: "oldest" }, "Sort: oldest first"),
          ),
          q.trim() && (savingName === null
            ? h("button.btn.sm", { onClick: () => setSavingName("") },
                "Save this search")
            : h(Fragment, null,
                h("input.input", {
                  placeholder: "Name this search…", value: savingName,
                  autoFocus: true,
                  style: { maxWidth: "220px" },
                  onChange: (e) => setSavingName(e.target.value),
                  onKeyDown: (e) => { if (e.key === "Enter") saveCurrent(); },
                  "aria-label": "Saved search name",
                }),
                h("button.btn.sm.primary", { onClick: saveCurrent }, "Save"),
                h("button.btn.sm", { onClick: () => setSavingName(null) }, "Cancel"),
              )),
        ),
        h("span.muted-note", { style: { display: "block", marginTop: "8px" } },
          "Phrases in quotes (“master direction”), OR between terms, " +
          "-word to exclude, * for prefixes"),
        saved.length > 0 && h("div.toolbar", {
          style: { marginTop: "10px", marginBottom: 0, flexWrap: "wrap" },
        },
          h("span.dim", { style: { fontSize: "var(--text-xs)" } }, "Saved:"),
          saved.map((item) =>
            h("span.tag", { key: item.id, title: item.query },
              h("a", {
                href: "#",
                style: { color: "inherit", textDecoration: "none" },
                onClick: (e) => { e.preventDefault(); recallSaved(item); },
              }, item.name),
              h("button", {
                "aria-label": `Delete saved search ${item.name}`,
                onClick: () => deleteSaved(item),
              }, "×"),
            ),
          ),
        ),
      ),

      !q.trim()
        ? h(EmptyState, { stamp: "Registry search" },
            h("p", null, "Searches titles, extracted text, OCR text, summaries and tags — ranked by relevance, with the matching passage shown."),
          )
        : result && result.total === 0
          ? h(EmptyState, { stamp: "No entries found" },
              h("p", null, `Nothing in the library matches “${truncate(q, 40)}”.`),
            )
          : h(Fragment, null,
              (result ? result.items : []).map((item) =>
                h("div", {
                  key: item.id,
                  className: `result spined ${spineClass(item.authority)}`,
                  role: "button",
                  tabIndex: 0,
                  onClick: () => navigate("documents", { select: item.id }),
                  onKeyDown: (e) => {
                    if (e.key === "Enter") navigate("documents", { select: item.id });
                  },
                },
                  h("div", { style: { display: "flex", gap: "8px", alignItems: "baseline" } },
                    h("span.doc-title", null,
                      truncate(item.title || item.original_filename, 90)),
                    h("span", { style: { marginLeft: "auto" } },
                      h(AuthorityChip, { authority: item.authority })),
                  ),
                  h("div.mono.dim", { style: { fontSize: "var(--text-xs)" } },
                    [item.doc_type, formatDate(item.doc_date)]
                      .filter((v) => v && v !== "—").join(" · ")),
                  item.snippet && h("p.snippet", null,
                    splitSnippet(item.snippet).map((part, i) =>
                      part.hit
                        ? h("mark", { key: i }, part.text)
                        : h("span", { key: i }, part.text),
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
  );
}
