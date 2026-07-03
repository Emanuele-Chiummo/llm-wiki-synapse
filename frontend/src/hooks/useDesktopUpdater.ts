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

// ─── Runtime-computed module identifiers ─────────────────────────────────────
//
// These are intentionally NOT string literals so Vite's import-analysis plugin
// cannot statically resolve them. The packages are installed by the devops-engineer
// alongside the Rust plugins (tauri-plugin-updater + tauri-plugin-process).
// /* @vite-ignore */ suppresses the "cannot analyze" warning.
// tsc is satisfied by the ambient declarations in src/types/tauri-plugins.d.ts.
const UPDATER_MODULE = "@tauri-apps/plugin-updater" as string;
const PROCESS_MODULE = "@tauri-apps/plugin-process" as string;

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * useDesktopUpdater — check for a desktop update once on app start.
 *
 * Wire this in AppShell (mounted once, after render).  The hook is a no-op in
 * the web/PWA build — isTauri() is false there.
 */
export function useDesktopUpdater(): DesktopUpdaterState {
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);

  // ── Startup check (fire-and-forget, single iteration, ADR-0049 §U4 / I7) ──
  useEffect(() => {
    if (!isTauri()) return;

    let cancelled = false;

    void (async () => {
      try {
        // Dynamic import via runtime variable — Vite cannot statically resolve a
        // variable import, so import-analysis is bypassed entirely (see UPDATER_MODULE
        // declaration above). /* @vite-ignore */ suppresses the "cannot analyze" warn.
        const { check } = await import(/* @vite-ignore */ UPDATER_MODULE) as typeof import("@tauri-apps/plugin-updater");
        const result = await check();
        if (cancelled) return;
        if (result !== null) {
          setUpdate({ version: result.version, notes: result.body });
        }
      } catch {
        // Swallow all errors: network failures, timeout, missing manifest, etc.
        // A failed check must never block or crash the app (ADR-0049 §6 Do-NOT #3).
      }
    })();

    return () => {
      cancelled = true;
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
      // Dynamic import via runtime variables — bypasses Vite's static resolution.
      // See UPDATER_MODULE / PROCESS_MODULE declarations above.
      const [{ check }, { relaunch }] = await Promise.all([
        import(/* @vite-ignore */ UPDATER_MODULE) as Promise<typeof import("@tauri-apps/plugin-updater")>,
        import(/* @vite-ignore */ PROCESS_MODULE) as Promise<typeof import("@tauri-apps/plugin-process")>,
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
