/**
 * reviewStore.ts — Zustand store for F9 HITL review queue (ADR-0034 §7.1 + ADR-0044).
 *
 * INVARIANT I3: separate from graphStore — review actions never cause the graph
 *               to re-render. Zustand selectors + shallow equality on collections.
 *               Selection Set + active tab live here behind selectors — no per-keystroke
 *               work; a row reads only its own membership via selectIsSelected(id).
 * INVARIANT I4: this store accumulates pages in `items[]`; the UI virtualises
 *               the list when > 50 rows (ADR-0034 §10, I4).
 *               "Select pending" selects only the loaded page (O(loaded) — ADR-0044 §7).
 * INVARIANT I7: GET /review/queue limit is capped (default 50, max 200 server-side).
 *               Bulk actions: len(ids) ≤ REVIEW_BULK_MAX_IDS (400 if exceeded — server enforces).
 *
 * Action semantics (ADR-0034 §7 + ADR-0044 §6):
 *   - Create  → POST /review/queue/{id}/create (preferred alias; 201 on success).
 *               409 = not pending / no provider (item stays pending).
 *               502 = generation failed (item stays pending; show retry-or-skip message).
 *               On 201: item removed from pending list (optimistic).
 *   - Skip    → POST /review/queue/{id}/skip (200). Item removed optimistically.
 *   - Dismiss → POST /review/queue/{id}/dismiss (200, ADR-0044). Item removed optimistically.
 *   - Deep Research → POST /review/queue/{id}/deep-research (202). Item removed optimistically.
 *                     503 = SEARXNG_URL not set; surfaced in deepResearchError.
 *   - Bulk    → POST /review/queue/bulk ({vault_id, action, ids}). Refresh after.
 *   - Clear resolved → DELETE /review/queue/resolved. Refresh after.
 *
 * Status tabs (ADR-0044 §7): "pending" | "resolved" | "dismissed"
 *   Changing tab re-fetches with GET /review/queue?status=<tab>.
 *
 * "Create" replaces the old "Approve" (which was a no-op in ADR-0025).
 * pre_generated_query is removed — content now comes from proposed_title + rationale.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type {
  ReviewItem,
  ReviewDeepResearchResponse,
  ReviewSweepResponse,
  ReviewBulkResponse,
  ReviewClearResolvedResponse,
  PageType,
  ReviewItemType,
  ReviewProposalOrigin,
} from "../api/types";
import {
  fetchReviewQueue,
  createReviewItem,
  skipReviewItem,
  dismissReviewItem,
  deepResearchReviewItem,
  resolveReviewItem,
  bulkReview,
  sweepReviewQueue,
  clearResolved,
  type ReviewQueueStatus,
} from "../api/reviewClient";
import { ApiError } from "../api/graphClient";

// ─── Constants ────────────────────────────────────────────────────────────────

const PAGE_LIMIT = 50;

export interface ReviewFilters {
  itemType: ReviewItemType | null;
  proposalOrigin: ReviewProposalOrigin | null;
  proposedPageType: PageType | null;
}

const EMPTY_REVIEW_FILTERS: ReviewFilters = {
  itemType: null,
  proposalOrigin: null,
  proposedPageType: null,
};

function queueOptions(
  vaultId: string,
  status: ReviewQueueStatus,
  filters: ReviewFilters,
  offset: number,
) {
  return {
    vaultId,
    status,
    limit: PAGE_LIMIT,
    offset,
    itemType: filters.itemType,
    proposalOrigin: filters.proposalOrigin,
    proposedPageType: filters.proposedPageType,
  };
}

// ─── State / Actions ─────────────────────────────────────────────────────────

interface ReviewState {
  /** Pending review proposals (created_at ASC from backend). */
  items: ReviewItem[];
  total: number;
  offset: number;
  loading: boolean;
  error: string | null;

  /**
   * Active status tab (ADR-0044 §7).
   * "pending" = pending items (default).
   * "resolved" = created/auto_resolved/deep_researched set.
   * "dismissed" = dismissed set.
   */
  activeTab: ReviewQueueStatus;

  /** Server-side queue filters. Kept across pagination, tab changes, and refreshes. */
  filters: ReviewFilters;

  /**
   * Selection Set — ids of currently selected items (ADR-0044 §7).
   * Stored as a Set<string>; rows read only their own membership via selectIsSelected(id).
   * "Select pending" adds all currently-loaded pending item ids (O(loaded) — I4).
   */
  selectedIds: Set<string>;

  /**
   * In-flight action state per item id.
   * "create" = lazy page generation; "approve" = acknowledge without creating (R2).
   */
  actionInFlight: Record<
    string,
    "create" | "approve" | "skip" | "dismiss" | "deep-research" | null
  >;

  /** Per-item action error (non-503). */
  actionError: Record<string, string | null>;

  /**
   * Per-item retry-or-skip hint (502 from Create: generation failed, item still pending).
   * Shown as a distinct message so the user knows to retry or skip, not that something broke.
   */
  createGenerationError: Record<string, string | null>;

  /**
   * Last deep-research action result: { itemId, runId }.
   * Surfaced in the UI so the user can jump to the Deep Search view.
   */
  lastDeepResearch: { itemId: string; runId: string } | null;

  /**
   * 503 error from POST .../deep-research (SEARXNG_URL not configured).
   * Shown as a distinct warning (not a generic error).
   */
  deepResearchError: string | null;

  /**
   * Last sweep result (ADR-0034 §6).
   * Shown briefly in the UI after a manual sweep trigger.
   */
  lastSweepResult: ReviewSweepResponse | null;

  /**
   * Last bulk action result (ADR-0044 §6).
   * Shown briefly in the UI after a bulk action.
   */
  lastBulkResult: ReviewBulkResponse | null;

  /**
   * Last clear-resolved result (ADR-0044 §6).
   */
  lastClearResult: ReviewClearResolvedResponse | null;

  /** Error from a bulk action (distinct from per-item actionError). */
  bulkError: string | null;
}

