/* The docket-spine sidebar: navigation, live counts, backend status. */

import { h } from "../h.js";
import { navigate } from "../store.js";
import { useStore } from "./hooks.js";

const ITEMS = [
  ["dashboard", "Dashboard"],
  ["sources", "Sources"],
  ["downloads", "Downloads"],
  ["documents", "Documents"],
  ["search", "Search"],
  ["review", "Review queue"],
  ["settings", "Settings"],
];

const HEALTH_LABEL = {
  starting: "backend starting…",
  ok: "backend running",
  down: "backend not reachable",
};

export function Sidebar() {
  const { screen, health, activeJobs, openReviewCount } = useStore();

  return h("aside.sidebar", null,
    h("div.sidebar-brand", null,
      h("h1.brand-name", null, "Legal Knowledge", h("br"), "Manager"),
      h("div.brand-sub", null, "Private library"),
    ),
    h("nav.nav", { "aria-label": "Main" },
      ITEMS.map(([id, label]) => {
        let count = null;
        let alert = false;
        if (id === "review" && openReviewCount > 0) {
          count = openReviewCount;
          alert = true; // red tape: needs a human
        }
        return h("button", {
          key: id,
          className: `nav-item ${screen === id ? "active" : ""}`,
          "aria-current": screen === id ? "page" : undefined,
          onClick: () => navigate(id),
        },
          label,
          count !== null && h("span", {
            className: `nav-count ${alert ? "alert" : ""}`,
          }, count),
        );
      }),
    ),
    h("div.sidebar-foot", null,
      activeJobs.length > 0 && h("span.muted-note", null,
        `${activeJobs.length} job${activeJobs.length === 1 ? "" : "s"} running`),
      h("span", {
        className: `backend-dot ${health}`,
        title: HEALTH_LABEL[health] || health,
      }, HEALTH_LABEL[health] || health),
    ),
  );
}
