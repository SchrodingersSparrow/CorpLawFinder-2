/* Dashboard: the registry's front page. */

import { h, Fragment, useEffect, useState } from "../h.js";
import { api } from "../api.js";
import {
  formatDateTime, gazetteDate, hostOf, spineClass, timeAgo, truncate,
} from "../format.js";
import { cancelJob, navigate, toastError } from "../store.js";
import { useStore } from "../components/hooks.js";
import { AuthorityChip, EmptyState, StatusPill } from "../components/ui.js";

export function DashboardScreen() {
  const { activeJobs, health } = useStore();
  const [data, setData] = useState(null);

  const load = async () => {
    try {
      setData(await api.get("/api/dashboard"));
    } catch (err) {
      if (health === "ok") toastError(err);
    }
  };

  useEffect(() => { load(); }, [health, activeJobs.length]);

  const counts = data ? data.counts : {};
  const cards = [
    ["Sources", counts.total_sources, () => navigate("sources")],
    ["Documents", counts.total_documents, () => navigate("documents")],
    ["Need OCR", counts.documents_needing_ocr,
      () => navigate("documents", { ocr_status: "required" })],
    ["Downloaded today", counts.downloaded_today, () => navigate("downloads")],
    ["Open review items", counts.open_review_items, () => navigate("review"), true],
  ];

  return h(Fragment, null,
    h("header.screen-head", null, h("h2.screen-title", null, "Dashboard")),
    h("div.screen-body", null,
      h("div.gazette-line", null, gazetteDate()),

      h("div.cards", null,
        cards.map(([label, value, go, alert], i) =>
          h("div", {
            key: label,
            className: `count-card rise ${i % 3 === 1 ? "d1" : i % 3 === 2 ? "d2" : ""} ${alert && value > 0 ? "alert" : ""}`,
            role: "button",
            tabIndex: 0,
            onClick: go,
            onKeyDown: (e) => { if (e.key === "Enter") go(); },
          },
            h("div.count-num", null, value === undefined ? "–" : value),
            h("div.count-label", null, label),
          ),
        ),
      ),

      activeJobs.length > 0 && h(Fragment, null,
        h("h3.section-title", null, "Happening now"),
        h("div.table-wrap", null,
          activeJobs.map((job) =>
            h("div.job-row", { key: job.id },
              h(StatusPill, { status: job.status }),
              h("span", null, jobLabel(job)),
              h("span.muted-note", { style: { marginLeft: "auto" } },
                timeAgo(job.created_at)),
              h("button.btn.sm", { onClick: () => cancelJob(job.id) }, "Cancel"),
            ),
          ),
        ),
      ),

      h("h3.section-title", null, "Recent documents"),
      data && data.recent_documents.length === 0
        ? h(EmptyState, { stamp: "Registry empty" },
            h("p", null, "Your library has no documents yet. Start by saving the web pages you work from — the app will fetch their documents in Stage 4."),
            h("button.btn.primary", { onClick: () => navigate("sources") },
              "Add your first source"),
          )
        : h("div.table-wrap", null,
            h("table.table", null,
              h("tbody", null,
                (data ? data.recent_documents : []).map((doc) =>
                  h("tr.selectable", {
                    key: doc.id,
                    onClick: () => navigate("documents", { select: doc.id }),
                  },
                    h("td", { className: `spined ${spineClass(doc.authority)}` },
                      h("span.doc-title", null,
                        truncate(doc.title || doc.original_filename, 80)),
                    ),
                    h("td", null, h(AuthorityChip, { authority: doc.authority })),
                    h("td.dim", null, doc.doc_type || "—"),
                    h("td.mono.dim", null, timeAgo(doc.downloaded_at)),
                  ),
                ),
              ),
            ),
          ),

      h("h3.section-title", null, "Recent sources"),
      data && data.recent_sources.length === 0
        ? h("p.muted-note", null, "No sources saved yet.")
        : h("div.table-wrap", null,
            h("table.table", null,
              h("tbody", null,
                (data ? data.recent_sources : []).map((src) =>
                  h("tr.selectable", {
                    key: src.id,
                    onClick: () => navigate("sources"),
                  },
                    h("td", null, truncate(src.title || src.url, 70)),
                    h("td.mono.dim", null, hostOf(src.url)),
                    h("td", null, h(StatusPill, { status: src.status })),
                    h("td.mono.dim", null, formatDateTime(src.date_added)),
                  ),
                ),
              ),
            ),
          ),
    ),
  );
}

function jobLabel(job) {
  const names = {
    analyze_source: "Analysing website",
    download_file: "Downloading",
    run_ocr: "Running OCR",
    ai_summarize: "Summarising",
  };
  return names[job.task_type] || job.task_type;
}
