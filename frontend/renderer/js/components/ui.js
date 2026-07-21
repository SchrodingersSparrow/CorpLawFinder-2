/* Shared building blocks used by every screen. */

import { h, Fragment, useEffect, useRef, useState } from "../h.js";
import { statusLabel, toneFor } from "../format.js";
import { store, useStore } from "./hooks.js";

export function StatusPill({ status }) {
  const tone = toneFor(status);
  return h("span", { className: `pill ${tone}` }, statusLabel(status));
}

export function AuthorityChip({ authority }) {
  if (!authority) return null;
  return h("span.authority-chip", null, authority);
}

export function EmptyState({ stamp, children }) {
  return h("div.empty", null,
    stamp && h("div.empty-stamp", null, stamp),
    children,
  );
}

export function Pager({ page, pages, total, onPage }) {
  if (!total) return null;
  return h("div.pager", null,
    h("span", null, `${total} record${total === 1 ? "" : "s"}`),
    pages > 1 && h(Fragment, null,
      h("button.btn.sm", {
        disabled: page <= 1,
        onClick: () => onPage(page - 1),
      }, "‹ Prev"),
      h("span", null, `page ${page} / ${pages}`),
      h("button.btn.sm", {
        disabled: page >= pages,
        onClick: () => onPage(page + 1),
      }, "Next ›"),
    ),
  );
}

export function Tabs({ tabs, active, onSelect }) {
  return h("div.tabs", { role: "tablist" },
    tabs.map(([id, label]) =>
      h("button", {
        key: id,
        role: "tab",
        "aria-selected": active === id,
        className: `tab ${active === id ? "active" : ""}`,
        onClick: () => onSelect(id),
      }, label),
    ),
  );
}

export function Toggle({ checked, onChange, label }) {
  return h("button", {
    className: "toggle",
    role: "switch",
    "aria-checked": String(!!checked),
    "aria-label": label,
    onClick: () => onChange(!checked),
  });
}

export function Field({ label, hint, children }) {
  return h("div.field", null,
    label && h("label", null, label),
    children,
    hint && h("span.hint", null, hint),
  );
}

/* Modal with Escape-to-close and initial focus. */
export function Modal({ title, onClose, footer, wide, children }) {
  const ref = useRef(null);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    const first = ref.current && ref.current.querySelector("input, select, textarea, button");
    if (first) first.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return h("div.modal-backdrop", {
    onMouseDown: (e) => { if (e.target === e.currentTarget) onClose(); },
  },
    h("div.modal", {
      ref,
      role: "dialog",
      "aria-modal": "true",
      style: wide ? { width: "min(760px, 100%)" } : null,
    },
      h("div.modal-head", null,
        h("h2", null, title),
        h("button.btn.sm", { onClick: onClose, "aria-label": "Close" }, "✕"),
      ),
      h("div.modal-body", null, children),
      footer && h("div.modal-foot", null, footer),
    ),
  );
}

/* One-question confirmation dialog. */
export function Confirm({ title, message, confirmLabel, danger, onConfirm, onClose, children }) {
  const [busy, setBusy] = useState(false);
  return h(Modal, {
    title,
    onClose,
    footer: h(Fragment, null,
      h("button.btn", { onClick: onClose, disabled: busy }, "Cancel"),
      h("button", {
        className: `btn ${danger ? "danger" : "primary"}`,
        disabled: busy,
        onClick: async () => {
          setBusy(true);
          try { await onConfirm(); } finally { setBusy(false); }
        },
      }, confirmLabel || "Confirm"),
    ),
  }, h("p", { style: { margin: 0 } }, message), children);
}

export function Toasts() {
  const { toasts } = useStore();
  return h("div.toasts", null,
    toasts.map((t) =>
      h("div", { key: t.id, className: `toast ${t.kind}` }, t.message),
    ),
  );
}

/* Loading placeholder that never flashes for fast responses. */
export function Loading({ shown }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!shown) { setVisible(false); return; }
    const t = setTimeout(() => setVisible(true), 250);
    return () => clearTimeout(t);
  }, [shown]);
  if (!shown || !visible) return null;
  return h("div.empty", null, h("span.muted-note", null, "Loading…"));
}
