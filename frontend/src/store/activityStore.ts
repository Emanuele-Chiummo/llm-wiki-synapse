/**
 * activityStore.ts — Zustand store for the live ingest queue (Activity Panel, F1).
 *
 * Polling strategy (mirrors ingestStore.ts ADR-0018 §3):
 *   - Fast poll: 1500ms while processing > 0 || pending > 0 || paused.
 *   - Slow poll: 5000ms when idle (queue is empty and not paused).
 *   - Single setTimeout chain — no setInterval, no runaway loop (I7).
 *   - One AbortController per active poll; aborted on stopPolling().
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
    const ctrl = new AbortController();
    let timerId: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (ctrl.signal.aborted) return;
      try {
        const snap = await getIngestQueue(ctrl.signal);
        if (ctrl.signal.aborted) return;
        // Clear cancellingIds that are no longer in the task list.
        const currentIds = new Set(
          snap.tasks.filter((t) => t.run_id !== undefined).map((t) => t.run_id as string),
        );
        set((s) => {
          const nextCancelling = new Set<string>();
          for (const id of s.cancellingIds) {
            if (currentIds.has(id)) nextCancelling.add(id);
          }
          return { snapshot: snap, error: null, cancellingIds: nextCancelling };
        });
      } catch (err: unknown) {
        if (ctrl.signal.aborted) return;
        if (err instanceof Error && err.name !== "AbortError") {
          set({ error: (err as Error).message });
        }
        // Keep polling on transient errors (backend may restart).
      }

      if (ctrl.signal.aborted) return;

      const { snapshot } = get();
      const isActive =
        (snapshot?.processing ?? 0) > 0 ||
        (snapshot?.pending ?? 0) > 0 ||
        (snapshot?.paused ?? false);
      timerId = setTimeout(() => void tick(), isActive ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    }

    // Kick off immediately.
    timerId = setTimeout(() => void tick(), 0);

    return () => {
      ctrl.abort();
      if (timerId !== null) clearTimeout(timerId);
    };
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
