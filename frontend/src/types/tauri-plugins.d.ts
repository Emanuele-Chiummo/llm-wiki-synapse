/**
 * tauri-plugins.d.ts — minimal ambient declarations for Tauri v2 first-party plugins.
 *
 * These modules are dynamically imported inside isTauri() guards (ADR-0049 §U4 / ADR-0039
 * §9.1 / ADR-0048 §2.4c). The real npm packages (@tauri-apps/plugin-updater,
 * @tauri-apps/plugin-process) are installed by the devops-engineer alongside the Rust
 * counterparts; while they may not be resolvable yet, the dynamic `await import(...)`
 * pattern requires at least a module declaration so tsc does not error on the import path.
 *
 * Only the subset of the API used by useDesktopUpdater is declared here (minimal surface).
 * DO NOT expand beyond what the hook actually calls.
 */

declare module "@tauri-apps/plugin-updater" {
  /** Represents an available update returned by check(). */
  export interface Update {
    /** The new version string (e.g. "0.7.0"). */
    version: string;
    /** Release notes from the manifest (may be undefined if absent). */
    body?: string;
    /**
     * Download the update and install it.
     * @param onChunk - optional progress callback called with each downloaded byte count.
     */
    downloadAndInstall(onChunk?: (byteCount: number) => void): Promise<void>;
  }

  /**
   * Check the configured updater endpoint for a newer version.
   * Returns the Update object if one is available, or null if already up to date.
   */
  export function check(): Promise<Update | null>;
}

declare module "@tauri-apps/plugin-process" {
  /**
   * Restart the Tauri application process.
   * Called after downloadAndInstall() completes successfully.
   */
  export function relaunch(): Promise<void>;
}
