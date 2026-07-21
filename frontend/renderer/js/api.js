/* HTTP client for the local backend.
 *
 * The backend answers every expected failure with one envelope:
 *   { "error": { "code": "...", "message": "...", "detail": {...} } }
 * This client turns that into an ApiError carrying the friendly message, so
 * screens can simply `catch (e) { toastError(e) }`.
 */

export const BASE_URL =
  (globalThis.lkm && globalThis.lkm.backendUrl) || "http://127.0.0.1:8756";

export class ApiError extends Error {
  constructor(message, code, status, detail) {
    super(message);
    this.name = "ApiError";
    this.code = code || "unknown";
    this.status = status || 0;
    this.detail = detail || null;
  }
}

async function request(method, path, { body, formData, params } = {}) {
  let url = BASE_URL + path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        qs.set(key, String(value));
      }
    }
    const s = qs.toString();
    if (s) url += "?" + s;
  }

  const init = { method, headers: {} };
  if (formData) {
    init.body = formData; // browser sets multipart boundary itself
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    throw new ApiError(
      "The backend is not reachable. It may still be starting up.",
      "backend_unreachable",
      0,
      { cause: String(err) },
    );
  }

  if (response.status === 204) return null;

  let data = null;
  const text = await response.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    const envelope = data && data.error ? data.error : {};
    throw new ApiError(
      envelope.message || `The backend answered with HTTP ${response.status}.`,
      envelope.code,
      response.status,
      envelope.detail,
    );
  }
  return data;
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body, params) => request("POST", path, { body, params }),
  put: (path, body) => request("PUT", path, { body }),
  patch: (path, body) => request("PATCH", path, { body }),
  del: (path, params) => request("DELETE", path, { params }),
  upload: (path, formData) => request("POST", path, { formData }),
  fileUrl: (documentId) => `${BASE_URL}/api/documents/${documentId}/file`,
};
