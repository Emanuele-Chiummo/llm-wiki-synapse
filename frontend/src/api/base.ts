/**
 * base.ts — runtime API base URL resolution (ADR-0047 §2.1) + auth token
 * management (ADR-0052 §4 — THE single injection point for the Bearer token).
 *
 * Priority order (call-time, not module-load-time):
 *   1. localStorage["synapse.serverUrl"]  — desktop runtime (Tauri first-launch)
 *   2. import.meta.env["VITE_API_BASE"]   — build-time inline (web/PWA split-origin)
 *   3. ""                                  — relative / same-origin (web + Vite dev proxy)
 *
 * Why call-time: the user sets the URL AFTER the bundle loads (at the Connect gate).
 * A module-level const captured at import would be stale. Call-time resolution keeps
 * a single source of truth (localStorage) and lets "change server" take effect without
 * a full page reload (ADR-0047 §2.1).
 *
 * Multi-server list (ADR-0048 §T4a):
 *   localStorage["synapse.servers"] — JSON array, deduped (case-insensitive), most-recent
 *   first, max 5. Written only by addKnownServer(), called only from setServerUrl() after
 *   a validated successful connect. Never holds unvalidated or hostile URLs.
 *
 * Auth token (ADR-0052 §4.1):
 *   localStorage["synapse.authToken"] — shared Bearer token for the current server.
 *   Never stored in Zustand (avoids accidental serialization). Read at request time.
 *   ONE injection point: apiFetch() merges authHeaders() into every outgoing request.
 *   No component may construct the Authorization header directly (ADR-0052 Do-NOT §10).
 *
 * No secrets or API keys in this file (CLAUDE.md §12).
 * ADR-0047 §6 Do-NOT: never introduce a module-level const API_BASE in any client.
 * ADR-0052 Do-NOT: never log the token; never construct Authorization outside this module.
 */

const LS_SERVER_URL = "synapse.serverUrl";

/**
 * Last successfully-connected server URL. Unlike LS_SERVER_URL it survives
 * clearServerUrl(), so the Connect gate can prefill the previous address
 * after a "change server" instead of an empty field.
 */
const LS_LAST_SERVER_URL = "synapse.lastServerUrl";

/**
 * Known-servers list (ADR-0048 §T4a).
 * JSON array of normalized URLs, most-recent first, max 5, deduped
 * case-insensitively. Written only from addKnownServer() which is called
 * only from setServerUrl() — so every entry is a previously-validated host.
 */
const LS_SERVERS = "synapse.servers";
const MAX_KNOWN_SERVERS = 5;

/** ALLOWED_SCHEMES: only http and https are acceptable (ADR-0047 §2.7.1). */
const ALLOWED_SCHEMES = ["http:", "https:"];

/**
 * Validates that `url` uses an http or https scheme.
 * Throws a TypeError with a descriptive message on failure.
 * Returns the parsed URL on success.
 */
function validateScheme(url: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new TypeError(`Invalid URL: "${url}"`);
  }
  if (!ALLOWED_SCHEMES.includes(parsed.protocol)) {
    throw new TypeError(
      `Invalid scheme "${parsed.protocol}": only http:// and https:// are allowed (ADR-0047 §2.7.1)`,
    );
  }
  return parsed;
}

/**
 * apiBase — resolve the API base URL at call time (ADR-0047 §2.1).
 *
 * Priority:
 *   1. localStorage["synapse.serverUrl"] (trimmed, trailing slash stripped)
 *   2. import.meta.env["VITE_API_BASE"]  (build-time inline)
 *   3. ""                                 (relative / same-origin)
 *
 * Called once per request — synchronous localStorage read (microseconds).
 * ADR-0047 §6 Do-NOT #1: never cache this in a module-level const.
 */
