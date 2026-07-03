/**
 * base.ts — runtime API base URL resolution (ADR-0047 §2.1).
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
 * No secrets or API keys in this file (CLAUDE.md §12).
 * ADR-0047 §6 Do-NOT: never introduce a module-level const API_BASE in any client.
 */

const LS_SERVER_URL = "synapse.serverUrl";

/**
 * Last successfully-connected server URL. Unlike LS_SERVER_URL it survives
 * clearServerUrl(), so the Connect gate can prefill the previous address
 * after a "change server" instead of an empty field.
 */
const LS_LAST_SERVER_URL = "synapse.lastServerUrl";

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
