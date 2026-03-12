/**
 * api.ts — fetch helpers for the VoIP Control Panel
 *
 * Auth model: nginx injects X-Voip-Token (a shared secret) on every request
 * to /voip/api/ via proxy_set_header. The Python backend validates this header.
 * Because proxy_set_header overwrites any client-supplied value, the secret
 * cannot be forged from outside even via VPN/Teleport.
 *
 * The /voip UI entry point is separately protected by UniFi OS auth_request,
 * so unauthenticated users are redirected to login before they can load the SPA.
 */

/**
 * POST a JSON body to the VoIP API.
 * Returns the raw Response so callers can call .json() themselves.
 */
export async function postJson(url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}
