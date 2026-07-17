/**
 * statusStore.ts — owns the app-wide GET /status poll [R12-3][ADR-0054 §6][FE-ARCH-3].
 *
 * This store OWNS the /status poll (startPolling/stopPolling) — it no longer lives
 * inside ActivityBar. Ownership moved here so the real-time /status stream (backend
 * version, review-pending badge, data_version freshness, connectivity) survives even
 * if ActivityBar unmounts; AppShell starts the single shared poll once on mount.
 * Built on the shared `createPollChain` primitive (FE-ARCH-2), refcounted so any
 * number of callers can safely start/stop without spawning duplicate chains.
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
import { fetchStatus } from "../api/pagesClient";
import type { StatusResponse } from "../api/types";
import { createPollChain } from "./pollChain";
import { useActivityStore } from "./activityStore";
import { useAppStore } from "./appStore";
import { useEventsStore } from "./eventsStore";

// ─── Adaptive /status cadence (RT-1) ───────────────────────────────────────────

const STATUS_POLL_MS = 30_000;
// RT-1: while an ingest / queue is active, poll fast so data_version — and the dashboard KPIs +
// graph that re-fetch on data_version change — stay live within a few seconds instead of lagging
// up to STATUS_POLL_MS. Self-throttles back to 30s when idle. I3: no new poller (same shared
// pollChain); I7: bounded, aborted on cleanup.
const STATUS_POLL_ACTIVE_MS = 3_000;

/**
 * RT-1: adaptive /status cadence. Fast (3s) while the queue is doing work — every write bumps
 * data_version and the dashboard KPIs + graph re-fetch on that change — else the idle 30s.
 * `paused` is intentionally NOT "active": a paused queue produces no writes.
 *
 * 1.9.3 W1 (FE-RT-2): when `sseHealthy` is true, GET /events already pushes data_version
 * bumps in real time, so this REST poll no longer needs the fast cadence to stay fresh —
 * it always falls back to the idle interval, purely as the permanent fallback poller
 * (NEVER disabled, per the non-regression mandate; just slower while SSE is healthy).
 * Defaults to `false` so every existing call site (and the pre-1.9.3 test suite) is
 * unaffected until the caller explicitly threads the SSE health flag through.
 */
export function statusPollDelayMs(
  snap: { processing?: number; pending?: number } | null,
  sseHealthy = false,
): number {
  if (sseHealthy) return STATUS_POLL_MS;
  const active = (snap?.processing ?? 0) > 0 || (snap?.pending ?? 0) > 0;
  return active ? STATUS_POLL_ACTIVE_MS : STATUS_POLL_MS;
}

// ─── State ────────────────────────────────────────────────────────────────────

interface StatusState {
  /** Shared shell-level connectivity derived from the existing /status poll. */
  connectionState: "checking" | "online" | "offline";
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
  /**
   * uptime_seconds from GET /status — surfaced in ActivityBar's collapsed bar.
   * Null until the first successful poll.
   */
  uptimeSeconds: number | null;
}

interface StatusActions {
  /** Called by the poll; consumers must not add another poller (FE-ARCH-3). */
  setConnectionState: (state: StatusState["connectionState"]) => void;
  setBackendVersion: (version: string | undefined) => void;
  setReviewPending: (count: number | undefined) => void;
  setSupportsVision: (v: boolean) => void;
  /**
   * WS-A: set from /status.data_version.
   * Absent on very old backends → null (unchanged; no spurious re-fetches).
   */
  setDataVersion: (version: number | null) => void;
  setUptimeSeconds: (seconds: number | null) => void;
  /**
   * Start the shared GET /status poll (FE-ARCH-2/FE-ARCH-3). Refcounted —
   * safe to call from multiple mount sites; the chain runs until the LAST
   * caller's cleanup fn (the returned function) is invoked. AppShell is the
   * canonical single caller; ActivityBar is a pure subscriber of the store.
   */
  startPolling: () => () => void;
  /**
   * FE-UIUX-3: clear the per-vault fields (dataVersion, reviewPending) when the
   * active vault changes, so stale-version-looking-fresh false negatives don't
   * suppress a re-fetch in HomeDashboard/GraphViewer's data_version-watch effects.
   * connectionState/backendVersion are backend-wide, not vault-specific, and are kept.
   */
  resetForVault: () => void;
}

export type StatusStore = StatusState & StatusActions;

// ─── Shared singleton poll chain (FE-ARCH-2) ────────────────────────────────────

