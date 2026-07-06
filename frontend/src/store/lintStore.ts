/**
 * lintStore.ts — Zustand store for K2 Lint-fix (ADR-0037 §6, B1).
 *
 * INVARIANT I3: separate from graphStore — lint actions never cause the graph
 *               to re-render. Zustand selectors + shallow equality on collections.
 * INVARIANT I4: findings list accumulated here; UI virtualises when rendering (I4).
 * INVARIANT I7: total_cost_usd is always present on LintRun; rendered at 4dp by consumers.
 *
 * Actions:
 *   scan             → POST /lint/scan (synchronous bounded run). Updates findings + run.
 *   apply            → POST /lint/findings/{id}/apply. For missing-xref/missing-page: real fix.
 *                      For orphan-page/contradiction/stale-claim: flag-only acknowledge.
 *   dismiss          → POST /lint/findings/{id}/dismiss.
 *   refresh          → GET /lint/findings (replaces open findings list for current vault).
 *   fetchRuns        → GET /lint/runs (run history).
 *   toggleSelect     → toggle a finding id in the selection set (B1-L5).
 *   selectAll        → select all currently-loaded finding ids (B1-L5).
 *   clearSelection   → clear the selection set (B1-L5).
 *   applyBatch       → POST /lint/findings/batch action=apply (B1-L5).
 *   dismissBatch     → POST /lint/findings/batch action=dismiss (B1-L5).
 *   sendToReviewBatch→ POST /lint/findings/batch action=send-to-review (B1-L5/L6).
 *   sendToReview     → POST /lint/findings/{id}/send-to-review (B1-L6).
 *   deleteOrphanPage → DELETE /pages/{pageId} then refresh (B1-L9).
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
  batchLintAction,
  sendLintFindingToReview,
  deleteWikiPage,
} from "../api/lintClient";

// ─── localStorage key ─────────────────────────────────────────────────────────

const LS_SEMANTIC = "synapse.lint.semantic";

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

  /**
   * B1-L8: whether to run the LLM semantic pass during scan.
   * Persisted to localStorage under "synapse.lint.semantic".
   * Defaults to true (same as backend default).
   */
  semanticEnabled: boolean;

  /**
   * B1-L5: set of selected finding ids for batch operations.
   * Using Set for O(1) lookup; Zustand stores it as-is.
   */
  selectedIds: Set<string>;

  /** True while a batch action is in-flight. */
  batchInFlight: boolean;

  /** Error from the last batch action (null when none / cleared). */
  batchError: string | null;
}

interface LintActions {
  /**
   * Run a bounded lint scan.
   * On success: replaces findings + sets currentRun + prepends run to history.
   * On error: sets scanError.
   * Does NOT trigger a full wiki rescan (I1).
   * B1-L8: passes semanticEnabled from store state.
   */
  scan: (vaultId: string, signal?: AbortSignal) => Promise<void>;

  /**
   * Apply (human-gate) a lint finding.
   * - For missing-xref / missing-page: runs the proposed fix (file write server-side).
   * - For broken-wikilink with suggestion: rewrites dangling link (B1-L3).
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

  // ── B1 additions ─────────────────────────────────────────────────────────────

  /** B1-L8: toggle semantic LLM pass for next scan. Persisted to localStorage. */
  setSemanticEnabled: (enabled: boolean) => void;

  /** B1-L5: toggle a finding id in the selection set. */
  toggleSelect: (findingId: string) => void;

  /** B1-L5: select all currently-loaded finding ids. */
  selectAll: () => void;

  /** B1-L5: clear the selection set. */
  clearSelection: () => void;

  /** B1-L5: batch apply selected findings. Returns toast-ready counts. */
  applyBatch: (vaultId: string) => Promise<{ ok: number; err: number }>;

  /** B1-L5: batch dismiss selected findings. Returns toast-ready counts. */
  dismissBatch: (vaultId: string) => Promise<{ ok: number; err: number }>;

  /** B1-L5/L6: batch send-to-review selected findings. Returns toast-ready counts. */
  sendToReviewBatch: (vaultId: string) => Promise<{ ok: number; err: number }>;

