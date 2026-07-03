/**
 * statusStore.ts — holds the backend version surfaced by GET /status [R12-3][ADR-0054 §6].
 *
 * The ActivityBar already polls /status every 30s (STATUS_POLL_MS). This store receives
 * the version field from that EXISTING poll via setBackendVersion() — no new poller
 * is added (I3: no new polling loop, no per-token work, single source of truth).
 *
 * AppShell reads backendVersion via selectBackendVersion and renders the version-mismatch
 * banner when backendVersion is present, non-"dev", and differs from __APP_VERSION__.
 *
 * Scalar state — Object.is comparison, no useShallow needed.
 */

import { create } from "zustand";

// ─── State ────────────────────────────────────────────────────────────────────

interface StatusState {
  /**
   * Backend version string from /status.version (ADR-0054 §6).
   * Undefined until the first successful /status poll.
   * "dev" means a local dev build — no banner shown (R12-3 AC-R12-3-5).
   * Absent (/status.version not present on older backends) → stays undefined.
   */
  backendVersion: string | undefined;
}

interface StatusActions {
  /** Called by ActivityBar when it receives a /status response. */
  setBackendVersion: (version: string | undefined) => void;
}

export type StatusStore = StatusState & StatusActions;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useStatusStore = create<StatusStore>((set) => ({
  backendVersion: undefined,
  setBackendVersion: (backendVersion) => set({ backendVersion }),
}));

// ─── Selectors ────────────────────────────────────────────────────────────────

export function selectBackendVersion(s: StatusStore): string | undefined {
  return s.backendVersion;
}

export function selectSetBackendVersion(s: StatusStore): StatusActions["setBackendVersion"] {
  return s.setBackendVersion;
}
