/* Downloads: the fetch history. Real downloading arrives in Stage 4; the
 * screen is already live because rows are the durable record. */

import { h, Fragment, useEffect, useState } from "../h.js";
import { api } from "../api.js";
import { formatDateTime, hostOf, statusLabel, truncate } from "../format.js";
import { toast, toastError } from "../store.js";
import { useStore } from "../components/hooks.js";
import { EmptyState, Pager, StatusPill } from "../components/ui.js";

const STATUSES = ["queued", "running", "succeeded", "failed",
                  "skipped_duplicate", "cancelled"];

export function DownloadsScreen() {
  const { activeJobs } = useStore();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [result, setResult] = useState(null);
  const [counts, setCounts] = useState({});

  const load = async () => {
    try {
      const [pageData, countData] = await Promise.all([
        api.get("/api/downloads", { status, page, page_size: 25 }),
        api.get("/api/downloads/counts"),
      ]);
      setResult(pageData);
      setCounts(countData);
    } catch (err) {
      toastError(err);
    }
  };

  useEffect(() => { load(); }, [status, page, activeJobs.length]);

  const retry = (row) => async () => {
    try {
      await api.post(`/api/downloads/${row.id}/retry`);
      toast("Download re-queued");
      load();
    } catch (err) {
      toastError(err);
    }
  };

  return h(Fragment, null,
    h("header.screen-head", null, h("h2.screen-title", null, "Downloads")),
    h("div.screen-body", null,
      h("div.toolbar", null,
        h("select.input", {
          value: status,
          onChange: (e) => { setStatus(e.target.value); setPage(1); },
          "aria-label": "Filter by status",
        },
          h("option", { value: "" }, "All statuses"),
          STATUSES.map((s) => h("option", { key: s, value: s }, statusLabel(s))),
        ),
        h("span.muted-note", { style: { marginLeft: "auto" } },
          STATUSES.filter((s) => counts[s])
            .map((s) => `${statusLabel(s)}: ${counts[s]}`)
            .join("  ·  ") || "no downloads yet",
        ),
      ),

      result && result.total === 0
        ? h(EmptyState, { stamp: "Nothing fetched yet" },
            h("p", null, status
              ? "No downloads with this status."
              : "When Stage 4 arrives, every file the app fetches will be recorded here — with what succeeded, what was a duplicate, and what needs a retry."),
          )
        : h(Fragment, null,
            h("div.table-wrap", null,
              h("table.table", null,
                h("thead", null, h("tr", null,
                  h("th", null, "File / URL"),
                  h("th", null, "Status"),
                  h("th", { className: "num" }, "HTTP"),
                  h("th", { className: "num" }, "Tries"),
                  h("th", null, "Queued"),
                  h("th", null, "Finished"),
                  h("th", null, ""),
                )),
                h("tbody", null,
                  (result ? result.items : []).map((row) =>
                    h("tr", { key: row.id },
                      h("td", null,
                        h("div", null, truncate(row.stored_filename || row.url, 56)),
                        h("div.mono.dim", null, hostOf(row.url)),
                        row.error_message && h("div", {
                          className: "muted-note",
                          style: { color: "var(--tape)" },
                        }, truncate(row.error_message, 90)),
                      ),
                      h("td", null, h(StatusPill, { status: row.status })),
                      h("td.num", null, row.http_status || "—"),
                      h("td.num", null, row.attempts || 0),
                      h("td.mono.dim", null, formatDateTime(row.queued_at)),
                      h("td.mono.dim", null, formatDateTime(row.finished_at)),
                      h("td", null,
                        row.status === "failed" &&
                          h("button.btn.sm", { onClick: retry(row) }, "Retry"),
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
