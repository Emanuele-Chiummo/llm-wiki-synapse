/**
 * activityStore.ts — Zustand store for the live ingest queue (Activity Panel, F1).
 *
 * Polling strategy: adaptive setTimeout chain (fast 1500ms while active, slow
 * 5000ms when idle), shared across all subscribers via `createPollChain`'s
 * refcounted `subscribe()` — see `pollChain.ts` (FE-ARCH-2). This kills the
 * "burst of ~5 identical /ingest/queue per tick" regression from StrictMode
 * double-invoke / HMR / extra mount sites.
 *
 * INVARIANT I3: Zustand selectors + useShallow for tasks array. No whole-store
 * subscriptions. No heavy work per render cycle.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { IngestQueueSnapshot, QueueTask } from "../api/types";
import {
  getIngestQueue,
  cancelIngestRun,
  retryIngestRun,
  pauseIngestQueue,
  resumeIngestQueue,
} from "../api/ingestClient";
import { createPollChain } from "./pollChain";

// ─── Structural equality guard (FE-PERF-1) ───────────────────────────────────
//
// getIngestQueue() returns a fresh object on every poll tick even when nothing
// changed server-side (no ETag/If-None-Match). Installing that fresh object into
// the store unconditionally forces every subscriber selector (useActivityCounts,
// useActivityTasks, useActivityBatch — all `useShallow`) to re-run its equality
// check against a brand-new object graph, which re-renders HomeDashboard/ActivityBar
// on every tick even when the snapshot is byte-for-byte identical to the last one.
// A cheap deep-equal guard before set() skips that churn entirely (I3).
//
// The snapshot is plain JSON-shaped data (booleans/numbers/strings/arrays/nested
// objects — see IngestQueueSnapshot/QueueTask) — no Dates, Maps, or functions —
// so a small recursive structural comparison is sufficient and cheap relative to
// the poll interval (≥1.5s).
function deepEqualJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (!deepEqualJson(a[i], b[i])) return false;
    }
    return true;
  }
  const aRec = a as Record<string, unknown>;
  const bRec = b as Record<string, unknown>;
  const aKeys = Object.keys(aRec);
  const bKeys = Object.keys(bRec);
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    if (!Object.prototype.hasOwnProperty.call(bRec, key)) return false;
    if (!deepEqualJson(aRec[key], bRec[key])) return false;
  }
  return true;
}

// ─── Constants ────────────────────────────────────────────────────────────────

/** Poll interval while there is active work (processing | pending | paused). */
const POLL_ACTIVE_MS = 1_500;
/** Poll interval when the queue is fully idle and unpaused. */
const POLL_IDLE_MS = 5_000;
/**
 * Cap on how many failed tasks we keep rendered in the panel.
 * I4: keeps the DOM bounded for single-vault use (practically always small).
 */
export const MAX_VISIBLE_FAILED = 50;

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ActivityState {
  snapshot: IngestQueueSnapshot | null;
  loading: boolean;
  error: string | null;
  /** run_ids currently in "cancelling" transient state (cleared on next poll). */
  cancellingIds: Set<string>;
}

export interface ActivityActions {
  /** Trigger a single immediate fetch of the queue snapshot. */
  fetchOnce: (signal?: AbortSignal) => Promise<void>;
  /** Start the adaptive poll loop. Returns a cleanup function. */
  startPolling: () => () => void;
  /** Cancel an active run; marks it as cancelling until next poll removes it. */
  cancelRun: (runId: string, signal?: AbortSignal) => Promise<void>;
  /**
   * Retry a failed run.
   * Throws MaxRetriesExceededError when retry_count >= 3 (caller should surface message).
   */
  retryRun: (runId: string, signal?: AbortSignal) => Promise<void>;
  /** Toggle pause/resume. */
  togglePause: (signal?: AbortSignal) => Promise<void>;
}

export type ActivityStore = ActivityState & ActivityActions;

