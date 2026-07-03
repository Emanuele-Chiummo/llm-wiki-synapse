/**
 * useDesktopUpdater.ts — startup update check for the Tauri v2 desktop shell (ADR-0049 §U4).
 *
 * Behaviour (binding contract from ADR-0049 §5.3):
 *   - Runs ONCE on mount, only when isTauri() — fire-and-forget, non-blocking.
 *   - Calls check() from @tauri-apps/plugin-updater via DYNAMIC IMPORT inside the guard.
 *   - All errors are caught and silently swallowed — a failed/timed-out check MUST NOT
 *     block or crash the app.
 *   - No polling, no interval, no retry loop (I7: exactly one check per process start).
 *   - If an update is available, exposes { version, notes } state for UpdateBanner.
 *
 * Plugin imports are DYNAMIC and INSIDE the isTauri() guard so the web/PWA bundle never
 * imports @tauri-apps/plugin-updater or @tauri-apps/plugin-process (ADR-0039 §9.1).
 *
 * INVARIANT I7 (loops bounded): this is a single startup check — no loop by construction.
 * INVARIANT I3 (no heavy work per token): update check is a one-shot lifecycle effect,
 *   entirely independent of the chat streaming path.
 */

import { useState, useEffect } from "react";
import { isTauri } from "../api/base";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface UpdateInfo {
  /** The new version string as returned by the updater manifest (e.g. "0.7.0"). */
  version: string;
  /** Release notes from the manifest body (may be undefined). */
  notes: string | undefined;
}

export interface DesktopUpdaterState {
  /** Non-null when an update is available and the user has not dismissed it. */
  update: UpdateInfo | null;
  /** True while downloadAndInstall() is in progress. */
  installing: boolean;
  /** Non-null if downloadAndInstall() threw — surface as inline error in the banner. */
  installError: string | null;
  /** Dismiss the banner for this session (does NOT persist — re-surfaces on next start). */
  dismiss: () => void;
  /** Begin the download-and-install flow, then relaunch(). */
  startInstall: () => Promise<void>;
}

// NOTE (fix post-v0.8.0): these dynamic imports MUST be static string literals so
// Vite bundles the plugin JS into lazy chunks. The earlier runtime-variable trick
// (used before the npm packages were installed) meant the plugins were NEVER
// bundled: the bare-specifier import failed at runtime inside the WebView, the
// error was swallowed, and the update banner never appeared. The isTauri() guard
// keeps the web/PWA build inert — the chunks are only fetched inside Tauri.

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * useDesktopUpdater — check for a desktop update on app start, then periodically.
 *
 * Wire this in AppShell (mounted once, after render).  The hook is a no-op in
 * the web/PWA build — isTauri() is false there.
 *
 * Re-check policy (owner request, v1.2 — "the banner never shows until I restart"):
 *   - once at startup (original ADR-0049 §U4 behaviour)
 *   - every RECHECK_INTERVAL_MS while the app stays open (long-lived apps see new
 *     releases without a relaunch)
 *   - on window focus, throttled to at most one check per FOCUS_THROTTLE_MS
 *   All checks remain fire-and-forget and error-swallowing (I7 / ADR-0049 §6).
 */

/** Periodic re-check while the app is open: every 4 hours. */
const RECHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;
/** Focus-triggered re-check, at most once per 30 minutes. */
const FOCUS_THROTTLE_MS = 30 * 60 * 1000;

export function useDesktopUpdater(): DesktopUpdaterState {
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);

  // ── Update checks: startup + periodic + focus-throttled (ADR-0049 §U4 amended) ──
  useEffect(() => {
    if (!isTauri()) return;

    let cancelled = false;
    let lastCheckAt = 0;

    const runCheck = async () => {
      lastCheckAt = Date.now();
      try {
        const { check } = await import("@tauri-apps/plugin-updater");
        const result = await check();
        if (cancelled) return;
        if (result !== null) {
          setUpdate({ version: result.version, notes: result.body });
        }
      } catch {
        // Swallow all errors: network failures, timeout, missing manifest, etc.
        // A failed check must never block or crash the app (ADR-0049 §6 Do-NOT #3).
      }
    };

    void runCheck();

    const intervalId = setInterval(() => void runCheck(), RECHECK_INTERVAL_MS);

    const onFocus = () => {
      if (Date.now() - lastCheckAt >= FOCUS_THROTTLE_MS) void runCheck();
    };
    window.addEventListener("focus", onFocus);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  // ── Dismiss (session-scoped, no persistence per ADR-0049 §U4) ─────────────
  const dismiss = () => {
    setUpdate(null);
    setInstallError(null);
  };

  // ── Install + relaunch ────────────────────────────────────────────────────
  const startInstall = async (): Promise<void> => {
    if (!update || installing) return;
    setInstalling(true);
    setInstallError(null);
    try {
      const [{ check }, { relaunch }] = await Promise.all([
        import("@tauri-apps/plugin-updater"),
        import("@tauri-apps/plugin-process"),
      ]);
      // Re-fetch the update object to get downloadAndInstall; the initial check()
      // result may have been GC'd. Re-calling check() is cheap (cached by Tauri).
      const freshUpdate = await check();
      if (freshUpdate === null) {
        // Edge case: update disappeared between check and install — just dismiss.
        setUpdate(null);
        setInstalling(false);
        return;
      }
      await freshUpdate.downloadAndInstall();
      await relaunch();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setInstallError(msg);
      setInstalling(false);
    }
  };

  return { update, installing, installError, dismiss, startInstall };
}
