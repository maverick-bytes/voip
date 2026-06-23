/**
 * api.ts - fetch helpers for the VoIP Control Panel
 *
 * Auth model: nginx authenticates every request to /voip/api/ via the same
 * include chain (cors.conf + security.conf + auth.conf) used by every other
 * authenticated API location on this controller (e.g. /proxy/network/).
 * auth.conf issues a CSRF token to the client via the X-Csrf-Token /
 * X-Updated-Csrf-Token response headers; state-changing requests (POST)
 * must echo it back as an X-Csrf-Token request header, or UniFi OS's
 * internal session validator rejects them with 403 before the request ever
 * reaches our backend. apiFetch() below captures the token from every
 * response and attaches it automatically on every request, so individual
 * components never need to think about it.
 *
 * X-Voip-Token (a separate, nginx-injected shared secret) remains as
 * defense-in-depth alongside the UniFi OS session/CSRF check.
 */

let csrfToken: string | null = null;

function captureCsrfToken(res: Response) {
  const updated = res.headers.get("X-Updated-Csrf-Token");
  const initial = res.headers.get("X-Csrf-Token");
  if (updated) csrfToken = updated;
  else if (initial) csrfToken = initial;
}

/**
 * Wrapper around fetch() for all /voip/api/ calls. Captures and replays the
 * UniFi OS CSRF token automatically. Use this instead of calling fetch()
 * directly anywhere in this app.
 */
export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (csrfToken && method !== "GET" && method !== "HEAD") {
    headers.set("X-Csrf-Token", csrfToken);
  }
  const res = await fetch(url, { ...init, headers });
  captureCsrfToken(res);
  return res;
}

/**
 * POST a JSON body to the VoIP API.
 * Returns the raw Response so callers can call .json() themselves.
 */
export async function postJson(url: string, body: unknown): Promise<Response> {
  return apiFetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}
