/**
 * researchStore.ts — Zustand store for deep-research runs (F10, ADR-0024 §8).
 *
 * INVARIANT I3: separate from graphStore — research polling never causes the graph
 *               to re-render; Zustand selectors + shallow equality on collections.
 * INVARIANT I7: total_cost_usd is always present and rendered at 4dp by consumers.
 * INVARIANT I4: this store accumulates pages in `runs[]`; the UI virtualises the list.
 *
 * Polling strategy: built on the shared `createPollChain` primitive (FE-ARCH-2).
 *   - Polls GET /research/runs/{id} every 5s ONLY while the selected run has
 *     status "running". Stops automatically on a terminal status.
 *   - Interval cleanup is ALWAYS called on unmount (I3 — no runaway timers).
 *
 * Terminal statuses (stop polling): converged | max_iter_reached | budget_exhausted | error.
 * Running status (keep polling): running.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { ResearchRunSummary, ResearchRunDetail } from "../api/types";
import { fetchResearchRuns, fetchResearchRunDetail, startResearch } from "../api/researchClient";
import { createPollChain } from "./pollChain";

// ─── Constants ────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 5_000;
const PAGE_LIMIT = 20;

/** Statuses that mean the run is finished; polling MUST stop. */
const TERMINAL_STATUSES = new Set(["converged", "max_iter_reached", "budget_exhausted", "error"]);

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

// ─── State / Actions ─────────────────────────────────────────────────────────

// ─── Prefill slice (B5/D3) ────────────────────────────────────────────────────

/**
 * Prefill payload written by Graph Insights' Deep Research button (B5/D3).
 * Consumed by DeepSearchView to seed the ResearchTopicDialog on mount.
 * Cleared immediately after the dialog opens or is cancelled.
 */
export interface ResearchPrefill {
  topic: string;
  queries: string[];
}

interface ResearchState {
  /** Paginated run list (summary rows, started_at DESC). */
  runs: ResearchRunSummary[];
  total: number;
  offset: number;
  listLoading: boolean;
  listError: string | null;

  /** The currently selected run's full detail (null if none selected). */
  selectedRunId: string | null;
  detail: ResearchRunDetail | null;
  detailLoading: boolean;
  detailError: string | null;

  /** Count of runs currently in "running" status (drives badge + poll). */
  runningCount: number;

  /** Whether a "start research" request is in-flight. */
  starting: boolean;
  startError: string | null;

  /**
   * Prefill data written by Graph Insights (B5/D3).
   * Non-null signals DeepSearchView to open the ResearchTopicDialog on mount.
   * Cleared on dialog open, confirm, or cancel.
   */
  prefill: ResearchPrefill | null;
}

interface ResearchActions {
  /** Fetch the run list from offset 0, replacing the list. */
  fetchFresh: (vaultId?: string, signal?: AbortSignal) => Promise<void>;
  /** Fetch the next page (offset += PAGE_LIMIT), appending. */
  fetchMore: (vaultId?: string) => Promise<void>;

  /** Select a run and load its full detail. */
  selectRun: (runId: string | null) => Promise<void>;

  /**
   * Start a new deep-research run.
   * On success: fetches the run list fresh and selects the new run.
   */
  startRun: (params: {
    vault_id: string;
    topic: string;
    /** Optional seed queries from the ResearchTopicDialog (B5/D3). */
    queries?: string[];
    max_iter?: number;
    token_budget?: number;
  }) => Promise<string>; // returns run_id on success

  /**
   * Start polling GET /research/runs/{id} for the selected run while it is
   * "running". The caller holds and calls the returned cleanup fn on unmount.
   *
   * I3: interval is a setTimeout chain, NOT setInterval; stops on terminal status.
   */
  startPollingDetail: (runId: string) => () => void;

  clearStartError: () => void;

  /** Write prefill data so DeepSearchView opens the confirm dialog on next mount (B5/D3). */
  setResearchPrefill: (prefill: ResearchPrefill) => void;
  /** Clear the prefill slice (called when dialog opens, cancels, or confirms). */
  clearResearchPrefill: () => void;

