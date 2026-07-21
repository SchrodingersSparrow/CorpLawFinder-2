/* Bridge between the sandboxed page and the Electron main process.
 *
 * Only these few, narrow functions are exposed — the page never gets Node or
 * file-system access. File paths are resolved in the main process from the
 * document id, so the page cannot ask for arbitrary paths.
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("lkm", {
  isElectron: true,
  platform: process.platform,
  backendUrl: "http://127.0.0.1:8756",
  /** Open document {id}'s file with the user's default app (PDF viewer, Word…). */
  openDocument: (documentId) => ipcRenderer.invoke("lkm:open-document", documentId),
  /** Highlight document {id}'s file in Windows Explorer / Finder. */
  revealDocument: (documentId) => ipcRenderer.invoke("lkm:reveal-document", documentId),
  /** Open a link in the user's browser (never inside the app window). */
  openExternal: (url) => ipcRenderer.invoke("lkm:open-external", url),
});
