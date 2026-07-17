/**
 * ReviewItemList.tsx — virtualised list of F9 Review Queue proposal cards
 * (I4). Extracted from ReviewQueueView.tsx (FE-ARCH-1/FE-TEST-10 —
 * mechanical move, no behavior change).
 */

import { useRef, useCallback } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useReviewStore,
  selectReviewItems,
  selectReviewTotal,
  selectReviewLoading,
  selectIsSelected,
  selectReviewActionInFlight,
  selectReviewActionError,
  selectCreateGenerationError,
  selectFetchMoreReview,
  selectCreate,
  selectSkip,
  selectDismiss,
  selectApprove,
  selectDeepResearch,
  selectClearCreateGenerationError,
  selectToggleSelected,
} from "../../store/reviewStore";
import { EmptyState } from "../common/EmptyState";
import { Skeleton } from "../ui/Skeleton";
import type { ReviewItem } from "../../api/types";
import type { Translate } from "./ReviewBadges";
import { ReviewRow } from "./ReviewRow";

/**
 * Base estimated row height; the virtualizer uses measureElement for actual
 * variable heights (ADR-0044 §7 — referenced_pages + search_queries grow cards).
 * Raised for the llm_wiki card layout (rounded bordered card + multi-line wrapping
 * description + inter-card gap) which is taller than the old dense flat row.
 */
const ROW_ESTIMATE = 200;

// ─── Item list (virtualised — I4) ─────────────────────────────────────────────

interface ReviewItemListProps {
  vaultId: string;
  onOpenSources: () => void;
  onOpenPage: (pageId: string) => void;
  onOpenCreatedPage: (pageId: string) => void;
}

