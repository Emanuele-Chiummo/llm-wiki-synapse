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
}

interface StatusActions {
  /** Called by ActivityBar when it receives a /status response. */
  setBackendVersion: (version: string | undefined) => void;
  /** Called by ActivityBar with /status.review_pending (may be absent → undefined). */
  setReviewPending: (count: number | undefined) => void;
}

export type StatusStore = StatusState & StatusActions;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useStatusStore = create<StatusStore>((set) => ({
  backendVersion: undefined,
  reviewPending: undefined,
  setBackendVersion: (backendVersion) => set({ backendVersion }),
  setReviewPending: (reviewPending) => set({ reviewPending }),
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
