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

  interviews: () => request("GET", "/interviews"),
  assessment: (id) => request("GET", `/interviews/${encodeURIComponent(id)}/assessment`),
  removeInterview: (id) => request("DELETE", `/interviews/${encodeURIComponent(id)}`),
  decide:     (id, decision) =>
    request("POST", `/interviews/${encodeURIComponent(id)}/decision`, { decision }),

  joinCredentials: (id) => request("GET", `/session/${encodeURIComponent(id)}/join`),
  startPanel:      (id, channel) =>
    request("POST", `/session/${encodeURIComponent(id)}/start`, { channel }),
  stopPanel:       (id) => request("POST", `/session/${encodeURIComponent(id)}/stop`),
  state:           (id) => request("GET", `/session/${encodeURIComponent(id)}/state`),
  finish:          (id) => request("POST", `/session/${encodeURIComponent(id)}/finish`),
};