export function apiBase(): string {
  try {
    const stored = localStorage.getItem(LS_SERVER_URL);
    if (stored && stored.trim().length > 0) {
      return stored.trim().replace(/\/+$/, "");
    }
  } catch {
    // localStorage may be unavailable in SSR/test environments — fall through
  }

  const envBase = import.meta.env["VITE_API_BASE"] as string | undefined;
  if (envBase && envBase.trim().length > 0) {
    return envBase.trim().replace(/\/+$/, "");
  }

  return "";
}

/**
 * getServerUrl — read the persisted desktop server URL.
 * Returns null when not set.
 */
export function getServerUrl(): string | null {
  try {
    const v = localStorage.getItem(LS_SERVER_URL);
    return v && v.trim().length > 0 ? v.trim() : null;
  } catch {
    return null;
  }
}

/**
 * getKnownServers — read the list of previously-connected servers (ADR-0048 §T4a).
 *
 * Returns a JSON array (most-recent first, max 5) of normalized URLs.
 * Every entry was written by addKnownServer() which is only called from the
 * successful-connect path (setServerUrl after 2xx /status), so all entries
 * are validated http(s) URLs.
 */
export function getKnownServers(): string[] {
  try {
    const raw = localStorage.getItem(LS_SERVERS);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return (parsed as unknown[]).filter((v): v is string => typeof v === "string");
  } catch {
    return [];
  }
}

/**
 * addKnownServer — append a validated URL to the known-servers list.
 *
 * MUST be called only from setServerUrl() after the /status probe succeeds.
 * Dedupes case-insensitively (lowercase comparison), keeps most-recent first,
 * caps the list at MAX_KNOWN_SERVERS (5). The URL must already be normalized
 * (trimmed, trailing slash stripped, http(s) scheme) — callers do not re-validate.
 *
 * Internal use only — not exported to consumers (use getKnownServers() to read).
 */
function addKnownServer(url: string): void {
  try {
    const existing = getKnownServers();
    const lower = url.toLowerCase();
    // Remove any case-insensitive duplicate of the new URL
    const deduped = existing.filter((s) => s.toLowerCase() !== lower);
    // Prepend most-recent, cap at max
    const next = [url, ...deduped].slice(0, MAX_KNOWN_SERVERS);
    localStorage.setItem(LS_SERVERS, JSON.stringify(next));
  } catch {
    // ignore — storage unavailable
  }
}

/**
 * setServerUrl — validate and persist the desktop server URL.
 *
 * Validation (ADR-0047 §2.7.1):
 *   - trims whitespace
 *   - strips trailing slash
 *   - rejects non-http(s) schemes (throws TypeError)
 *
 * Note: ConnectScreen MUST NOT call this until the /status probe succeeds (ADR-0047 §2.7.2).
 * This function stores the value unconditionally once validation passes; the probe gate is
 * the caller's responsibility.
 *
 * ADR-0048 §T4a: also registers the validated URL in the known-servers list so
 * the Header dropdown can list previously-connected servers.
 */
export function setServerUrl(url: string): void {
  const trimmed = url.trim().replace(/\/+$/, "");
  // Throws TypeError on invalid URL or non-http(s) scheme (ADR-0047 §2.7.1)
  validateScheme(trimmed);
  try {
    localStorage.setItem(LS_SERVER_URL, trimmed);
    localStorage.setItem(LS_LAST_SERVER_URL, trimmed);
  } catch {
    // ignore — storage unavailable
  }
  // Register in the known-servers list ONLY after validation passes (ADR-0048 §T4a).
  // addKnownServer is internal-only; callers cannot bypass the validation gate here.
  addKnownServer(trimmed);
}

/**
 * getLastServerUrl — read the last successfully-connected server URL.
 * Survives clearServerUrl(); used by ConnectScreen to prefill the input
 * after a "change server". Returns null when the app never connected.
 */
export function getLastServerUrl(): string | null {
  try {
    const v = localStorage.getItem(LS_LAST_SERVER_URL);
    return v && v.trim().length > 0 ? v.trim() : null;
  } catch {
    return null;
  }
}

