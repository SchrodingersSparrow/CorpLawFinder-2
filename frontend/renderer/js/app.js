/* Application root: sidebar + the active screen. */

import { h } from "./h.js";
import { initThemeFromCache, startPolling } from "./store.js";
import { useStore } from "./components/hooks.js";
import { Sidebar } from "./components/sidebar.js";
import { Toasts } from "./components/ui.js";
import { DashboardScreen } from "./screens/dashboard.js";
import { SourcesScreen } from "./screens/sources.js";
import { DownloadsScreen } from "./screens/downloads.js";
import { DocumentsScreen } from "./screens/documents.js";
import { SearchScreen } from "./screens/search.js";
import { ReviewScreen } from "./screens/review.js";
import { SettingsScreen } from "./screens/settings.js";

const SCREENS = {
  dashboard: DashboardScreen,
  sources: SourcesScreen,
  downloads: DownloadsScreen,
  documents: DocumentsScreen,
  search: SearchScreen,
  review: ReviewScreen,
  settings: SettingsScreen,
};

function App() {
  const { screen } = useStore();
  const Screen = SCREENS[screen] || DashboardScreen;
  return h("div.app", null,
    h(Sidebar),
    h("main.main", null, h(Screen)),
    h(Toasts),
  );
}

initThemeFromCache();
startPolling();

const root = globalThis.ReactDOM.createRoot(document.getElementById("root"));
root.render(h(App));