interface ReviewActions {
  /** Fetch the queue from offset 0 (replaces the list). */
  fetchFresh: (vaultId: string, signal?: AbortSignal) => Promise<void>;
  /** Fetch the next page (offset += PAGE_LIMIT), appending. */
  fetchMore: (vaultId: string) => Promise<void>;

  /** Switch the active status tab and re-fetch from offset 0. */
  setActiveTab: (tab: ReviewQueueStatus, vaultId: string) => Promise<void>;

  /** Merge queue filters and re-fetch from offset zero. */
  setFilters: (filters: Partial<ReviewFilters>, vaultId: string) => Promise<void>;

  /** Reset all queue filters and re-fetch from offset zero. */
  clearFilters: (vaultId: string) => Promise<void>;

  /**
   * Create action: lazy on-demand page generation from a proposal (ADR-0034 §5).
   * Runs a bounded LLM call — can take many seconds; UI should show a spinner.
   *
   * On 201: removes the item from the pending list (optimistic).
   * On 409 (not pending / no provider): sets actionError, item stays pending.
   * On 502 (generation failed): sets createGenerationError, item stays pending.
   * Does NOT re-trigger a full ingest scan (I1).
   */
  create: (itemId: string) => Promise<void>;

  /**
   * Skip an item. On success, removes it from the pending list (optimistic).
   */
  skip: (itemId: string) => Promise<void>;

  /**
   * Dismiss an item (ADR-0044 §6). On success, removes from pending list (optimistic).
   * Distinct from skip: dismissed = "hide this, I'm not acting"; skipped = "considered and declined".
   */
  dismiss: (itemId: string) => Promise<void>;

  /**
   * Approve (acknowledge) an item — for confirm + contradiction types (R2).
   * Calls POST /review/queue/bulk with action="mark-resolved" and a single id.
   * Transitions item to auto_resolved (terminal). Does NOT create a page.
   * On success, removes from pending list (optimistic).
   */
  approve: (itemId: string, vaultId: string) => Promise<void>;

  /**
   * Trigger deep research for an item. On success, removes from pending list and
   * stores lastDeepResearch. On 503 (SEARXNG not set), sets deepResearchError.
   */
  deepResearch: (itemId: string) => Promise<ReviewDeepResearchResponse | null>;

  /**
   * Manual auto-resolution sweep (ADR-0034 §6).
   * Triggers Pass-1 (rule-based) + Pass-2 (LLM, if enabled).
   * After completion, refreshes the queue to reflect resolved items.
   */
  sweep: (vaultId: string) => Promise<void>;

  /**
   * Bulk action on selected ids (ADR-0044 §7).
   * POST /review/queue/bulk → refresh queue.
   * Only pending ids mutated; terminal ids counted in skipped_terminal.
   */
  bulkAction: (vaultId: string, action: "skip" | "dismiss" | "mark-resolved") => Promise<void>;

