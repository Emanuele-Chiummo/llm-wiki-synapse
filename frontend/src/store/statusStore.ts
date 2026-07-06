/**
 * statusStore.ts — holds scalar fields surfaced by GET /status [R12-3][ADR-0054 §6].
 *
 * The ActivityBar already polls /status every 30s (STATUS_POLL_MS). This store receives
 * fields from that EXISTING poll — no new poller is added (I3: no new polling loop,
 * no per-token work, single source of truth).
 *
 * Consumers:
 *  - AppShell reads backendVersion via selectBackendVersion and renders the
 *    version-mismatch banner when the backend is semver-behind __APP_VERSION__.
 *  - NavRail reads reviewPending via selectReviewPending for the Revisione badge
 *    (owner request, v1.2.x) — hidden at 0/undefined.
 *  - HomeDashboard and GraphViewer subscribe to dataVersion via selectStatusDataVersion
 *    to detect backend bumps and re-fetch their data (WS-A [F16/F4/F18]).
 *    INVARIANT I3: only re-fetches when the version value actually changes.
 *    INVARIANT I2: no client-side layout; graph coords always fetched from server.
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
  /**
   * Pending review-queue items from /status.review_pending.
   * Undefined until the first poll or on older backends (field absent) —
   * the NavRail badge renders nothing in that case.
   */
  reviewPending: number | undefined;
  /**
   * Whether the active provider supports image inputs (B2 — vision gate).
   * Undefined until the first /status poll. Absent from older backends → false.
   * MessageInput uses this to enable/disable the attach-image button.
   */
  supportsVision: boolean;
  /**
   * data_version from GET /status — the backend's monotonically-increasing
   * counter that bumps whenever new pages/links/graph coords are written.
   * Written by ActivityBar's existing /status poll (STATUS_POLL_MS = 30s).
   * Null until the first successful /status poll.
   *
   * WS-A [F16/F4/F18]: HomeDashboard and GraphViewer subscribe to this value
   * and re-fetch their data when it changes. No new poller introduced (I3).
   * No client-side layout triggered (I2).
   */
  dataVersion: number | null;
}

interface StatusActions {
  /** Called by ActivityBar when it receives a /status response. */
  setBackendVersion: (version: string | undefined) => void;
  /** Called by ActivityBar with /status.review_pending (may be absent → undefined). */
  setReviewPending: (count: number | undefined) => void;
  /** Called by ActivityBar with /status.supports_vision (may be absent → false). */
  setSupportsVision: (v: boolean) => void;
  /**
   * Called by ActivityBar with /status.data_version (WS-A).
   * Absent on very old backends → null (unchanged; no spurious re-fetches).
   */
  setDataVersion: (version: number | null) => void;
}

export type StatusStore = StatusState & StatusActions;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useStatusStore = create<StatusStore>((set) => ({
  backendVersion: undefined,
  reviewPending: undefined,
  supportsVision: false,
  dataVersion: null,
  setBackendVersion: (backendVersion) => set({ backendVersion }),
  setReviewPending: (reviewPending) => set({ reviewPending }),
  setSupportsVision: (supportsVision) => set({ supportsVision }),
  setDataVersion: (dataVersion) => set({ dataVersion }),
}));

// ─── Selectors ────────────────────────────────────────────────────────────────

export function selectBackendVersion(s: StatusStore): string | undefined {
  return s.backendVersion;
}

export function selectSetBackendVersion(s: StatusStore): StatusActions["setBackendVersion"] {
  return s.setBackendVersion;
}

export function selectReviewPending(s: StatusStore): number | undefined {
  return s.reviewPending;
}

export function selectSupportsVision(s: StatusStore): boolean {
  return s.supportsVision;
}

export function selectSetSupportsVision(s: StatusStore): StatusActions["setSupportsVision"] {
  return s.setSupportsVision;
}

/**
 * WS-A [F16/F4/F18]: Select the data_version from the last /status poll.
 * Null until ActivityBar's first successful poll. Used by HomeDashboard and
 * GraphViewer to detect version bumps without adding a new polling loop (I3).
 */
export function selectStatusDataVersion(s: StatusStore): number | null {
  return s.dataVersion;
}

export function selectSetDataVersion(s: StatusStore): StatusActions["setDataVersion"] {
  return s.setDataVersion;
}
