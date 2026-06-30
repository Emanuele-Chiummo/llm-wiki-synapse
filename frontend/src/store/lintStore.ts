/**
 * lintStore.ts — Zustand store for K2 Lint-fix (ADR-0037 §6).
 *
 * INVARIANT I3: separate from graphStore — lint actions never cause the graph
 *               to re-render. Zustand selectors + shallow equality on collections.
 * INVARIANT I4: findings list accumulated here; UI virtualises when rendering (I4).
 * INVARIANT I7: total_cost_usd is always present on LintRun; rendered at 4dp by consumers.
 *
 * Actions:
 *   scan       → POST /lint/scan (synchronous bounded run). Updates findings + run.
 *   apply      → POST /lint/findings/{id}/apply. For missing-xref/missing-page: real fix.
 *                For orphan-page/contradiction/stale-claim: flag-only acknowledge.
 *   dismiss    → POST /lint/findings/{id}/dismiss.
 *   refresh    → GET /lint/findings (replaces open findings list for current vault).
 *   fetchRuns  → GET /lint/runs (run history).
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { LintFinding, LintRun } from "../api/types";
import {
  runLintScan,
  fetchLintRuns,
  fetchLintFindings,
  applyLintFinding,
  dismissLintFinding,
} from "../api/lintClient";

// ─── Constants ────────────────────────────────────────────────────────────────

const PAGE_LIMIT = 50;

// ─── State / Actions ──────────────────────────────────────────────────────────

interface LintState {
  /** Open lint findings (created_at ASC). */
  findings: LintFinding[];
  findingsTotal: number;
  findingsOffset: number;
  findingsLoading: boolean;
  findingsError: string | null;

  /** Run history (created_at DESC). */
  runs: LintRun[];
  runsTotal: number;
  runsLoading: boolean;
  runsError: string | null;

  /**
   * The most recent scan result (run row). Updated each time scan() completes.
   * null until the first scan.
   */
  currentRun: LintRun | null;

  /** True while POST /lint/scan is in-flight. */
  scanning: boolean;
  scanError: string | null;

  /**
   * In-flight action per finding id.
   * "apply" | "dismiss" | null
   */
  actionInFlight: Record<string, "apply" | "dismiss" | null>;

  /** Per-finding action error. */
  actionError: Record<string, string | null>;
}

interface LintActions {
  /**
   * Run a bounded lint scan.
   * On success: replaces findings + sets currentRun + prepends run to history.
   * On error: sets scanError.
   * Does NOT trigger a full wiki rescan (I1).
   */
  scan: (vaultId: string, signal?: AbortSignal) => Promise<void>;

  /**
   * Apply (human-gate) a lint finding.
   * - For missing-xref / missing-page: runs the proposed fix (file write server-side).
   * - For orphan-page / contradiction / stale-claim: flag-only acknowledgement.
   * On success: removes finding from open list.
   */
  apply: (findingId: string) => Promise<void>;

  /**
   * Dismiss a lint finding (no fix, no action).
   * On success: removes finding from open list.
   */
  dismiss: (findingId: string) => Promise<void>;

  /**
   * Refresh open findings for vault from offset 0 (replaces list).
   */
  refresh: (vaultId: string, signal?: AbortSignal) => Promise<void>;

  /** Fetch more findings (offset paging). */
  fetchMoreFindings: (vaultId: string) => Promise<void>;

  /** Fetch run history. */
  fetchRuns: (vaultId?: string, signal?: AbortSignal) => Promise<void>;

  clearScanError: () => void;
  clearActionError: (findingId: string) => void;
}