// ─── Shared singleton poll chain (dedup guard) ──────────────────────────────────
//
// Module-level so that every startPolling() caller shares ONE GET /ingest/queue
// chain via createPollChain's refcounted subscribe() (FE-ARCH-2). Without this,
// StrictMode's double-invoke, Vite HMR reloads that leave orphan chains, and any
// extra mount site each spawn an independent poll loop — producing bursts of
// identical requests per tick.
let sharedPollChain: ReturnType<typeof createPollChain<IngestQueueSnapshot>> | null = null;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useActivityStore = create<ActivityStore>((set, get) => ({
  snapshot: null,
  loading: false,
  error: null,
  cancellingIds: new Set<string>(),

  fetchOnce: async (signal) => {
    set({ loading: true, error: null });
    try {
      const snap = await getIngestQueue(signal);
      if (signal?.aborted) return;
      set({ snapshot: snap, loading: false });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message, loading: false });
    }
  },

  startPolling: () => {
    if (sharedPollChain === null) {
      sharedPollChain = createPollChain<IngestQueueSnapshot>({
        fetch: (signal) => getIngestQueue(signal),
        onResult: (snap) => {
          // Clear cancellingIds that are no longer in the task list.
          const currentIds = new Set(
            snap.tasks.filter((t) => t.run_id !== undefined).map((t) => t.run_id as string),
          );
          set((s) => {
            const nextCancelling = new Set<string>();
            for (const id of s.cancellingIds) {
              if (currentIds.has(id)) nextCancelling.add(id);
            }
            // FE-PERF-1: skip the set() entirely when the new snapshot is structurally
            // identical to the current one AND cancellingIds hasn't changed either —
            // avoids a re-render burst on every idle poll tick (I3).
            const cancellingUnchanged =
              nextCancelling.size === s.cancellingIds.size &&
              [...nextCancelling].every((id) => s.cancellingIds.has(id));
            if (
              s.error === null &&
              cancellingUnchanged &&
              s.snapshot !== null &&
              deepEqualJson(s.snapshot, snap)
            ) {
              return s;
            }
            return { snapshot: snap, error: null, cancellingIds: nextCancelling };
          });
        },
        intervalFor: (snap) => {
          const isActive = snap.processing > 0 || snap.pending > 0 || snap.paused;
          return isActive ? POLL_ACTIVE_MS : POLL_IDLE_MS;
        },
        onError: (err) => {
          if (err instanceof Error && err.name !== "AbortError") {
            set({ error: err.message });
          }
        },
        // Keep polling on transient errors (backend may restart) — reuse the
        // last-known active/idle cadence rather than a fixed retry delay.
        errorIntervalFor: () => {
          const { snapshot } = get();
          const isActive =
            (snapshot?.processing ?? 0) > 0 ||
            (snapshot?.pending ?? 0) > 0 ||
            (snapshot?.paused ?? false);
          return isActive ? POLL_ACTIVE_MS : POLL_IDLE_MS;
        },
      });
    }
    // Refcounted singleton: shares ONE chain across every subscriber.
    return sharedPollChain.subscribe();
  },

  cancelRun: async (runId, signal) => {
    // Optimistically mark as cancelling so the row shows transient state.
    set((s) => ({ cancellingIds: new Set([...s.cancellingIds, runId]) }));
    try {
      await cancelIngestRun(runId, signal);
    } catch {
      // On error, remove the optimistic marking — the row stays as-is.
      set((s) => {
        const next = new Set(s.cancellingIds);
        next.delete(runId);
        return { cancellingIds: next };
      });
    }
  },

  retryRun: async (runId, signal) => {
    // May throw MaxRetriesExceededError — caller handles it.
    await retryIngestRun(runId, signal);
    // Trigger a refresh so the new queued task appears quickly.
    void get().fetchOnce(signal);
  },

  togglePause: async (signal) => {
    const { snapshot } = get();
    if (snapshot?.paused) {
      await resumeIngestQueue(signal);
    } else {
      await pauseIngestQueue(signal);
    }
    // Refresh immediately after pause/resume.
    void get().fetchOnce(signal);
  },
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────

export function selectSnapshot(s: ActivityStore): IngestQueueSnapshot | null {
  return s.snapshot;
}
export function selectActivityLoading(s: ActivityStore): boolean {
  return s.loading;
}
export function selectActivityError(s: ActivityStore): string | null {
  return s.error;
}
export function selectCancellingIds(s: ActivityStore): Set<string> {
  return s.cancellingIds;
}
export function selectFetchOnce(s: ActivityStore): ActivityActions["fetchOnce"] {
  return s.fetchOnce;
}
export function selectStartPolling(s: ActivityStore): ActivityActions["startPolling"] {
  return s.startPolling;
}
export function selectCancelRun(s: ActivityStore): ActivityActions["cancelRun"] {
  return s.cancelRun;
}
export function selectRetryRun(s: ActivityStore): ActivityActions["retryRun"] {
  return s.retryRun;
}
export function selectTogglePause(s: ActivityStore): ActivityActions["togglePause"] {
  return s.togglePause;
}

/** Hook: tasks array — shallow equality (I3). */
export function useActivityTasks(): QueueTask[] {
  return useActivityStore(useShallow((s) => s.snapshot?.tasks ?? []));
}

/** Hook: whole-batch progress ("index all") — shallow equality (I3). Null when no batch. */
export function useActivityBatch(): {
  running: boolean;
  done: number;
  total: number;
  eta_seconds: number | null;
} | null {
  return useActivityStore(
    useShallow((s) => {
      const b = s.snapshot?.batch;
      if (!b) return null;
      return {
        running: b.running,
        done: b.done,
        total: b.total,
        eta_seconds: b.eta_seconds ?? null,
      };
    }),
  );
}

/** Hook: stable scalar fields from snapshot — avoids re-render on tasks change. */
export function useActivityCounts(): {
  paused: boolean;
  pending: number;
  processing: number;
  failed: number;
  completed_since_idle: number;
  total: number;
} {
  return useActivityStore(
    useShallow((s) => ({
      paused: s.snapshot?.paused ?? false,
      pending: s.snapshot?.pending ?? 0,
      processing: s.snapshot?.processing ?? 0,
      failed: s.snapshot?.failed ?? 0,
      completed_since_idle: s.snapshot?.completed_since_idle ?? 0,
      total: s.snapshot?.total ?? 0,
    })),
  );
}