  /**
   * Clear all resolved/terminal rows for the vault (ADR-0044 §6).
   * DELETE /review/queue/resolved → refresh queue.
   * Pending rows are NEVER touched.
   */
  clearResolvedRows: (vaultId: string) => Promise<void>;

  // ── Selection helpers (I3 / I4) ───────────────────────────────────────────

  /** Toggle selection of a single item id. */
  toggleSelected: (id: string) => void;

  /**
   * Select all currently-loaded pending items (O(loaded) — ADR-0044 §10 Do-NOT #8).
   * Only selects items with status="pending".
   */
  selectAllPending: () => void;

  /** Clear entire selection. */
  clearSelection: () => void;

  clearDeepResearchError: () => void;
  clearLastDeepResearch: () => void;
  clearLastSweepResult: () => void;
  clearCreateGenerationError: (itemId: string) => void;
  clearLastBulkResult: () => void;
  clearLastClearResult: () => void;
  clearBulkError: () => void;

  /**
   * FE-UIUX-3: clear all vault-scoped review-queue state when the active
   * vault changes (items, tab/filters, selection, per-item action state).
   */
  resetForVault: () => void;
}

export type ReviewStore = ReviewState & ReviewActions;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useReviewStore = create<ReviewStore>((set, get) => ({
  items: [],
  total: 0,
  offset: 0,
  loading: false,
  error: null,
  activeTab: "pending",
  filters: { ...EMPTY_REVIEW_FILTERS },
  selectedIds: new Set<string>(),
  actionInFlight: {},
  actionError: {},
  createGenerationError: {},
  lastDeepResearch: null,
  deepResearchError: null,
  lastSweepResult: null,
  lastBulkResult: null,
  lastClearResult: null,
  bulkError: null,

  // ── fetchFresh ─────────────────────────────────────────────────────────────
  fetchFresh: async (vaultId, signal) => {
    const { activeTab, filters } = get();
    set({ loading: true, error: null });
    try {
      const res = await fetchReviewQueue(queueOptions(vaultId, activeTab, filters, 0), signal);
      set({
        items: res.items,
        total: res.total,
        offset: 0,
        loading: false,
        selectedIds: new Set<string>(),
      });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message, loading: false });
    }
  },

  // ── fetchMore ──────────────────────────────────────────────────────────────
  fetchMore: async (vaultId) => {
    const { offset, total, items, loading, activeTab, filters } = get();
    if (loading || items.length >= total) return;
    const nextOffset = offset + PAGE_LIMIT;
    set({ loading: true });
    try {
      const res = await fetchReviewQueue(queueOptions(vaultId, activeTab, filters, nextOffset));
      set({
        items: [...items, ...res.items],
        total: res.total,
        offset: nextOffset,
        loading: false,
      });
    } catch (err: unknown) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  // ── setActiveTab ───────────────────────────────────────────────────────────
  setActiveTab: async (tab, vaultId) => {
    set({ activeTab: tab, loading: true, error: null, selectedIds: new Set<string>() });
    try {
      const res = await fetchReviewQueue(queueOptions(vaultId, tab, get().filters, 0));
      set({ items: res.items, total: res.total, offset: 0, loading: false });
    } catch (err: unknown) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  // ── setFilters / clearFilters ─────────────────────────────────────────────
  setFilters: async (patch, vaultId) => {
    const nextFilters = { ...get().filters, ...patch };
    set({
      filters: nextFilters,
      loading: true,
      error: null,
      selectedIds: new Set<string>(),
    });
    try {
      const res = await fetchReviewQueue(queueOptions(vaultId, get().activeTab, nextFilters, 0));
      set({ items: res.items, total: res.total, offset: 0, loading: false });
    } catch (err: unknown) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  clearFilters: async (vaultId) => {
    await get().setFilters({ ...EMPTY_REVIEW_FILTERS }, vaultId);
  },

  // ── create ─────────────────────────────────────────────────────────────────
  create: async (itemId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [itemId]: "create" },
      actionError: { ...s.actionError, [itemId]: null },
      createGenerationError: { ...s.createGenerationError, [itemId]: null },
    }));
    try {
      await createReviewItem(itemId);
      // 201: page written — remove from pending list
      set((s) => ({
        items: s.items.filter((i) => i.id !== itemId),
        total: Math.max(0, s.total - 1),
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        selectedIds: (() => {
          const next = new Set(s.selectedIds);
          next.delete(itemId);
          return next;
        })(),
      }));
    } catch (err: unknown) {
      const is502 = err instanceof ApiError && err.status === 502;

      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        // 502: generation failed, item stays pending — show retry-or-skip hint
        createGenerationError: is502
          ? { ...s.createGenerationError, [itemId]: (err as Error).message }
          : s.createGenerationError,
        // 409 / other: not pending / no provider — generic per-item error
        actionError: !is502
          ? { ...s.actionError, [itemId]: (err as Error).message }
          : s.actionError,
      }));
    }
  },

  // ── skip ──────────────────────────────────────────────────────────────────
  skip: async (itemId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [itemId]: "skip" },
      actionError: { ...s.actionError, [itemId]: null },
      createGenerationError: { ...s.createGenerationError, [itemId]: null },
    }));
    try {
      await skipReviewItem(itemId);
      // Optimistic removal from pending list
      set((s) => ({
        items: s.items.filter((i) => i.id !== itemId),
        total: Math.max(0, s.total - 1),
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        selectedIds: (() => {
          const next = new Set(s.selectedIds);
          next.delete(itemId);
          return next;
        })(),
      }));
    } catch (err: unknown) {
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        actionError: { ...s.actionError, [itemId]: (err as Error).message },
      }));
    }
  },

  // ── dismiss ────────────────────────────────────────────────────────────────
  dismiss: async (itemId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [itemId]: "dismiss" },
      actionError: { ...s.actionError, [itemId]: null },
      createGenerationError: { ...s.createGenerationError, [itemId]: null },
    }));
    try {
      await dismissReviewItem(itemId);
      // Optimistic removal from pending list
      set((s) => ({
        items: s.items.filter((i) => i.id !== itemId),
        total: Math.max(0, s.total - 1),
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        selectedIds: (() => {
          const next = new Set(s.selectedIds);
          next.delete(itemId);
          return next;
        })(),
      }));
    } catch (err: unknown) {
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        actionError: { ...s.actionError, [itemId]: (err as Error).message },
      }));
    }
  },

  // ── approve ────────────────────────────────────────────────────────────────
  approve: async (itemId, vaultId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [itemId]: "approve" },
      actionError: { ...s.actionError, [itemId]: null },
      createGenerationError: { ...s.createGenerationError, [itemId]: null },
    }));
    try {
      await resolveReviewItem(itemId, vaultId);
      // Optimistic removal from pending list (resolved → auto_resolved terminal)
      set((s) => ({
        items: s.items.filter((i) => i.id !== itemId),
        total: Math.max(0, s.total - 1),
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        selectedIds: (() => {
          const next = new Set(s.selectedIds);
          next.delete(itemId);
          return next;
        })(),
      }));
    } catch (err: unknown) {
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        actionError: { ...s.actionError, [itemId]: (err as Error).message },
      }));
    }
  },

  // ── deepResearch ──────────────────────────────────────────────────────────
  deepResearch: async (itemId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [itemId]: "deep-research" },
      actionError: { ...s.actionError, [itemId]: null },
      deepResearchError: null,
    }));
    try {
      const result = await deepResearchReviewItem(itemId);
      // Optimistic removal from pending list + surface run id
      set((s) => ({
        items: s.items.filter((i) => i.id !== itemId),
        total: Math.max(0, s.total - 1),
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        lastDeepResearch: { itemId, runId: result.run_id },
        selectedIds: (() => {
          const next = new Set(s.selectedIds);
          next.delete(itemId);
          return next;
        })(),
      }));
      return result;
    } catch (err: unknown) {
      // 503 = SEARXNG not configured — surface distinctly (I9 guard)
      const is503 = err instanceof ApiError && err.status === 503;
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        actionError: is503 ? s.actionError : { ...s.actionError, [itemId]: (err as Error).message },
        deepResearchError: is503 ? (err as Error).message : s.deepResearchError,
      }));
      return null;
    }
  },

  // ── sweep ──────────────────────────────────────────────────────────────────
  sweep: async (vaultId) => {
    set({ loading: true, error: null });
    try {
      const result = await sweepReviewQueue(vaultId);
      // Refresh queue after sweep so resolved items disappear
      const res = await fetchReviewQueue(queueOptions(vaultId, get().activeTab, get().filters, 0));
      set({
        items: res.items,
        total: res.total,
        offset: 0,
        loading: false,
        lastSweepResult: result,
        selectedIds: new Set<string>(),
      });
    } catch (err: unknown) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  // ── bulkAction ─────────────────────────────────────────────────────────────
  bulkAction: async (vaultId, action) => {
    const { selectedIds, activeTab, filters } = get();
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    set({ loading: true, bulkError: null });
    try {
      const result = await bulkReview({ vault_id: vaultId, action, ids });
      // Refresh queue after bulk action
      const res = await fetchReviewQueue(queueOptions(vaultId, activeTab, filters, 0));
      set({
        items: res.items,
        total: res.total,
        offset: 0,
        loading: false,
        lastBulkResult: result,
        selectedIds: new Set<string>(),
      });
    } catch (err: unknown) {
      set({ bulkError: (err as Error).message, loading: false });
    }
  },

  // ── clearResolvedRows ──────────────────────────────────────────────────────
  clearResolvedRows: async (vaultId) => {
    set({ loading: true, bulkError: null });
    try {
      const result = await clearResolved(vaultId);
      // Refresh queue
      const res = await fetchReviewQueue(queueOptions(vaultId, get().activeTab, get().filters, 0));
      set({
        items: res.items,
        total: res.total,
        offset: 0,
        loading: false,
        lastClearResult: result,
        selectedIds: new Set<string>(),
      });
    } catch (err: unknown) {
      set({ bulkError: (err as Error).message, loading: false });
    }
  },

  // ── Selection helpers (I3 / I4) ───────────────────────────────────────────

  toggleSelected: (id) => {
    set((s) => {
      const next = new Set(s.selectedIds);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return { selectedIds: next };
    });
  },

  selectAllPending: () => {
    set((s) => {
      // O(loaded): only select currently-loaded pending items (ADR-0044 §10 Do-NOT #8)
      const pendingIds = s.items.filter((item) => item.status === "pending").map((item) => item.id);
      return { selectedIds: new Set(pendingIds) };
    });
  },

  clearSelection: () => set({ selectedIds: new Set<string>() }),

  clearDeepResearchError: () => set({ deepResearchError: null }),
  clearLastDeepResearch: () => set({ lastDeepResearch: null }),
  clearLastSweepResult: () => set({ lastSweepResult: null }),
  clearCreateGenerationError: (itemId) =>
    set((s) => ({
      createGenerationError: { ...s.createGenerationError, [itemId]: null },
    })),
  clearLastBulkResult: () => set({ lastBulkResult: null }),
  clearLastClearResult: () => set({ lastClearResult: null }),
  clearBulkError: () => set({ bulkError: null }),

  // FE-UIUX-3
  resetForVault: () =>
    set({
      items: [],
      total: 0,
      offset: 0,
      loading: false,
      error: null,
      activeTab: "pending",
      filters: { ...EMPTY_REVIEW_FILTERS },
      selectedIds: new Set<string>(),
      actionInFlight: {},
      actionError: {},
      createGenerationError: {},
      lastDeepResearch: null,
      deepResearchError: null,
      lastSweepResult: null,
      lastBulkResult: null,
      lastClearResult: null,
      bulkError: null,
    }),
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────

export function selectReviewItems(s: ReviewStore): ReviewItem[] {
  return s.items;
}
export function selectReviewTotal(s: ReviewStore): number {
  return s.total;
}
export function selectReviewLoading(s: ReviewStore): boolean {
  return s.loading;
}
export function selectReviewError(s: ReviewStore): string | null {
  return s.error;
}
export function selectActiveTab(s: ReviewStore): ReviewQueueStatus {
  return s.activeTab;
}
export function selectReviewFilters(s: ReviewStore): ReviewFilters {
  return s.filters;
}
export function selectSelectedIds(s: ReviewStore): Set<string> {
  return s.selectedIds;
}
/**
 * Per-item membership selector (I3): the row subscribes only to its own id's membership.
 * Usage: useReviewStore(selectIsSelected(item.id))
 */
export function selectIsSelected(id: string): (s: ReviewStore) => boolean {
  return (s) => s.selectedIds.has(id);
}
export function selectReviewActionInFlight(
  s: ReviewStore,
): Record<string, "create" | "approve" | "skip" | "dismiss" | "deep-research" | null> {
  return s.actionInFlight;
}
export function selectReviewActionError(s: ReviewStore): Record<string, string | null> {
  return s.actionError;
}
export function selectCreateGenerationError(s: ReviewStore): Record<string, string | null> {
  return s.createGenerationError;
}
export function selectLastDeepResearch(s: ReviewStore): { itemId: string; runId: string } | null {
  return s.lastDeepResearch;
}
export function selectDeepResearchError(s: ReviewStore): string | null {
  return s.deepResearchError;
}
export function selectLastSweepResult(s: ReviewStore): ReviewSweepResponse | null {
  return s.lastSweepResult;
}
export function selectLastBulkResult(s: ReviewStore): ReviewBulkResponse | null {
  return s.lastBulkResult;
}
export function selectLastClearResult(s: ReviewStore): ReviewClearResolvedResponse | null {
  return s.lastClearResult;
}
export function selectBulkError(s: ReviewStore): string | null {
  return s.bulkError;
}
export function selectFetchFreshReview(s: ReviewStore): ReviewActions["fetchFresh"] {
  return s.fetchFresh;
}
export function selectFetchMoreReview(s: ReviewStore): ReviewActions["fetchMore"] {
  return s.fetchMore;
}
export function selectSetActiveTab(s: ReviewStore): ReviewActions["setActiveTab"] {
  return s.setActiveTab;
}
export function selectSetReviewFilters(s: ReviewStore): ReviewActions["setFilters"] {
  return s.setFilters;
}
export function selectClearReviewFilters(s: ReviewStore): ReviewActions["clearFilters"] {
  return s.clearFilters;
}
export function selectCreate(s: ReviewStore): ReviewActions["create"] {
  return s.create;
}
export function selectSkip(s: ReviewStore): ReviewActions["skip"] {
  return s.skip;
}
export function selectDismiss(s: ReviewStore): ReviewActions["dismiss"] {
  return s.dismiss;
}
export function selectApprove(s: ReviewStore): ReviewActions["approve"] {
  return s.approve;
}
export function selectDeepResearch(s: ReviewStore): ReviewActions["deepResearch"] {
  return s.deepResearch;
}
export function selectSweep(s: ReviewStore): ReviewActions["sweep"] {
  return s.sweep;
}
export function selectBulkAction(s: ReviewStore): ReviewActions["bulkAction"] {
  return s.bulkAction;
}
export function selectClearResolvedRows(s: ReviewStore): ReviewActions["clearResolvedRows"] {
  return s.clearResolvedRows;
}
export function selectToggleSelected(s: ReviewStore): ReviewActions["toggleSelected"] {
  return s.toggleSelected;
}
export function selectSelectAllPending(s: ReviewStore): ReviewActions["selectAllPending"] {
  return s.selectAllPending;
}
export function selectClearSelection(s: ReviewStore): ReviewActions["clearSelection"] {
  return s.clearSelection;
}
export function selectClearDeepResearchError(
  s: ReviewStore,
): ReviewActions["clearDeepResearchError"] {
  return s.clearDeepResearchError;
}
export function selectClearLastDeepResearch(
  s: ReviewStore,
): ReviewActions["clearLastDeepResearch"] {
  return s.clearLastDeepResearch;
}
export function selectClearLastSweepResult(s: ReviewStore): ReviewActions["clearLastSweepResult"] {
  return s.clearLastSweepResult;
}
export function selectClearCreateGenerationError(
  s: ReviewStore,
): ReviewActions["clearCreateGenerationError"] {
  return s.clearCreateGenerationError;
}
export function selectClearLastBulkResult(s: ReviewStore): ReviewActions["clearLastBulkResult"] {
  return s.clearLastBulkResult;
}
export function selectClearLastClearResult(s: ReviewStore): ReviewActions["clearLastClearResult"] {
  return s.clearLastClearResult;
}
export function selectClearBulkError(s: ReviewStore): ReviewActions["clearBulkError"] {
  return s.clearBulkError;
}
export function selectReviewResetForVault(s: ReviewStore): ReviewActions["resetForVault"] {
  return s.resetForVault;
}

/** Hook: items array — shallow equality (I3). */
export function useReviewItems(): ReviewItem[] {
  return useReviewStore(useShallow(selectReviewItems));
}

/** Hook: per-item actionInFlight map — shallow equality (I3). */
export function useReviewActionInFlight(): Record<
  string,
  "create" | "approve" | "skip" | "dismiss" | "deep-research" | null
> {
  return useReviewStore(useShallow(selectReviewActionInFlight));
}

/** Hook: per-item actionError map — shallow equality (I3). */
export function useReviewActionError(): Record<string, string | null> {
  return useReviewStore(useShallow(selectReviewActionError));
}

/** Hook: per-item createGenerationError map — shallow equality (I3). */
export function useCreateGenerationError(): Record<string, string | null> {
  return useReviewStore(useShallow(selectCreateGenerationError));
}

/** Hook: selectedIds Set — shallow equality (I3). */
export function useSelectedIds(): Set<string> {
  return useReviewStore(useShallow(selectSelectedIds));
}