let sharedStatusPollChain: ReturnType<typeof createPollChain<StatusResponse>> | null = null;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useStatusStore = create<StatusStore>((set) => ({
  connectionState: "checking",
  backendVersion: undefined,
  reviewPending: undefined,
  supportsVision: false,
  dataVersion: null,
  uptimeSeconds: null,
  setConnectionState: (connectionState) => set({ connectionState }),
  setBackendVersion: (backendVersion) => set({ backendVersion }),
  setReviewPending: (reviewPending) => set({ reviewPending }),
  setSupportsVision: (supportsVision) => set({ supportsVision }),
  setDataVersion: (dataVersion) => set({ dataVersion }),
  setUptimeSeconds: (uptimeSeconds) => set({ uptimeSeconds }),

  startPolling: () => {
    if (sharedStatusPollChain === null) {
      sharedStatusPollChain = createPollChain<StatusResponse>({
        fetch: (signal) => fetchStatus(signal),
        onResult: (res) => {
          set({
            backendVersion: res.version,
            reviewPending: res.review_pending,
            supportsVision: res.supports_vision ?? false,
            dataVersion: res.data_version ?? null,
            uptimeSeconds: res.uptime_seconds,
            connectionState: "online",
          });
          // Sync the active vault from the backend so every data list (review, pages,
          // graph, lint) queries the vault the backend is actually serving — not a
          // stale "default". Without this a non-default VAULT_ID makes the lists
          // query the wrong vault (13-badge / 2-list mismatch).
          if (res.vault_id && res.vault_id !== useAppStore.getState().vaultId) {
            useAppStore.getState().setVaultId(res.vault_id);
          }
        },
        intervalFor: () =>
          statusPollDelayMs(useActivityStore.getState().snapshot, useEventsStore.getState().healthy),
        onError: () => set({ connectionState: "offline" }),
        errorIntervalFor: () =>
          statusPollDelayMs(useActivityStore.getState().snapshot, useEventsStore.getState().healthy),
      });
    }
    return sharedStatusPollChain.subscribe();
  },

  // FE-UIUX-3
  resetForVault: () => set({ dataVersion: null, reviewPending: undefined }),
}));

// ─── Selectors ────────────────────────────────────────────────────────────────

export function selectBackendVersion(s: StatusStore): string | undefined {
  return s.backendVersion;
}

export function selectBackendConnectionState(s: StatusStore): StatusState["connectionState"] {
  return s.connectionState;
}

export function selectSetBackendConnectionState(
  s: StatusStore,
): StatusActions["setConnectionState"] {
  return s.setConnectionState;
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

/** Uptime (seconds) from the last /status poll — rendered in ActivityBar's collapsed bar. */
export function selectUptimeSeconds(s: StatusStore): number | null {
  return s.uptimeSeconds;
}

export function selectStartStatusPolling(s: StatusStore): StatusActions["startPolling"] {
  return s.startPolling;
}

export function selectStatusResetForVault(s: StatusStore): StatusActions["resetForVault"] {
  return s.resetForVault;
}

/**
 * refreshDataVersion — fire-and-forget helper (RT-2).
 *
 * Calls GET /status and pushes the latest data_version into the store.
 * Intended to be called after mutating REST operations (PUT /pages, DELETE /pages,
 * lint-fix apply, save-to-wiki) so the dashboard and graph pick up the server
 * version bump without waiting the full ActivityBar poll cadence (~30s).
 *
 * Never awaited; never throws (errors are silently swallowed).
 * Does NOT introduce a new polling interval (I7).
 */
export function refreshDataVersion(): void {
  void fetchStatus()
    .then((res) => {
      useStatusStore.getState().setDataVersion(res.data_version);
    })
    .catch(() => {
      // fire-and-forget: transient network errors are acceptable
    });
}

/**
 * refreshStatusNow — fire-and-forget full /status refresh (FE-UIUX-3).
 *
 * Called right after a vault switch so the reviewPending badge, dataVersion,
 * and supportsVision gate reflect the NEW vault immediately instead of waiting
 * for the next ActivityBar poll tick (up to STATUS_POLL_MS). Does not touch
 * connectionState/backendVersion semantics beyond what the shared poll already
 * does, and does not introduce a new polling interval (I7) — a single one-shot
 * GET /status. Never awaited by the caller; errors are silently swallowed.
 */
export function refreshStatusNow(): void {
  void fetchStatus()
    .then((res) => {
      useStatusStore.getState().setBackendVersion(res.version);
      useStatusStore.getState().setReviewPending(res.review_pending);
      useStatusStore.getState().setSupportsVision(res.supports_vision ?? false);
      useStatusStore.getState().setDataVersion(res.data_version ?? null);
      useStatusStore.getState().setUptimeSeconds(res.uptime_seconds);
    })
    .catch(() => {
      // fire-and-forget: transient network errors are acceptable
    });
}