/**
 * clearServerUrl — remove the persisted desktop server URL.
 * After calling this, getServerUrl() returns null and apiBase() falls back to
 * VITE_API_BASE / "".
 * Called by the "change server" action in Header (Tauri only) to return to the gate.
 */
export function clearServerUrl(): void {
  try {
    localStorage.removeItem(LS_SERVER_URL);
  } catch {
    // ignore
  }
}

// ─── Auth token (ADR-0052 §4.1 — THE single source, never in Zustand) ───────

/**
 * localStorage key for the shared Bearer token (ADR-0052 §4.1).
 * Namespaced like the other synapse.* keys. Never holds multi-server-keyed
 * data — the current server URL is resolved by apiBase() independently.
 */
const LS_AUTH_TOKEN = "synapse.authToken";

/**
 * getAuthToken — read the stored Bearer token for the current server.
 * Returns null when no token is stored (auth disabled or not yet entered).
 * Read at request time — never cached (ADR-0052 §4.1).
 */
export function getAuthToken(): string | null {
  try {
    const v = localStorage.getItem(LS_AUTH_TOKEN);
    return v && v.trim().length > 0 ? v.trim() : null;
  } catch {
    return null;
  }
}

/**
 * setAuthToken — store the Bearer token for the current server.
 * Called by ConnectScreen (on successful connect with a token) and by
 * Settings › Security "Update" action. Never called from components
 * that construct the Authorization header themselves (ADR-0052 Do-NOT §10).
 */
/**
 * bearerHeadersFor — build the Authorization header for an EXPLICIT token.
 *
 * Exists solely for the ConnectScreen bootstrap probe (ADR-0052 §4.4): the
 * server URL is not yet persisted there, so apiFetch cannot be used, but the
 * header construction must still live in this module (Do-NOT §10 — the final
 * QA gate greps that no component builds "Authorization" itself).
 */
export function bearerHeadersFor(token: string): Record<string, string> {
  const trimmed = token.trim();
  return trimmed.length > 0 ? { Authorization: `Bearer ${trimmed}` } : {};
}

export function setAuthToken(token: string): void {
  try {
    const trimmed = token.trim();
    if (trimmed.length > 0) {
      localStorage.setItem(LS_AUTH_TOKEN, trimmed);
    } else {
      localStorage.removeItem(LS_AUTH_TOKEN);
    }
  } catch {
    // ignore — storage unavailable
  }
}

/**
 * clearAuthToken — remove the stored token (e.g. on 401 from server,
 * indicating the server token was rotated and the stored value is stale).
 */
export function clearAuthToken(): void {
  try {
    localStorage.removeItem(LS_AUTH_TOKEN);
  } catch {
    // ignore
  }
}

/**
 * authHeaders — return the Authorization header for the current server.
 * Returns { Authorization: "Bearer <token>" } when a token is stored,
 * {} otherwise.  The ONLY place in the codebase that constructs this header
 * (ADR-0052 §4.2 / Do-NOT §10). Consumed exclusively by apiFetch().
 */
export function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

// ─── Cloudflare Access service token (edge auth — parallel to Bearer) ─────────
//
// A SEPARATE layer from the app Bearer token above: these headers are consumed
// by Cloudflare Access at the edge (before the request ever reaches Synapse),
// not by the Synapse backend. Needed only for NON-BROWSER clients (Tauri
// desktop, iOS, clipper) whose cross-origin requests carry no CF_Authorization
// cookie. In the browser/PWA build the interactive-login cookie handles the
// gate and these stay unset. Created in Cloudflare Zero Trust → Access →
// Service Auth; the matching Access policy uses the "Service Auth" action.
//
// Stored like the Bearer token: localStorage, read at request time, never in
// Zustand, never logged. Both values are required together — one without the
// other is meaningless, so getCfAccessCreds() returns null unless both exist.

