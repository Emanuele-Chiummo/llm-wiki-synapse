/**
 * ingestStore.ts — Zustand store for ingest run history (ADR-0018 §3 / F1-INGEST-VIEW).
 *
 * INVARIANT I3: separate from graphStore — ingest polling never causes the graph to re-render.
 * INVARIANT I7: total_cost_usd is always present and rendered at 4dp by consumers.
 * INVARIANT I4: this store accumulates pages in `runs[]`; the UI virtualizes the list.
 *
 * Polling strategy (ADR-0018 §3):
 *   - Polls GET /ingest/runs every 5s ONLY while at least one run has status "running".
 *   - Uses a single AbortController + setTimeout chain — no setInterval, no runaway loop.
 *   - Stops automatically when no running rows remain.
 *
 * Ingest-completion notification (ADR-0048 §T4c):
 *   - Fired when the poll detects runningCount transitions from >0 to 0 (terminal state).
 *   - Only when isTauri(). Dynamic-import of @tauri-apps/plugin-notification so the
 *     web/PWA build is never affected (tree-shaken; never bundled unless isTauri() at runtime).
 *   - Fire-and-forget; permission requested lazily on first fire.
 *   - INVARIANT I3: notification is event-driven (terminal state), never per-token.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { IngestRunItem, IngestStatus } from "../api/types";
import { fetchIngestRuns, cancelIngestRun } from "../api/ingestClient";
import { isTauri } from "../api/base";

// ─── Constants ────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 5_000;
const PAGE_LIMIT = 20;

// ─── State / Actions ─────────────────────────────────────────────────────────

interface IngestState {
  runs: IngestRunItem[];
  total: number;
  offset: number;
  loading: boolean;
  error: string | null;
  selectedRunId: string | null;
  /** Count of runs currently in "running" status (drives rail badge + polling). */
  runningCount: number;
}

interface IngestActions {
  /** Fetch from offset 0, replacing the list. */
  fetchFresh: (vaultId?: string, signal?: AbortSignal) => Promise<void>;
  /** Fetch the next page (offset += PAGE_LIMIT), appending. */
  fetchMore: (vaultId?: string) => Promise<void>;
  setSelectedRunId: (id: string | null) => void;
  /** Start polling while any run is "running". Caller holds and aborts the controller. */
  startPolling: (vaultId?: string) => () => void;
  /**
   * Cancel an active ingest run (R13-3).
   * Optimistically updates the run status to "cancelling" or "cancelled".
   * Throws ApiError on 404 (unknown) or 409 (already terminal) — caller should toast.
   */
  cancelRun: (runId: string, signal?: AbortSignal) => Promise<void>;
}

export type IngestStore = IngestState & IngestActions;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function countRunning(runs: IngestRunItem[]): number {
  return runs.filter((r) => r.status === "running").length;
}

/**
 * sendIngestNotification — fire a system notification when an ingest run completes.
 *
 * Dynamic-import guards the @tauri-apps/plugin-notification so it is NEVER bundled
 * into the web/PWA build. This function is only called when isTauri() is true.
 * It is fire-and-forget; any error is silently swallowed so it never breaks ingest.
 *
 * Permission is requested lazily on first call (ADR-0048 §T4c).
 * Uses i18n key desktop.notify.ingestDone / desktop.notify.ingestFailed.
 */
async function sendIngestNotification(completed: IngestRunItem[]): Promise<void> {
  try {
    // Dynamic import — not bundled in web/PWA (tree-shaken when isTauri() is false)
    const { isPermissionGranted, requestPermission, sendNotification } =
      await import("@tauri-apps/plugin-notification");

    let granted = await isPermissionGranted();
    if (!granted) {
      const permission = await requestPermission();
      granted = permission === "granted";
    }
    if (!granted) return;

    const failed = completed.filter((r) => r.status === "failed");
    const succeeded = completed.filter((r) => r.status !== "failed");

    // i18n instance works outside React components (i18n.t) — localized like the UI.
    const { default: i18n } = await import("../i18n");

    if (failed.length > 0) {
      // Use pages_created + id as context — IngestRunItem has no file_path field
      const count = failed.length;
      const label = count === 1 ? `run ${failed[0]?.id.slice(0, 8) ?? "?"}` : `${count} run`;
      sendNotification({
        title: "Synapse",
        body: i18n.t("desktop.notify.ingestFailed", { label }),
      });
    } else if (succeeded.length > 0) {
      const count = succeeded.length;
      const pages = succeeded.reduce((sum, r) => sum + r.pages_created, 0);
      const label =
        count === 1
          ? i18n.t("desktop.notify.pagesCreated", { count: pages })
          : `${count} run · ${i18n.t("desktop.notify.pagesCreated", { count: pages })}`;
      sendNotification({
        title: "Synapse",
        body: i18n.t("desktop.notify.ingestDone", { label }),
      });
    }
  } catch {
    // Silently ignore — notification failure must never break ingest (ADR-0048 §T4c)
  }
}

// ─── Store ────────────────────────────────────────────────────────────────────

