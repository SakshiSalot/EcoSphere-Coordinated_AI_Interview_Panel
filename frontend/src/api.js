/* Every call to the gateway goes through here.
 *
 * One place that knows about the token means one place to change when it
 * expires, and no component ever handles a credential.
 *
 * sessionStorage, not localStorage: the token dies when the tab closes, which
 * is the right lifetime for an interview and means a shared or public machine
 * does not keep somebody signed in after they walk away.
 */

const KEY = "echosphere.token";

export const token = {
  get: () => {
    try { return sessionStorage.getItem(KEY); } catch { return null; }
  },
  set: (value) => {
    try { value ? sessionStorage.setItem(KEY, value) : sessionStorage.removeItem(KEY); }
    catch { /* private mode - the session simply will not persist a reload */ }
  },
  clear: () => token.set(null),
};

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const held = token.get();
  const res = await fetch(path, {
    method,
    headers: {
      ...(held ? { Authorization: `Bearer ${held}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.status === 204) return null;

  let payload = null;
  try { payload = await res.json(); } catch { /* empty or non-JSON body */ }

  if (!res.ok) {
    // The gateway deliberately says only "unauthorized" or "forbidden" - it
    // will not tell a caller which half of a guess was right. Turn that into
    // something a person can act on without inventing detail the server
    // withheld on purpose.
    const detail = payload?.detail;
    const message =
      typeof detail === "string" && detail !== "unauthorized" && detail !== "forbidden"
        ? detail
        : res.status === 401
          ? "Your session has ended. Please sign in again."
          : res.status === 403
            ? "You do not have access to this interview."
            : `Something went wrong (${res.status}).`;
    throw new ApiError(message, res.status);
  }
  return payload;
}

export const api = {
  get:  (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  del:  (path) => request("DELETE", path),

  login:    (username, password) => request("POST", "/auth/login", { username, password }),
  register: (fields) => request("POST", "/auth/register", fields),
  me:       () => request("GET", "/auth/me"),

  // Openings, and the interviews curated under them.
  jobs:         () => request("GET", "/jobs"),
  createJob:    (fields) => request("POST", "/jobs", fields),
  job:          (id) => request("GET", `/jobs/${encodeURIComponent(id)}`),
  addCandidate: (id, fields) =>
    request("POST", `/jobs/${encodeURIComponent(id)}/candidates`, fields),
  claim:        (code) => request("POST", "/interviews/claim", { code }),

  // The coding round. Taken separately from the conversation, so every call
  // is against the database rather than a live session.
  coding:       (id) => request("GET", `/session/${encodeURIComponent(id)}/coding`),
  codingSave:   (id, source) =>
    request("POST", `/session/${encodeURIComponent(id)}/coding/save`, { source }),
  codingRun:    (id, source, stdin) =>
    request("POST", `/session/${encodeURIComponent(id)}/coding/run`, { source, stdin }),
  codingSubmit: (id, source) =>
    request("POST", `/session/${encodeURIComponent(id)}/coding/submit`, { source }),

  /* Multipart, so it bypasses `request` — setting Content-Type by hand on a
   * FormData body strips the boundary the server needs to parse it. */
  extract: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const held = token.get();
    const res = await fetch("/extract", {
      method: "POST",
      headers: held ? { Authorization: `Bearer ${held}` } : {},
      body: form,
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new ApiError(payload.detail || `Could not read that file (${res.status}).`,
                         res.status);
    }
    return payload;
  },

  interviews: () => request("GET", "/interviews"),
  assessment: (id) => request("GET", `/interviews/${encodeURIComponent(id)}/assessment`),
  removeInterview: (id) => request("DELETE", `/interviews/${encodeURIComponent(id)}`),
  decide:     (id, decision) =>
    request("POST", `/interviews/${encodeURIComponent(id)}/decision`, { decision }),

  // The candidate's own profile links. Verification is a separate call from
  // saving, because saving is only a claim.
  profile:       () => request("GET", "/profile"),
  saveLinks:     (fields) => request("POST", "/profile/links", fields),
  verifyGithub:  (github_username) =>
    request("POST", "/profile/github", { github_username }),

  integrity: (id) => request("GET", `/session/${encodeURIComponent(id)}/integrity`),

  /* The assessment as a PDF. Fetched with the token and handed to the browser
   * as a blob rather than linked directly: the endpoint needs an Authorization
   * header, and a plain <a href> cannot send one. */
  reportPdf: async (id) => {
    const held = token.get();
    const res = await fetch(`/interviews/${encodeURIComponent(id)}/report.pdf`, {
      headers: held ? { Authorization: `Bearer ${held}` } : {},
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}));
      throw new ApiError(payload.detail || `Could not build the report (${res.status}).`,
                         res.status);
    }
    const name = (res.headers.get("Content-Disposition") || "")
      .match(/filename="([^"]+)"/)?.[1] || "assessment.pdf";
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    // Revoked on a delay: revoking immediately races the download in Safari.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  },

  joinCredentials: (id) => request("GET", `/session/${encodeURIComponent(id)}/join`),
  startPanel:      (id, channel) =>
    request("POST", `/session/${encodeURIComponent(id)}/start`, { channel }),
  stopPanel:       (id) => request("POST", `/session/${encodeURIComponent(id)}/stop`),
  state:           (id) => request("GET", `/session/${encodeURIComponent(id)}/state`),
  finish:          (id) => request("POST", `/session/${encodeURIComponent(id)}/finish`),
};