const LS_CF_CLIENT_ID = "synapse.cfAccessClientId";
const LS_CF_CLIENT_SECRET = "synapse.cfAccessClientSecret";

export interface CfAccessCreds {
  clientId: string;
  clientSecret: string;
}

/**
 * getCfAccessCreds — read the stored Cloudflare Access service-token pair.
 * Returns null unless BOTH the Client ID and Client Secret are present
 * (a lone value would produce a 403 at the edge, so treat it as "unset").
 * Read at request time — never cached.
 */
export function getCfAccessCreds(): CfAccessCreds | null {
  try {
    const id = localStorage.getItem(LS_CF_CLIENT_ID);
    const secret = localStorage.getItem(LS_CF_CLIENT_SECRET);
    if (id && id.trim().length > 0 && secret && secret.trim().length > 0) {
      return { clientId: id.trim(), clientSecret: secret.trim() };
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * setCfAccessCreds — store the service-token pair. Passing an empty value for
 * either field clears BOTH (there is no valid half-configured state).
 * Called by Settings › Security. Never logs the secret.
 */
export function setCfAccessCreds(clientId: string, clientSecret: string): void {
  try {
    const id = clientId.trim();
    const secret = clientSecret.trim();
    if (id.length > 0 && secret.length > 0) {
      localStorage.setItem(LS_CF_CLIENT_ID, id);
      localStorage.setItem(LS_CF_CLIENT_SECRET, secret);
    } else {
      localStorage.removeItem(LS_CF_CLIENT_ID);
      localStorage.removeItem(LS_CF_CLIENT_SECRET);
    }
  } catch {
    // ignore — storage unavailable
  }
}

/**
 * clearCfAccessCreds — remove the stored service-token pair.
 */
export function clearCfAccessCreds(): void {
  try {
    localStorage.removeItem(LS_CF_CLIENT_ID);
    localStorage.removeItem(LS_CF_CLIENT_SECRET);
  } catch {
    // ignore
  }
}

/**
 * cfAccessHeaders — return the Cloudflare Access service-token headers for the
 * current client. Returns { "CF-Access-Client-Id", "CF-Access-Client-Secret" }
 * when both are stored, {} otherwise. The ONLY place these headers are built.
 * Consumed by apiFetch() and by the ConnectScreen bootstrap probe.
 */
export function cfAccessHeaders(): Record<string, string> {
  const creds = getCfAccessCreds();
  if (!creds) return {};
  return {
    "CF-Access-Client-Id": creds.clientId,
    "CF-Access-Client-Secret": creds.clientSecret,
  };
}

// ─── platformFetch — native HTTP bypass for Tauri desktop (v1.3.10) ─────────
//
// Problem: the Tauri desktop app (origin tauri://localhost) calls a backend that
// may be behind Cloudflare Access.  Adding CF-Access-Client-Id/Secret to a
// cross-origin request triggers a CORS preflight OPTIONS, which CF Access rejects
// with 403 — the app can't reach the backend at all.
//
// Fix: when running inside Tauri, route all HTTP calls through the native HTTP
// plugin (@tauri-apps/plugin-http).  Native requests bypass the webview's
// CORS/preflight machinery entirely, so the service-token headers reach the
// backend correctly.
//
// Implementation notes:
//   - Dynamic import inside isTauri() branch → the web/PWA bundle never loads
//     the Tauri plugin module; vitest (jsdom) never hits this branch.
//   - Web build: isTauri() is always false → global fetch is used unchanged.
//   - Streaming caveat: live per-token streaming (res.body.getReader()) may
//     buffer until response completion on some tauri-plugin-http versions.
//     The message arrives complete; streaming appearance in the chat panel may
//     be absent on desktop.  Known caveat, not a blocker for v1.3.10.

/**
 * platformFetch — transparent fetch wrapper for the Tauri desktop.
 *
 * On Tauri: uses `@tauri-apps/plugin-http`'s fetch (native HTTP stack, no CORS
 * preflight) so CF-Access service-token headers reach CF-protected backends.
 * On web/PWA/test: falls through to the global `fetch` unchanged.
 *
 * The dynamic import is intentionally inside the isTauri() guard so bundlers
 * (Vite) emit it only as a separate on-demand chunk and the web build never
 * resolves or loads the Tauri module.
 *
 * Exported so ConnectScreen can use it for its raw /status probes (which do
 * NOT go through apiFetch because the server URL is not yet persisted there).
 */
export async function platformFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  if (isTauri()) {
    const { fetch: tauriFetch } = await import("@tauri-apps/plugin-http");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return tauriFetch(input as any, init as any) as unknown as Promise<Response>;
  }
  return fetch(input, init);
}

// ─── 401 callback (module-level, registered once by AppShell) ────────────────

/**
 * Module-level callback invoked by apiFetch() on every 401 response.
 * Registered ONCE by AppShell (or the store bootstrap) via register401Handler().
 * Using a module-level callback avoids importing from stores/components here,
 * keeps the I3 boundary clean, and requires no Zustand subscription in base.ts.
 */
let _on401: (() => void) | null = null;

/**
 * register401Handler — register a callback to be fired when apiFetch receives
 * a 401 response. Call this exactly ONCE from AppShell on mount.
 * The callback should: clear the auth token and signal the app to show
 * the token gate (e.g. set authRequired in settingsStore).
 */
export function register401Handler(cb: () => void): void {
  _on401 = cb;
}

/**
 * apiFetch — the SINGLE fetch wrapper that injects auth headers (ADR-0052 §4.2).
 *
 * Drop-in replacement for global fetch:
 *   - merges authHeaders() into init.headers on every request
 *   - on 401 response: clears the stored token and invokes the registered
 *     401 callback (so AppShell can show the token gate without a redirect)
 *   - does NOT throw on 401 — callers receive the Response and can inspect it
 *   - streaming responses (NDJSON) pass through untouched (transport only, I3)
 *
 * MIGRATION: every frontend/src/api/*.ts client MUST use apiFetch instead of
 * the global fetch. No component may call fetch() with an Authorization header.
 */
export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const merged: RequestInit = {
    ...init,
    headers: {
      // Edge auth (Cloudflare Access) first, then app auth (Bearer), then the
      // caller's explicit headers — callers always win on collision.
      ...cfAccessHeaders(),
      ...authHeaders(),
      ...((init.headers as Record<string, string> | undefined) ?? {}),
    },
  };

  const res = await platformFetch(input, merged);

  if (res.status === 401) {
    // Clear the stale stored token (the server rotated it or it was wrong)
    clearAuthToken();
    // Notify the app layer once (the callback was registered by AppShell)
    if (_on401) {
      _on401();
    }
  }

  return res;
}

// ─── isTauri ─────────────────────────────────────────────────────────────────

/**
 * isTauri — detect whether the app is running inside a Tauri v2 webview.
 *
 * Uses the presence of `__TAURI_INTERNALS__` on window — the v2 runtime marker
 * injected by the Tauri webview. This is a passive presence check (NOT a Tauri
 * IPC/command call), so it does not violate ADR-0039 §9.1's rule against
 * window.__TAURI__ API calls (ADR-0047 §2.1).
 */
export function isTauri(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  if ("__TAURI_INTERNALS__" in window) {
    return true;
  }
  // Dev-only escape hatch: `?tauri` simulates the desktop shell in a browser so the
  // Connect gate can be verified visually (preview + D5 Playwright screenshots).
  // import.meta.env.DEV is statically false in production builds — dead-code eliminated.
  if (import.meta.env.DEV) {
    try {
      return new URLSearchParams(window.location.search).has("tauri");
    } catch {
      return false;
    }
  }
  return false;
}
