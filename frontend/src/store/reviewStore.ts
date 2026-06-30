/**
 * reviewStore.ts — Zustand store for F9 HITL review queue (ADR-0034 §7.1).
 *
 * INVARIANT I3: separate from graphStore — review actions never cause the graph
 *               to re-render. Zustand selectors + shallow equality on collections.
 * INVARIANT I4: this store accumulates pages in `items[]`; the UI virtualises
 *               the list when > 50 rows (ADR-0034 §10, I4).
 * INVARIANT I7: GET /review/queue limit is capped (default 50, max 200 server-side).
 *
 * Action semantics (ADR-0034 §7):
 *   - Create  → POST /review/queue/{id}/create (preferred alias; 201 on success).
 *               409 = not pending / no provider (item stays pending).
 *               502 = generation failed (item stays pending; show retry-or-skip message).
 *               On 201: item removed from pending list (optimistic).
 *   - Skip    → POST /review/queue/{id}/skip (200). Item removed optimistically.
 *   - Deep Research → POST /review/queue/{id}/deep-research (202). Item removed optimistically.
 *                     503 = SEARXNG_URL not set; surfaced in deepResearchError.
 *
 * "Create" replaces the old "Approve" (which was a no-op in ADR-0025).
 * pre_generated_query is removed — content now comes from proposed_title + rationale.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { ReviewItem, ReviewDeepResearchResponse, ReviewSweepResponse } from "../api/types";
import {
  fetchReviewQueue,
  createReviewItem,
  skipReviewItem,
  deepResearchReviewItem,
  sweepReviewQueue,
} from "../api/reviewClient";
import { ApiError } from "../api/graphClient";

// ─── Constants ────────────────────────────────────────────────────────────────

const PAGE_LIMIT = 50;

// ─── State / Actions ─────────────────────────────────────────────────────────

interface ReviewState {
  /** Pending review proposals (created_at ASC from backend). */
  items: ReviewItem[];
  total: number;
  offset: number;
  loading: boolean;
  error: string | null;

  /**
   * In-flight action state per item id.
   * "create" replaces "approve" (ADR-0034 §7).
   */
  actionInFlight: Record<string, "create" | "skip" | "deep-research" | null>;

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
}

interface ReviewActions {
  /** Fetch the queue from offset 0 (replaces the list). */
  fetchFresh: (vaultId: string, signal?: AbortSignal) => Promise<void>;
  /** Fetch the next page (offset += PAGE_LIMIT), appending. */
  fetchMore: (vaultId: string) => Promise<void>;

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

  clearDeepResearchError: () => void;
  clearLastDeepResearch: () => void;
  clearLastSweepResult: () => void;
  clearCreateGenerationError: (itemId: string) => void;
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
  createGenerationError: {},
  lastDeepResearch: null,
  deepResearchError: null,
  lastSweepResult: null,

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
        actionError:
          !is502
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

  // ── sweep ──────────────────────────────────────────────────────────────────
  sweep: async (vaultId) => {
    set({ loading: true, error: null });
    try {
      const result = await sweepReviewQueue(vaultId);
      // Refresh queue after sweep so resolved items disappear
      const res = await fetchReviewQueue({ vaultId, limit: PAGE_LIMIT, offset: 0 });
      set({
        items: res.items,
        total: res.total,
        offset: 0,
        loading: false,
        lastSweepResult: result,
      });
    } catch (err: unknown) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  clearDeepResearchError: () => set({ deepResearchError: null }),
  clearLastDeepResearch: () => set({ lastDeepResearch: null }),
  clearLastSweepResult: () => set({ lastSweepResult: null }),
  clearCreateGenerationError: (itemId) =>
    set((s) => ({
      createGenerationError: { ...s.createGenerationError, [itemId]: null },
    })),
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
): Record<string, "create" | "skip" | "deep-research" | null> {
  return s.actionInFlight;
}
export function selectReviewActionError(
  s: ReviewStore,
): Record<string, string | null> {
  return s.actionError;
}
export function selectCreateGenerationError(
  s: ReviewStore,
): Record<string, string | null> {
  return s.createGenerationError;
}
export function selectLastDeepResearch(
  s: ReviewStore,
): { itemId: string; runId: string } | null {
  return s.lastDeepResearch;
}
export function selectDeepResearchError(s: ReviewStore): string | null {
  return s.deepResearchError;
}
export function selectLastSweepResult(
  s: ReviewStore,
): ReviewSweepResponse | null {
  return s.lastSweepResult;
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
export function selectCreate(s: ReviewStore): ReviewActions["create"] {
  return s.create;
}
export function selectSkip(s: ReviewStore): ReviewActions["skip"] {
  return s.skip;
}
export function selectDeepResearch(
  s: ReviewStore,
): ReviewActions["deepResearch"] {
  return s.deepResearch;
}
export function selectSweep(s: ReviewStore): ReviewActions["sweep"] {
  return s.sweep;
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
export function selectClearLastSweepResult(
  s: ReviewStore,
): ReviewActions["clearLastSweepResult"] {
  return s.clearLastSweepResult;
}
export function selectClearCreateGenerationError(
  s: ReviewStore,
): ReviewActions["clearCreateGenerationError"] {
  return s.clearCreateGenerationError;
}

/** Hook: items array — shallow equality (I3). */
export function useReviewItems(): ReviewItem[] {
  return useReviewStore(useShallow(selectReviewItems));
}

/** Hook: per-item actionInFlight map — shallow equality (I3). */
export function useReviewActionInFlight(): Record<
  string,
  "create" | "skip" | "deep-research" | null
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