  /** FE-UIUX-3: clear all vault-scoped deep-research state on vault switch. */
  resetForVault: () => void;
}

export type ResearchStore = ResearchState & ResearchActions;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function countRunning(runs: ResearchRunSummary[]): number {
  return runs.filter((r) => r.status === "running").length;
}

// ─── Store ────────────────────────────────────────────────────────────────────

export const useResearchStore = create<ResearchStore>((set, get) => ({
  runs: [],
  total: 0,
  offset: 0,
  listLoading: false,
  listError: null,

  selectedRunId: null,
  detail: null,
  detailLoading: false,
  detailError: null,

  runningCount: 0,

  starting: false,
  startError: null,

  prefill: null,

  // ── fetchFresh ─────────────────────────────────────────────────────────────
  fetchFresh: async (vaultId, signal) => {
    set({ listLoading: true, listError: null });
    try {
      const opts =
        vaultId !== undefined
          ? { limit: PAGE_LIMIT, offset: 0, vaultId }
          : { limit: PAGE_LIMIT, offset: 0 };
      const res = await fetchResearchRuns(opts, signal);
      const running = countRunning(res.items);
      set({
        runs: res.items,
        total: res.total,
        offset: 0,
        listLoading: false,
        runningCount: running,
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ listError: (err as Error).message, listLoading: false });
    }
  },

  // ── fetchMore ──────────────────────────────────────────────────────────────
  fetchMore: async (vaultId) => {
    const { offset, total, runs, listLoading } = get();
    if (listLoading || runs.length >= total) return;
    const nextOffset = offset + PAGE_LIMIT;
    set({ listLoading: true });
    try {
      const opts =
        vaultId !== undefined
          ? { limit: PAGE_LIMIT, offset: nextOffset, vaultId }
          : { limit: PAGE_LIMIT, offset: nextOffset };
      const res = await fetchResearchRuns(opts);
      const newRuns = [...runs, ...res.items];
      set({
        runs: newRuns,
        offset: nextOffset,
        total: res.total,
        listLoading: false,
        runningCount: countRunning(newRuns),
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ listError: (err as Error).message, listLoading: false });
    }
  },

  // ── selectRun ─────────────────────────────────────────────────────────────
  selectRun: async (runId) => {
    if (runId === null) {
      set({ selectedRunId: null, detail: null, detailError: null });
      return;
    }
    set({ selectedRunId: runId, detailLoading: true, detailError: null, detail: null });
    try {
      const detail = await fetchResearchRunDetail(runId);
      set({ detail, detailLoading: false });
    } catch (err: unknown) {
      set({ detailError: (err as Error).message, detailLoading: false });
    }
  },

  // ── startRun ──────────────────────────────────────────────────────────────
  startRun: async (params) => {
    set({ starting: true, startError: null });
    try {
      const res = await startResearch(params);
      const runId = res.run_id;
      // Refresh the list to show the new run, then select it
      await get().fetchFresh(params.vault_id);
      await get().selectRun(runId);
      set({ starting: false });
      return runId;
    } catch (err: unknown) {
      const msg = (err as Error).message;
      set({ starting: false, startError: msg });
      throw err;
    }
  },

  // ── startPollingDetail ────────────────────────────────────────────────────
  startPollingDetail: (runId) => {
    // Stop if the user navigated away or the run is already terminal (I3/I7).
    const chain = createPollChain({
      shouldContinue: () => {
        const { detail, selectedRunId } = get();
        if (selectedRunId !== runId) return false;
        if (detail !== null && isTerminal(detail.status)) return false;
        return true;
      },
      fetch: (signal) => fetchResearchRunDetail(runId, signal),
      onResult: (updated) => {
        set((s) => ({
          detail: updated,
          // Also keep the list summary in sync (status + cost)
          runs: s.runs.map((r) =>
            r.id === runId
              ? {
                  ...r,
                  status: updated.status,
                  iterations_used: updated.iterations_used,
                  sources_fetched: updated.sources_fetched,
                  total_cost_usd: updated.total_cost_usd,
                  completed_at: updated.completed_at,
                }
              : r,
          ),
          runningCount: s.runs
            .map((r) => (r.id === runId ? { ...r, status: updated.status } : r))
            .filter((r) => r.status === "running").length,
        }));
      },
      // Schedule next tick ONLY if still running (I3 — no tight loop)
      intervalFor: (updated) => (isTerminal(updated.status) ? null : POLL_INTERVAL_MS),
      initialDelayMs: POLL_INTERVAL_MS,
      // Stop polling on error — user can retry by re-selecting (errorIntervalFor omitted).
    });

    return chain.subscribe();
  },

  clearStartError: () => set({ startError: null }),

  // ── prefill (B5/D3) ───────────────────────────────────────────────────────
  setResearchPrefill: (prefill) => set({ prefill }),
  clearResearchPrefill: () => set({ prefill: null }),

  // FE-UIUX-3
  resetForVault: () =>
    set({
      runs: [],
      total: 0,
      offset: 0,
      listLoading: false,
      listError: null,
      selectedRunId: null,
      detail: null,
      detailLoading: false,
      detailError: null,
      runningCount: 0,
      starting: false,
      startError: null,
      prefill: null,
    }),
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────

export function selectResearchRuns(s: ResearchStore): ResearchRunSummary[] {
  return s.runs;
}
export function selectResearchTotal(s: ResearchStore): number {
  return s.total;
}
export function selectResearchListLoading(s: ResearchStore): boolean {
  return s.listLoading;
}
export function selectResearchListError(s: ResearchStore): string | null {
  return s.listError;
}
export function selectSelectedRunId(s: ResearchStore): string | null {
  return s.selectedRunId;
}
export function selectResearchDetail(s: ResearchStore): ResearchRunDetail | null {
  return s.detail;
}
export function selectDetailLoading(s: ResearchStore): boolean {
  return s.detailLoading;
}
export function selectDetailError(s: ResearchStore): string | null {
  return s.detailError;
}
export function selectResearchRunningCount(s: ResearchStore): number {
  return s.runningCount;
}
export function selectStarting(s: ResearchStore): boolean {
  return s.starting;
}
export function selectStartError(s: ResearchStore): string | null {
  return s.startError;
}
export function selectFetchFreshResearch(s: ResearchStore): ResearchActions["fetchFresh"] {
  return s.fetchFresh;
}
export function selectFetchMoreResearch(s: ResearchStore): ResearchActions["fetchMore"] {
  return s.fetchMore;
}
export function selectSelectRun(s: ResearchStore): ResearchActions["selectRun"] {
  return s.selectRun;
}
export function selectStartRun(s: ResearchStore): ResearchActions["startRun"] {
  return s.startRun;
}
export function selectStartPollingDetail(s: ResearchStore): ResearchActions["startPollingDetail"] {
  return s.startPollingDetail;
}
export function selectClearStartError(s: ResearchStore): ResearchActions["clearStartError"] {
  return s.clearStartError;
}
export function selectResearchPrefill(s: ResearchStore): ResearchPrefill | null {
  return s.prefill;
}
export function selectSetResearchPrefill(s: ResearchStore): ResearchActions["setResearchPrefill"] {
  return s.setResearchPrefill;
}
export function selectClearResearchPrefill(
  s: ResearchStore,
): ResearchActions["clearResearchPrefill"] {
  return s.clearResearchPrefill;
}
export function selectResearchResetForVault(s: ResearchStore): ResearchActions["resetForVault"] {
  return s.resetForVault;
}

/** Hook: runs array — shallow equality (I3). */
export function useResearchRuns(): ResearchRunSummary[] {
  return useResearchStore(useShallow(selectResearchRuns));
}

/** Hook: running count for badge. */
export function useResearchRunningCount(): number {
  return useResearchStore(selectResearchRunningCount);
}