export const useIngestStore = create<IngestStore>((set, get) => ({
  runs: [],
  total: 0,
  offset: 0,
  loading: false,
  error: null,
  selectedRunId: null,
  runningCount: 0,

  fetchFresh: async (vaultId, signal) => {
    set({ loading: true, error: null });
    try {
      const opts =
        vaultId !== undefined
          ? { limit: PAGE_LIMIT, offset: 0, vaultId }
          : { limit: PAGE_LIMIT, offset: 0 };
      const res = await fetchIngestRuns(opts, signal);
      const running = countRunning(res.items);
      set({
        runs: res.items,
        total: res.total,
        offset: 0,
        loading: false,
        runningCount: running,
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message, loading: false });
    }
  },

  fetchMore: async (vaultId) => {
    const { offset, total, runs, loading } = get();
    if (loading || runs.length >= total) return;
    const nextOffset = offset + PAGE_LIMIT;
    set({ loading: true });
    try {
      const opts =
        vaultId !== undefined
          ? { limit: PAGE_LIMIT, offset: nextOffset, vaultId }
          : { limit: PAGE_LIMIT, offset: nextOffset };
      const res = await fetchIngestRuns(opts);
      const newRuns = [...runs, ...res.items];
      set({
        runs: newRuns,
        offset: nextOffset,
        total: res.total,
        loading: false,
        runningCount: countRunning(newRuns),
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message, loading: false });
    }
  },

  setSelectedRunId: (selectedRunId) => set({ selectedRunId }),

  cancelRun: async (runId, signal) => {
    // Optimistic: immediately show the run as "cancelling" so the UI responds instantly.
    set((s) => {
      const runs = s.runs.map((r) =>
        r.id === runId ? { ...r, status: "cancelling" as IngestStatus } : r,
      );
      return { runs, runningCount: countRunning(runs) };
    });
    try {
      const result = await cancelIngestRun(runId, signal);
      // Confirm with the server-authoritative status ("cancelling" or "cancelled").
      set((s) => {
        const runs = s.runs.map((r) =>
          r.id === runId ? { ...r, status: result.status as IngestStatus } : r,
        );
        return { runs, runningCount: countRunning(runs) };
      });
    } catch (err) {
      // Revert the optimistic status on error — poll will reconcile next tick.
      set((s) => {
        const runs = s.runs.map((r) =>
          r.id === runId && r.status === "cancelling"
            ? { ...r, status: "running" as IngestStatus }
            : r,
        );
        return { runs, runningCount: countRunning(runs) };
      });
      // Re-throw so callers (UI) can surface a toast for 404 / 409.
      throw err;
    }
  },

  startPolling: (vaultId) => {
    const ctrl = new AbortController();

    async function tick() {
      if (ctrl.signal.aborted) return;
      const { runningCount: prevRunning, runs: prevRuns } = get();
      if (prevRunning === 0) return; // bounded: stop when no running rows
      try {
        const pollOpts =
          vaultId !== undefined
            ? { limit: PAGE_LIMIT, offset: 0, vaultId }
            : { limit: PAGE_LIMIT, offset: 0 };
        const res = await fetchIngestRuns(pollOpts, ctrl.signal);
        if (!ctrl.signal.aborted) {
          const running = countRunning(res.items);
          set((s) => ({
            // Replace the first page in the accumulated list; keep deeper pages
            runs: [...res.items, ...s.runs.slice(res.items.length)],
            total: res.total,
            runningCount: running,
          }));

          // ── Ingest-completion notification (ADR-0048 §T4c) ────────────────
          // Detect transition: was running, now 0 running → terminal state.
          // Find the runs that were "running" in the previous snapshot and are
          // now completed/failed. Fire-and-forget; only when isTauri().
          if (prevRunning > 0 && running === 0 && isTauri()) {
            const prevRunningIds = new Set(
              prevRuns.filter((r) => r.status === "running").map((r) => r.id),
            );
            const nowTerminal = res.items.filter(
              (r) => prevRunningIds.has(r.id) && r.status !== "running",
            );
            if (nowTerminal.length > 0) {
              void sendIngestNotification(nowTerminal);
            }
          }

          if (running > 0) {
            setTimeout(() => void tick(), POLL_INTERVAL_MS);
          }
        }
      } catch {
        // Stop polling on error — user can manually refresh
      }
    }

    setTimeout(() => void tick(), POLL_INTERVAL_MS);

    return () => ctrl.abort();
  },
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────

export function selectRuns(s: IngestStore): IngestRunItem[] {
  return s.runs;
}
export function selectIngestTotal(s: IngestStore): number {
  return s.total;
}
export function selectIngestLoading(s: IngestStore): boolean {
  return s.loading;
}
export function selectIngestError(s: IngestStore): string | null {
  return s.error;
}
export function selectSelectedRunId(s: IngestStore): string | null {
  return s.selectedRunId;
}
export function selectRunningCount(s: IngestStore): number {
  return s.runningCount;
}
export function selectFetchFresh(s: IngestStore): IngestActions["fetchFresh"] {
  return s.fetchFresh;
}
export function selectFetchMore(s: IngestStore): IngestActions["fetchMore"] {
  return s.fetchMore;
}
export function selectSetSelectedRunId(s: IngestStore): IngestActions["setSelectedRunId"] {
  return s.setSelectedRunId;
}
export function selectStartPolling(s: IngestStore): IngestActions["startPolling"] {
  return s.startPolling;
}
export function selectCancelRun(s: IngestStore): IngestActions["cancelRun"] {
  return s.cancelRun;
}

/** Hook: runs array — shallow equality (I3). */
export function useIngestRuns(): IngestRunItem[] {
  return useIngestStore(useShallow(selectRuns));
}

/**
 * Hook: count of running runs — used by NavRail badge (ADR-0018 §2).
 * Kept separate from graphStore so ingest polling never re-renders the graph.
 */
export function useIngestRunningCount(): number {
  return useIngestStore(selectRunningCount);
}
