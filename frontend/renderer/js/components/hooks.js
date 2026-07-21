/* React binding for the global store. */

import { useSyncExternalStore } from "../h.js";
import { store } from "../store.js";

export { store };

export function useStore() {
  return useSyncExternalStore(store.subscribe, store.getState);
}