export function ReviewItemList({
  vaultId,
  onOpenSources,
  onOpenPage,
  onOpenCreatedPage,
}: ReviewItemListProps) {
  const { t, i18n } = useTranslation();
  const items = useReviewStore(useShallow(selectReviewItems));
  const total = useReviewStore(selectReviewTotal);
  const loading = useReviewStore(selectReviewLoading);
  const actionInFlight = useReviewStore(useShallow(selectReviewActionInFlight));
  const actionError = useReviewStore(useShallow(selectReviewActionError));
  const generationError = useReviewStore(useShallow(selectCreateGenerationError));
  const fetchMore = useReviewStore(selectFetchMoreReview);
  const create = useReviewStore(selectCreate);
  const skip = useReviewStore(selectSkip);
  const dismiss = useReviewStore(selectDismiss);
  const approve = useReviewStore(selectApprove);
  const deepResearch = useReviewStore(selectDeepResearch);
  const clearGenerationError = useReviewStore(selectClearCreateGenerationError);
  const toggleSelected = useReviewStore(selectToggleSelected);

  const scrollRef = useRef<HTMLDivElement>(null);

  // Always virtualise — the virtualizer is efficient regardless of list size (I4).
  // Use measureElement for variable heights (referenced_pages / search_queries — ADR-0044 §7).
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_ESTIMATE,
    overscan: 5,
    measureElement: (el) => el?.getBoundingClientRect().height ?? ROW_ESTIMATE,
  });

  const handleCreate = useCallback(
    (id: string) => {
      void create(id);
    },
    [create],
  );
  const handleApprove = useCallback(
    (id: string) => {
      void approve(id, vaultId);
    },
    [approve, vaultId],
  );
  const handleSkip = useCallback(
    (id: string) => {
      void skip(id);
    },
    [skip],
  );
  const handleDismiss = useCallback(
    (id: string) => {
      void dismiss(id);
    },
    [dismiss],
  );
  const handleDeepResearch = useCallback(
    (id: string) => {
      void deepResearch(id);
    },
    [deepResearch],
  );
  const handleDismissGenerationError = useCallback(
    (id: string) => {
      clearGenerationError(id);
    },
    [clearGenerationError],
  );
  const handleToggleSelect = useCallback(
    (id: string) => {
      toggleSelected(id);
    },
    [toggleSelected],
  );

  if (items.length === 0 && loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 16 }}>
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} height={48} radius={8} />
        ))}
      </div>
    );
  }

  if (items.length === 0 && !loading) {
    return (
      <div style={{ display: "flex", height: "100%", padding: 16 }}>
        <EmptyState
          testId="review-empty"
          eyebrow={t("nav.review")}
          title={t("review.empty")}
          body={t("review.emptyBody")}
          actions={[{ label: t("review.openSources"), onClick: onOpenSources, variant: "primary" }]}
        />
      </div>
    );
  }

  const totalHeight = virtualizer.getTotalSize();
  const virtualItems = virtualizer.getVirtualItems();
  const hasMore = items.length < total;

  return (
    <div
      ref={scrollRef}
      data-testid="review-item-list"
      style={{ overflow: "auto", height: "100%", flex: 1, minHeight: 0 }}
    >
      <div style={{ height: totalHeight + (hasMore ? 48 : 0), position: "relative" }}>
        {virtualItems.map((vRow) => {
          const item = items[vRow.index];
          if (!item) return null;
          return (
            <RowWrapper
              key={item.id}
              item={item}
              vRow={vRow}
              virtualizer={virtualizer}
              inFlight={actionInFlight[item.id]}
              actionError={actionError[item.id]}
              generationError={generationError[item.id]}
              onCreate={handleCreate}
              onApprove={handleApprove}
              onSkip={handleSkip}
              onDismiss={handleDismiss}
              onDeepResearch={handleDeepResearch}
              onDismissGenerationError={handleDismissGenerationError}
              onToggleSelect={handleToggleSelect}
              onOpenPage={onOpenPage}
              onOpenCreatedPage={onOpenCreatedPage}
              t={t}
              lang={i18n.language}
            />
          );
        })}

        {hasMore && (
          <button
            onClick={() => void fetchMore(vaultId)}
            disabled={loading}
            data-testid="review-load-more"
            style={{
              position: "absolute",
              top: totalHeight,
              left: 0,
              right: 0,
              height: 40,
              margin: "4px 16px",
              border: "1px solid var(--syn-border)",
              borderRadius: "var(--syn-radius-sm)",
              background: "var(--syn-surface)",
              color: "var(--syn-text-muted)",
              fontSize: 12,
              cursor: loading ? "wait" : "pointer",
            }}
          >
            {loading ? t("common.loading") : t("review.loadMore")}
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * RowWrapper reads its own selection state via selectIsSelected(item.id) —
 * a row only re-renders when ITS membership changes, not the full Set (I3).
 */
interface RowWrapperProps {
  item: ReviewItem;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vRow: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  virtualizer: any;
  inFlight: "create" | "approve" | "skip" | "dismiss" | "deep-research" | null | undefined;
  actionError: string | null | undefined;
  generationError: string | null | undefined;
  onCreate: (id: string) => void;
  onApprove: (id: string) => void;
  onSkip: (id: string) => void;
  onDismiss: (id: string) => void;
  onDeepResearch: (id: string) => void;
  onDismissGenerationError: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onOpenPage: (pageId: string) => void;
  onOpenCreatedPage: (pageId: string) => void;
  t: Translate;
  lang: string;
}

function RowWrapper({
  item,
  vRow,
  virtualizer,
  inFlight,
  actionError,
  generationError,
  onCreate,
  onApprove,
  onSkip,
  onDismiss,
  onDeepResearch,
  onDismissGenerationError,
  onToggleSelect,
  onOpenPage,
  onOpenCreatedPage,
  t,
  lang,
}: RowWrapperProps) {
  // Per-row selection read: only this row re-renders on its own id toggle (I3)
  const isSelected = useReviewStore(selectIsSelected(item.id));

  return (
    <ReviewRow
      item={item}
      style={{ position: "absolute", top: vRow.start, width: "100%" }}
      // TanStack Virtual maps a measured node to its row via the data-index
      // attribute; without it measureElement is a no-op and every row keeps the
      // fixed ROW_ESTIMATE (leaving big gaps under the short resolved/dismissed
      // cards). Stamp it before measuring so variable heights actually apply.
      measureRef={(el) => {
        if (el) el.setAttribute("data-index", String(vRow.index));
        virtualizer.measureElement(el);
      }}
      inFlight={inFlight}
      actionError={actionError}
      generationError={generationError}
      isSelected={isSelected}
      onCreate={onCreate}
      onApprove={onApprove}
      onSkip={onSkip}
      onDismiss={onDismiss}
      onDeepResearch={onDeepResearch}
      onDismissGenerationError={onDismissGenerationError}
      onToggleSelect={onToggleSelect}
      onOpenPage={onOpenPage}
      onOpenCreatedPage={onOpenCreatedPage}
      t={t}
      lang={lang}
    />
  );
}