  /** B1-L6: send a single finding to the review queue. */
  sendToReview: (findingId: string) => Promise<void>;

  /**
   * B1-L9: delete an orphan wiki page then dismiss/refresh the finding.
   * The caller MUST have shown a two-stage confirm before calling this.
   */
  deleteOrphanPage: (findingId: string, pageId: string, vaultId: string) => Promise<void>;

  clearBatchError: () => void;
}

export type LintStore = LintState & LintActions;

// ─── Store ────────────────────────────────────────────────────────────────────

// Read semantic preference from localStorage on store init (B1-L8).
function readSemanticEnabled(): boolean {
  try {
    const v = localStorage.getItem(LS_SEMANTIC);
    if (v === null) return true; // default on
    return v !== "false";
  } catch {
    return true;
  }
}

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

  // B1 additions
  semanticEnabled: readSemanticEnabled(),
  selectedIds: new Set<string>(),
  batchInFlight: false,
  batchError: null,

  // ── scan ──────────────────────────────────────────────────────────────────
  scan: async (vaultId, signal) => {
    const { semanticEnabled } = get();
    set({ scanning: true, scanError: null });
    try {
      const res = await runLintScan({ vault_id: vaultId }, signal, semanticEnabled);
      // Replace open findings with the ones returned by the scan
      // (the scan already filtered to open status)
      set((s) => ({
        scanning: false,
        currentRun: res.run,
        findings: res.findings,
        findingsTotal: res.findings.length,
        findingsOffset: 0,
        selectedIds: new Set<string>(),
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

  // ── B1: semantic toggle ───────────────────────────────────────────────────
  setSemanticEnabled: (enabled) => {
    try {
      localStorage.setItem(LS_SEMANTIC, enabled ? "true" : "false");
    } catch {
      // ignore — storage unavailable
    }
    set({ semanticEnabled: enabled });
  },

  // ── B1: selection ─────────────────────────────────────────────────────────
  toggleSelect: (findingId) => {
    set((s) => {
      const next = new Set(s.selectedIds);
      if (next.has(findingId)) {
        next.delete(findingId);
      } else {
        next.add(findingId);
      }
      return { selectedIds: next };
    });
  },

  selectAll: () => {
    set((s) => ({
      selectedIds: new Set(s.findings.map((f) => f.id)),
    }));
  },

  clearSelection: () => set({ selectedIds: new Set<string>() }),

  // ── B1: batch actions ─────────────────────────────────────────────────────

  applyBatch: async (vaultId) => {
    const ids = [...get().selectedIds];
    if (ids.length === 0) return { ok: 0, err: 0 };
    set({ batchInFlight: true, batchError: null });
    try {
      const res = await batchLintAction(ids, "apply");
      const okIds = new Set(res.results.filter((r) => r.status === "ok").map((r) => r.id));
      set((s) => ({
        findings: s.findings.filter((f) => !okIds.has(f.id)),
        findingsTotal: Math.max(0, s.findingsTotal - okIds.size),
        selectedIds: new Set<string>(),
        batchInFlight: false,
      }));
      await get().refresh(vaultId);
      return { ok: res.ok_count, err: res.error_count };
    } catch (err: unknown) {
      const msg = (err as Error).message;
      set({ batchInFlight: false, batchError: msg });
      return { ok: 0, err: ids.length };
    }
  },

  dismissBatch: async (vaultId) => {
    const ids = [...get().selectedIds];
    if (ids.length === 0) return { ok: 0, err: 0 };
    set({ batchInFlight: true, batchError: null });
    try {
      const res = await batchLintAction(ids, "dismiss");
      const okIds = new Set(res.results.filter((r) => r.status === "ok").map((r) => r.id));
      set((s) => ({
        findings: s.findings.filter((f) => !okIds.has(f.id)),
        findingsTotal: Math.max(0, s.findingsTotal - okIds.size),
        selectedIds: new Set<string>(),
        batchInFlight: false,
      }));
      await get().refresh(vaultId);
      return { ok: res.ok_count, err: res.error_count };
    } catch (err: unknown) {
      const msg = (err as Error).message;
      set({ batchInFlight: false, batchError: msg });
      return { ok: 0, err: ids.length };
    }
  },

  sendToReviewBatch: async (vaultId) => {
    const ids = [...get().selectedIds];
    if (ids.length === 0) return { ok: 0, err: 0 };
    set({ batchInFlight: true, batchError: null });
    try {
      const res = await batchLintAction(ids, "send-to-review");
      const okIds = new Set(res.results.filter((r) => r.status === "ok").map((r) => r.id));
      set((s) => ({
        findings: s.findings.filter((f) => !okIds.has(f.id)),
        findingsTotal: Math.max(0, s.findingsTotal - okIds.size),
        selectedIds: new Set<string>(),
        batchInFlight: false,
      }));
      await get().refresh(vaultId);
      return { ok: res.ok_count, err: res.error_count };
    } catch (err: unknown) {
      const msg = (err as Error).message;
      set({ batchInFlight: false, batchError: msg });
      return { ok: 0, err: ids.length };
    }
  },

  // ── B1: single send-to-review ────────────────────────────────────────────
  sendToReview: async (findingId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [findingId]: "apply" },
      actionError: { ...s.actionError, [findingId]: null },
    }));
    try {
      await sendLintFindingToReview(findingId);
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

  // ── B1: delete orphan page ────────────────────────────────────────────────
  deleteOrphanPage: async (findingId, pageId, vaultId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [findingId]: "apply" },
      actionError: { ...s.actionError, [findingId]: null },
    }));
    try {
      await deleteWikiPage(pageId);
      // Optimistically remove the finding; a refresh will reconcile
      set((s) => ({
        findings: s.findings.filter((f) => f.id !== findingId),
        findingsTotal: Math.max(0, s.findingsTotal - 1),
        actionInFlight: { ...s.actionInFlight, [findingId]: null },
      }));
      await get().refresh(vaultId);
    } catch (err: unknown) {
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [findingId]: null },
        actionError: { ...s.actionError, [findingId]: (err as Error).message },
      }));
    }
  },

  clearBatchError: () => set({ batchError: null }),
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

