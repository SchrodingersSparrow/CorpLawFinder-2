/* Review queue: everything that needs a human decision. Nothing in the app
 * fails silently — it lands here instead. */

import { h, Fragment, useEffect, useState } from "../h.js";
import { api } from "../api.js";
import { formatDateTime, statusLabel, truncate } from "../format.js";
import { navigate, toast, toastError } from "../store.js";
import { EmptyState, Pager, StatusPill, Tabs } from "../components/ui.js";

const CATEGORY_LABELS = {
  download_failure: "Download failed",
  ocr_failure: "OCR failed",
  metadata_failure: "Details unclear",
  low_ai_confidence: "AI unsure",
  other: "Other",
};

export function ReviewScreen() {
  const [tab, setTab] = useState("open");
  const [page, setPage] = useState(1);
  const [result, setResult] = useState(null);

  const load = async () => {
    try {
      setResult(await api.get("/api/review", {
        status: tab === "all" ? "all" : tab,
        page,
        page_size: 25,
      }));
    } catch (err) {
      toastError(err);
    }
  };

  useEffect(() => { load(); }, [tab, page]);

  const settle = (item, status) => async () => {
    try {
      await api.post(`/api/review/${item.id}/resolve`, { status });
      toast(status === "resolved" ? "Marked resolved" : "Dismissed");
      load();
    } catch (err) {
      toastError(err);
    }
  };

  return h(Fragment, null,
    h("header.screen-head", null, h("h2.screen-title", null, "Review queue")),
    h("div.screen-body", null,
      h(Tabs, {
        tabs: [["open", "Open"], ["resolved", "Resolved"],
               ["dismissed", "Dismissed"], ["all", "All"]],
        active: tab,
        onSelect: (t) => { setTab(t); setPage(1); },
      }),

      result && result.total === 0
        ? h(EmptyState, { stamp: tab === "open" ? "Queue clear" : "Nothing here" },
            h("p", null, tab === "open"
              ? "Nothing needs your attention. Failed downloads, unreadable scans and uncertain AI results will appear here when the later stages are running."
              : "No items with this status."),
          )
        : h(Fragment, null,
            h("div.table-wrap", null,
              h("table.table", null,
                h("thead", null, h("tr", null,
                  h("th", null, "What happened"),
                  h("th", null, "Category"),
                  h("th", null, "Relates to"),
                  h("th", null, "When"),
                  h("th", null, ""),
                )),
                h("tbody", null,
                  (result ? result.items : []).map((item) =>
                    h("tr", { key: item.id },
                      h("td", null, truncate(item.detail, 90)),
                      h("td", null,
                        h("span.pill.bad", null,
                          CATEGORY_LABELS[item.category] || statusLabel(item.category)),
                      ),
                      h("td", null,
                        item.document_id
                          ? h("button.btn.ghost.sm", {
                              onClick: () => navigate("documents", { select: item.document_id }),
                            }, truncate(item.document_title || `document #${item.document_id}`, 40))
                          : item.source_url
                            ? h("span.mono.dim", null, truncate(item.source_url, 40))
                            : h("span.dim", null, "—"),
                      ),
                      h("td.mono.dim", null, formatDateTime(item.created_at)),
                      h("td", null,
                        item.status === "open"
                          ? h("div", { style: { display: "flex", gap: "4px", justifyContent: "flex-end" } },
                              h("button.btn.sm", { onClick: settle(item, "resolved") }, "Resolve"),
                              h("button.btn.sm", { onClick: settle(item, "dismissed") }, "Dismiss"),
                            )
                          : h(StatusPill, { status: item.status }),
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
  );
}
