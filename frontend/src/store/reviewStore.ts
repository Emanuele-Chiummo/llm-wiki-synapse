/**
 * reviewStore.ts — Zustand store for F9 HITL review queue (ADR-0025 §3.6).
 *
 * INVARIANT I3: separate from graphStore — review actions never cause the graph
 *               to re-render. Zustand selectors + shallow equality on collections.
 * INVARIANT I4: this store accumulates pages in `items[]`; the UI virtualises
 *               the list when > 50 rows (AC-F9-5).
 * INVARIANT I7: GET /review/queue limit is capped (default 50, max 200 server-side).
 *
 * After approve / skip / deep-research the actioned item is REMOVED from the
 * pending list (optimistic update by filtering out the item on success).
 *
 * 503 from deep-research (SEARXNG_URL not set) is surfaced in `deepResearchError`.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { ReviewItem, ReviewDeepResearchResponse } from "../api/types";
import {
  fetchReviewQueue,
  approveReviewItem,
  skipReviewItem,
  deepResearchReviewItem,
} from "../api/reviewClient";
import { ApiError } from "../api/graphClient";

// ─── Constants ────────────────────────────────────────────────────────────────

const PAGE_LIMIT = 50;

// ─── State / Actions ─────────────────────────────────────────────────────────

interface ReviewState {
  /** Pending review items (created_at ASC from backend). */
  items: ReviewItem[];
  total: number;
  offset: number;
  loading: boolean;
  error: string | null;

  /** In-flight action state per item id. */
  actionInFlight: Record<string, "approve" | "skip" | "deep-research" | null>;

  /** Per-item action error (non-503). */
  actionError: Record<string, string | null>;

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
}

interface ReviewActions {
  /** Fetch the queue from offset 0 (replaces the list). */
  fetchFresh: (vaultId: string, signal?: AbortSignal) => Promise<void>;
  /** Fetch the next page (offset += PAGE_LIMIT), appending. */
  fetchMore: (vaultId: string) => Promise<void>;

  /**
   * Approve an item. On success, removes it from the pending list (optimistic).
   * Does NOT re-trigger ingest (AC-F9-6, I1).
   */
  approve: (itemId: string) => Promise<void>;

  /**
   * Skip an item. On success, removes it from the pending list (optimistic).
   */
  skip: (itemId: string) => Promise<void>;

  /**
   * Trigger deep research for an item. On success, removes from pending list and
   * stores lastDeepResearch. On 503 (SEARXNG not set), sets deepResearchError.
   */
  deepResearch: (itemId: string) => Promise<ReviewDeepResearchResponse | null>;

  clearDeepResearchError: () => void;
  clearLastDeepResearch: () => void;
}

export type ReviewStore = ReviewState & ReviewActions;

// ─── Store ────────────────────────────────────────────────────────────────────

export const useReviewStore = create<ReviewStore>((set, get) => ({
  items: [],
  total: 0,
  offset: 0,
  loading: false,
  error: null,
  actionInFlight: {},
  actionError: {},
  lastDeepResearch: null,
  deepResearchError: null,

  // ── fetchFresh ─────────────────────────────────────────────────────────────
  fetchFresh: async (vaultId, signal) => {
    set({ loading: true, error: null });
    try {
      const res = await fetchReviewQueue(
        { vaultId, limit: PAGE_LIMIT, offset: 0 },
        signal,
      );
      set({ items: res.items, total: res.total, offset: 0, loading: false });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message, loading: false });
    }
  },

  // ── fetchMore ──────────────────────────────────────────────────────────────
  fetchMore: async (vaultId) => {
    const { offset, total, items, loading } = get();
    if (loading || items.length >= total) return;
    const nextOffset = offset + PAGE_LIMIT;
    set({ loading: true });
    try {
      const res = await fetchReviewQueue({ vaultId, limit: PAGE_LIMIT, offset: nextOffset });
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

  // ── approve ────────────────────────────────────────────────────────────────
  approve: async (itemId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [itemId]: "approve" },
      actionError: { ...s.actionError, [itemId]: null },
    }));
    try {
      await approveReviewItem(itemId);
      // Optimistic removal from pending list
      set((s) => ({
        items: s.items.filter((i) => i.id !== itemId),
        total: Math.max(0, s.total - 1),
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
      }));
    } catch (err: unknown) {
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        actionError: { ...s.actionError, [itemId]: (err as Error).message },
      }));
    }
  },

  // ── skip ──────────────────────────────────────────────────────────────────
  skip: async (itemId) => {
    set((s) => ({
      actionInFlight: { ...s.actionInFlight, [itemId]: "skip" },
      actionError: { ...s.actionError, [itemId]: null },
    }));
    try {
      await skipReviewItem(itemId);
      // Optimistic removal from pending list
      set((s) => ({
        items: s.items.filter((i) => i.id !== itemId),
        total: Math.max(0, s.total - 1),
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
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
      }));
      return result;
    } catch (err: unknown) {
      // 503 = SEARXNG not configured — surface distinctly (I9 guard)
      const is503 = err instanceof ApiError && err.status === 503;
      set((s) => ({
        actionInFlight: { ...s.actionInFlight, [itemId]: null },
        actionError: is503
          ? s.actionError
          : { ...s.actionError, [itemId]: (err as Error).message },
        deepResearchError: is503 ? (err as Error).message : s.deepResearchError,
      }));
      return null;
    }
  },

  clearDeepResearchError: () => set({ deepResearchError: null }),
  clearLastDeepResearch: () => set({ lastDeepResearch: null }),
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
export function selectReviewActionInFlight(
  s: ReviewStore,
): Record<string, "approve" | "skip" | "deep-research" | null> {
  return s.actionInFlight;
}
export function selectReviewActionError(
  s: ReviewStore,
): Record<string, string | null> {
  return s.actionError;
}
export function selectLastDeepResearch(
  s: ReviewStore,
): { itemId: string; runId: string } | null {
  return s.lastDeepResearch;
}
export function selectDeepResearchError(s: ReviewStore): string | null {
  return s.deepResearchError;
}
export function selectFetchFreshReview(
  s: ReviewStore,
): ReviewActions["fetchFresh"] {
  return s.fetchFresh;
}
export function selectFetchMoreReview(
  s: ReviewStore,
): ReviewActions["fetchMore"] {
  return s.fetchMore;
}
export function selectApprove(s: ReviewStore): ReviewActions["approve"] {
  return s.approve;
}
export function selectSkip(s: ReviewStore): ReviewActions["skip"] {
  return s.skip;
}
export function selectDeepResearch(
  s: ReviewStore,
): ReviewActions["deepResearch"] {
  return s.deepResearch;
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

/** Hook: items array — shallow equality (I3). */
export function useReviewItems(): ReviewItem[] {
  return useReviewStore(useShallow(selectReviewItems));
}

/** Hook: per-item actionInFlight map — shallow equality (I3). */
export function useReviewActionInFlight(): Record<
  string,
  "approve" | "skip" | "deep-research" | null
> {
  return useReviewStore(useShallow(selectReviewActionInFlight));
}

/** Hook: per-item actionError map — shallow equality (I3). */
export function useReviewActionError(): Record<string, string | null> {
  return useReviewStore(useShallow(selectReviewActionError));
}