export type LintStore = LintState & LintActions;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useLintStore = create<LintStore>((set, get) => ({
  findings: [],
  findingsTotal: 0,
  findingsOffset: 0,
  findingsLoading: false,
  findingsError: null,

  runs: [],
  runsTotal: 0,
  runsLoading: false,
  runsError: null,

  currentRun: null,
  scanning: false,
  scanError: null,

  actionInFlight: {},
  actionError: {},

  // ── scan ──────────────────────────────────────────────────────────────────
  scan: async (vaultId, signal) => {
    set({ scanning: true, scanError: null });
    try {
      const res = await runLintScan({ vault_id: vaultId }, signal);
      // Replace open findings with the ones returned by the scan
      // (the scan already filtered to open status)
      set((s) => ({
        scanning: false,
        currentRun: res.run,
        findings: res.findings,
        findingsTotal: res.findings.length,
        findingsOffset: 0,
        // Prepend to run history (most recent first)
        runs: [res.run, ...s.runs.filter((r) => r.id !== res.run.id)],
        runsTotal: s.runsTotal + (s.runs.some((r) => r.id === res.run.id) ? 0 : 1),
      }));
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") {
        set({ scanning: false });
        return;
      }
      set({ scanning: false, scanError: (err as Error).message });
    }
  },

  // ── apply ─────────────────────────────────────────────────────────────────
  apply: async (findingId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [findingId]: "apply" },
      actionError: { ...s.actionError, [findingId]: null },
    }));
    try {
      await applyLintFinding(findingId);
      // Optimistic: remove from open list on success
      set((s) => ({
        findings: s.findings.filter((f) => f.id !== findingId),
        findingsTotal: Math.max(0, s.findingsTotal - 1),
        actionInFlight: { ...s.actionInFlight, [findingId]: null },
      }));
    } catch (err: unknown) {
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [findingId]: null },
        actionError: { ...s.actionError, [findingId]: (err as Error).message },
      }));
    }
  },

  // ── dismiss ───────────────────────────────────────────────────────────────
  dismiss: async (findingId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [findingId]: "dismiss" },
      actionError: { ...s.actionError, [findingId]: null },
    }));
    try {
      await dismissLintFinding(findingId);
      // Optimistic: remove from open list on success
      set((s) => ({
        findings: s.findings.filter((f) => f.id !== findingId),
        findingsTotal: Math.max(0, s.findingsTotal - 1),
        actionInFlight: { ...s.actionInFlight, [findingId]: null },
      }));
    } catch (err: unknown) {
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [findingId]: null },
        actionError: { ...s.actionError, [findingId]: (err as Error).message },
      }));
    }
  },

  // ── refresh ───────────────────────────────────────────────────────────────
  refresh: async (vaultId, signal) => {
    set({ findingsLoading: true, findingsError: null });
    try {
      const res = await fetchLintFindings(
        { vaultId, status: "open", limit: PAGE_LIMIT, offset: 0 },
        signal,
      );
      set({
        findings: res.items,
        findingsTotal: res.total,
        findingsOffset: 0,
        findingsLoading: false,
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") {
        set({ findingsLoading: false });
        return;
      }
      set({ findingsError: (err as Error).message, findingsLoading: false });
    }
  },

  // ── fetchMoreFindings ─────────────────────────────────────────────────────
  fetchMoreFindings: async (vaultId) => {
    const { findingsOffset, findingsTotal, findings, findingsLoading } = get();
    if (findingsLoading || findings.length >= findingsTotal) return;
    const nextOffset = findingsOffset + PAGE_LIMIT;
    set({ findingsLoading: true });
    try {
      const res = await fetchLintFindings({
        vaultId,
        status: "open",
        limit: PAGE_LIMIT,
        offset: nextOffset,
      });
      set((s) => ({
        findings: [...s.findings, ...res.items],
        findingsTotal: res.total,
        findingsOffset: nextOffset,
        findingsLoading: false,
      }));
    } catch (err: unknown) {
      set({ findingsError: (err as Error).message, findingsLoading: false });
    }
  },

  // ── fetchRuns ─────────────────────────────────────────────────────────────
  fetchRuns: async (vaultId, signal) => {
    set({ runsLoading: true, runsError: null });
    try {
      const runsOpts =
        vaultId !== undefined
          ? { vaultId, limit: 20, offset: 0 }
          : { limit: 20, offset: 0 };
      const res = await fetchLintRuns(runsOpts, signal);
      set({
        runs: res.items,
        runsTotal: res.total,
        runsLoading: false,
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") {
        set({ runsLoading: false });
        return;
      }
      set({ runsError: (err as Error).message, runsLoading: false });
    }
  },

  clearScanError: () => set({ scanError: null }),
  clearActionError: (findingId) =>
    set((s) => ({
      actionError: { ...s.actionError, [findingId]: null },
    })),
}));

// ─── Typed selectors (I3) ────────────────────────────────────────────────────

export function selectLintFindings(s: LintStore): LintFinding[] {
  return s.findings;
}
export function selectLintFindingsTotal(s: LintStore): number {
  return s.findingsTotal;
}
export function selectLintFindingsLoading(s: LintStore): boolean {
  return s.findingsLoading;
}
export function selectLintFindingsError(s: LintStore): string | null {
  return s.findingsError;
}
export function selectLintRuns(s: LintStore): LintRun[] {
  return s.runs;
}
export function selectLintCurrentRun(s: LintStore): LintRun | null {
  return s.currentRun;
}
export function selectLintScanning(s: LintStore): boolean {
  return s.scanning;
}
export function selectLintScanError(s: LintStore): string | null {
  return s.scanError;
}
export function selectLintActionInFlight(
  s: LintStore,
): Record<string, "apply" | "dismiss" | null> {
  return s.actionInFlight;
}
export function selectLintActionError(
  s: LintStore,
): Record<string, string | null> {
  return s.actionError;
}
export function selectLintScan(s: LintStore): LintActions["scan"] {
  return s.scan;
}
export function selectLintApply(s: LintStore): LintActions["apply"] {
  return s.apply;
}
export function selectLintDismiss(s: LintStore): LintActions["dismiss"] {
  return s.dismiss;
}
export function selectLintRefresh(s: LintStore): LintActions["refresh"] {
  return s.refresh;
}
export function selectLintFetchMoreFindings(
  s: LintStore,
): LintActions["fetchMoreFindings"] {
  return s.fetchMoreFindings;
}
export function selectLintFetchRuns(s: LintStore): LintActions["fetchRuns"] {
  return s.fetchRuns;
}
export function selectClearLintScanError(
  s: LintStore,
): LintActions["clearScanError"] {
  return s.clearScanError;
}

/** Hook: findings array — shallow equality (I3). */
export function useLintFindings(): LintFinding[] {
  return useLintStore(useShallow(selectLintFindings));
}

/** Hook: per-finding actionInFlight map — shallow equality (I3). */
export function useLintActionInFlight(): Record<
  string,
  "apply" | "dismiss" | null
> {
  return useLintStore(useShallow(selectLintActionInFlight));
}

/** Hook: per-finding actionError map — shallow equality (I3). */
export function useLintActionError(): Record<string, string | null> {
  return useLintStore(useShallow(selectLintActionError));
}
