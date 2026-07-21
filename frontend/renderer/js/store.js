/* Tiny global store (pub/sub + useSyncExternalStore).
 *
 * Holds only what genuinely crosses screens: navigation, theme, backend
 * health, active jobs, open-review count and toasts. Screens keep their own
 * local state.
 */

import { api } from "./api.js";

const state = {
  screen: "dashboard",
  screenParams: {},
  theme: "system",            // "system" | "light" | "dark" (ui.theme setting)
  health: "starting",         // "starting" | "ok" | "down"
  capabilities: null,
  activeJobs: [],
  openReviewCount: 0,
  toasts: [],
};

const listeners = new Set();

function emit() {
  for (const listener of listeners) listener();
}

export const store = {
  getState: () => state,
  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function navigate(screen, params = {}) {
  state.screen = screen;
  state.screenParams = params;
  emit();
}

/* ---- theme ---------------------------------------------------------------
 * The preference lives in the backend's ui.theme setting so it survives
 * reinstalls with the data folder; localStorage caches it for instant paint.
 */

const media = typeof matchMedia === "function"
  ? matchMedia("(prefers-color-scheme: dark)")
  : null;

export function applyTheme(theme) {
  state.theme = theme;
  try { localStorage.setItem("lkm.theme", theme); } catch { /* fine */ }
  const dark = theme === "dark" || (theme === "system" && media && media.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  emit();
}

if (media) {
  media.addEventListener("change", () => {
    if (state.theme === "system") applyTheme("system");
  });
}

export async function setTheme(theme) {
  applyTheme(theme);
  try {
    await api.put("/api/settings", { values: { "ui.theme": theme } });
  } catch {
    /* offline backend — the local cache still applies it */
  }
}

export function initThemeFromCache() {
  let cached = "system";
  try { cached = localStorage.getItem("lkm.theme") || "system"; } catch { /* fine */ }
  applyTheme(cached);
}

/* ---- toasts -------------------------------------------------------------- */

let toastCounter = 0;

export function toast(message, kind = "plain", ms = 4200) {
  const id = ++toastCounter;
  state.toasts = [...state.toasts, { id, message, kind }];
  emit();
  setTimeout(() => {
    state.toasts = state.toasts.filter((t) => t.id !== id);
    emit();
  }, ms);
}

export function toastError(err) {
  const message = err && err.message ? err.message : String(err);
  toast(message, err && err.code === "feature_not_available" ? "info" : "error", 6000);
}

/* ---- background polling --------------------------------------------------
 * One light loop: health every 4 s until OK, then jobs + review count on a
 * slower cadence (faster while jobs are actually running).
 */

let pollTimer = null;

async function pollOnce() {
  try {
    await api.get("/api/health");
    if (state.health !== "ok") {
      state.health = "ok";
      emit();
      refreshCapabilities();
    }
  } catch {
    if (state.health !== "down" && state.health !== "starting") {
      state.health = "down";
      emit();
    } else if (state.health === "starting") {
      emit();
    }
    return; // no point asking for jobs while unreachable
  }

  try {
    const [jobs, review] = await Promise.all([
      api.get("/api/jobs", { active: true }),
      api.get("/api/review", { status: "open", page_size: 1 }),
    ]);
    const changed =
      JSON.stringify(jobs) !== JSON.stringify(state.activeJobs) ||
      review.total !== state.openReviewCount;
    state.activeJobs = jobs || [];
    state.openReviewCount = review.total || 0;
    if (changed) emit();
  } catch {
    /* transient — next tick will retry */
  }
}

export function startPolling() {
  if (pollTimer) return;
  const tick = async () => {
    await pollOnce();
    const busy = state.activeJobs.length > 0 || state.health !== "ok";
    pollTimer = setTimeout(tick, busy ? 2500 : 6000);
  };
  tick();
}

export async function refreshCapabilities() {
  try {
    state.capabilities = await api.get("/api/capabilities");
    emit();
  } catch {
    /* shown as unknown in Settings */
  }
}

export async function cancelJob(jobId) {
  try {
    await api.post(`/api/jobs/${jobId}/cancel`);
    toast("Cancel requested");
    pollOnce();
  } catch (err) {
    toastError(err);
  }
}
