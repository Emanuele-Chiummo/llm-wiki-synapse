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
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { IngestRunItem } from "../api/types";
import { fetchIngestRuns } from "../api/ingestClient";

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
}

export type IngestStore = IngestState & IngestActions;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function countRunning(runs: IngestRunItem[]): number {
  return runs.filter((r) => r.status === "running").length;
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
      const opts = vaultId !== undefined ? { limit: PAGE_LIMIT, offset: 0, vaultId } : { limit: PAGE_LIMIT, offset: 0 };
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
      const opts = vaultId !== undefined ? { limit: PAGE_LIMIT, offset: nextOffset, vaultId } : { limit: PAGE_LIMIT, offset: nextOffset };
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

  startPolling: (vaultId) => {
    const ctrl = new AbortController();

    async function tick() {
      if (ctrl.signal.aborted) return;
      const { runningCount } = get();
      if (runningCount === 0) return; // bounded: stop when no running rows
      try {
        const pollOpts = vaultId !== undefined ? { limit: PAGE_LIMIT, offset: 0, vaultId } : { limit: PAGE_LIMIT, offset: 0 };
        const res = await fetchIngestRuns(pollOpts, ctrl.signal);
        if (!ctrl.signal.aborted) {
          const running = countRunning(res.items);
          set((s) => ({
            // Replace the first page in the accumulated list; keep deeper pages
            runs: [...res.items, ...s.runs.slice(res.items.length)],
            total: res.total,
            runningCount: running,
          }));
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

export function selectRuns(s: IngestStore): IngestRunItem[] { return s.runs; }
export function selectIngestTotal(s: IngestStore): number { return s.total; }
export function selectIngestLoading(s: IngestStore): boolean { return s.loading; }
export function selectIngestError(s: IngestStore): string | null { return s.error; }
export function selectSelectedRunId(s: IngestStore): string | null { return s.selectedRunId; }
export function selectRunningCount(s: IngestStore): number { return s.runningCount; }
export function selectFetchFresh(s: IngestStore): IngestActions["fetchFresh"] { return s.fetchFresh; }
export function selectFetchMore(s: IngestStore): IngestActions["fetchMore"] { return s.fetchMore; }
export function selectSetSelectedRunId(s: IngestStore): IngestActions["setSelectedRunId"] { return s.setSelectedRunId; }
export function selectStartPolling(s: IngestStore): IngestActions["startPolling"] { return s.startPolling; }

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