// ─── B1 selectors ─────────────────────────────────────────────────────────────

export function selectLintSemanticEnabled(s: LintStore): boolean {
  return s.semanticEnabled;
}
export function selectLintSelectedIds(s: LintStore): Set<string> {
  return s.selectedIds;
}
export function selectLintBatchInFlight(s: LintStore): boolean {
  return s.batchInFlight;
}
export function selectLintBatchError(s: LintStore): string | null {
  return s.batchError;
}
export function selectLintSetSemanticEnabled(
  s: LintStore,
): LintActions["setSemanticEnabled"] {
  return s.setSemanticEnabled;
}
export function selectLintToggleSelect(s: LintStore): LintActions["toggleSelect"] {
  return s.toggleSelect;
}
export function selectLintSelectAll(s: LintStore): LintActions["selectAll"] {
  return s.selectAll;
}
export function selectLintClearSelection(s: LintStore): LintActions["clearSelection"] {
  return s.clearSelection;
}
export function selectLintApplyBatch(s: LintStore): LintActions["applyBatch"] {
  return s.applyBatch;
}
export function selectLintDismissBatch(s: LintStore): LintActions["dismissBatch"] {
  return s.dismissBatch;
}
export function selectLintSendToReviewBatch(
  s: LintStore,
): LintActions["sendToReviewBatch"] {
  return s.sendToReviewBatch;
}
export function selectLintSendToReview(s: LintStore): LintActions["sendToReview"] {
  return s.sendToReview;
}
export function selectLintDeleteOrphanPage(
  s: LintStore,
): LintActions["deleteOrphanPage"] {
  return s.deleteOrphanPage;
}
export function selectLintClearBatchError(s: LintStore): LintActions["clearBatchError"] {
  return s.clearBatchError;
}
