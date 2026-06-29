/**
 * ReviewQueueView.tsx — F9 HITL Review Queue section (ADR-0025 §3.6, AC-F9-5).
 *
 * Layout:
 *   - Header with title + total count badge
 *   - 503 SEARXNG banner when deep-research is unavailable (I9)
 *   - Last deep-research success banner with run_id + jump link
 *   - Pending items list (TanStack Virtual when > 50 rows — I4)
 *   - Per-item: page_title, item_type badge, pre_generated_query, created_at
 *   - Per-item actions: Approve · Skip · Deep-Research
 *   - Empty state and error state
 *
 * INVARIANT I3: Zustand selectors + shallow equality. No store subscriptions
 *   that trigger on unrelated state.
 * INVARIANT I4: list virtualised with TanStack Virtual; virtualization kicks in
 *   always (the virtualiser manages this efficiently regardless of list size).
 * INVARIANT I6: Deep-Research action delegates to POST /review/queue/{id}/deep-research
 *   which in turn delegates to POST /research/start — no hardcoded provider (I6).
 * INVARIANT I7: approve / skip do NOT re-trigger ingest (AC-F9-6, I1).
 *
 * AC-F9-5: separate from the M4 Ingest Activity View (AC-F9-7).
 */

import { useEffect, useRef, useCallback, type CSSProperties } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useReviewStore,
  selectReviewItems,
  selectReviewTotal,
  selectReviewLoading,
  selectReviewError,
  selectReviewActionInFlight,
  selectReviewActionError,
  selectLastDeepResearch,
  selectDeepResearchError,
  selectFetchFreshReview,
  selectFetchMoreReview,
  selectApprove,
  selectSkip,
  selectDeepResearch,
  selectClearDeepResearchError,
  selectClearLastDeepResearch,
} from "../../store/reviewStore";
import { useGraphStore, selectVaultId, selectSetActiveSection } from "../../store/graphStore";
import type { ReviewItem } from "../../api/types";

// ─── Constants ────────────────────────────────────────────────────────────────

const ROW_HEIGHT = 108;

// ─── Item type badge ──────────────────────────────────────────────────────────

const ITEM_TYPE_COLORS: Record<string, { color: string; bg: string }> = {
  new_page:                { color: "#3fb950", bg: "#3fb95022" },
  update_page:             { color: "#d29922", bg: "#d2992222" },
  deep_research_candidate: { color: "#58a6ff", bg: "#58a6ff22" },
};

interface ItemTypeBadgeProps {
  itemType: string;
  t: (key: string) => string;
}

function ItemTypeBadge({ itemType, t }: ItemTypeBadgeProps) {
  const { color, bg } = ITEM_TYPE_COLORS[itemType] ?? { color: "#8b949e", bg: "#8b949e22" };
  const label = t(`review.itemType.${itemType}`);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontSize: 10,
        fontWeight: 600,
        color,
        background: bg,
        border: `1px solid ${color}4d`,
        borderRadius: 8,
        padding: "1px 6px",
        whiteSpace: "nowrap",
        userSelect: "none",
      }}
    >
      {label}
    </span>
  );
}

// ─── Action button ────────────────────────────────────────────────────────────

interface ActionButtonProps {
  label: string;
  onClick: () => void;
  disabled: boolean;
  variant: "approve" | "skip" | "deep-research";
}

function ActionButton({ label, onClick, disabled, variant }: ActionButtonProps) {
  const COLORS: Record<string, { border: string; color: string; hoverBg: string }> = {
    approve:        { border: "#3fb950", color: "#3fb950", hoverBg: "#3fb95015" },
    skip:           { border: "#484f58", color: "#8b949e", hoverBg: "#21262d" },
    "deep-research": { border: "#58a6ff", color: "#58a6ff", hoverBg: "#58a6ff15" },
  };
  const { border, color } = COLORS[variant] ?? (COLORS["skip"] as { border: string; color: string; hoverBg: string });
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      data-testid={`review-action-${variant}`}
      style={{
        padding: "3px 10px",
        fontSize: 11,
        fontWeight: 600,
        border: `1px solid ${disabled ? "#21262d" : border}`,
        borderRadius: 5,
        background: "transparent",
        color: disabled ? "#484f58" : color,
        cursor: disabled ? "not-allowed" : "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </button>
  );
}

// ─── Review item row (virtualised) ────────────────────────────────────────────

interface ReviewRowProps {
  item: ReviewItem;
  style: CSSProperties;
  inFlight: "approve" | "skip" | "deep-research" | null | undefined;
  actionError: string | null | undefined;
  onApprove: (id: string) => void;
  onSkip: (id: string) => void;
  onDeepResearch: (id: string) => void;
  t: (key: string) => string;
  lang: string;
}

function ReviewRow({
  item,
  style,
  inFlight,
  actionError,
  onApprove,
  onSkip,
  onDeepResearch,
  t,
  lang,
}: ReviewRowProps) {
  const isDisabled = inFlight !== null && inFlight !== undefined;

  const relativeTime = (() => {
    try {
      const date = new Date(item.created_at);
      const diff = Date.now() - date.getTime();
      const mins = Math.floor(diff / 60_000);
      if (mins < 60) return `${mins}m ago`;
      const hrs = Math.floor(mins / 60);
      if (hrs < 24) return `${hrs}h ago`;
      return date.toLocaleDateString(lang);
    } catch {
      return "";
    }
  })();

  const queryPreview = item.pre_generated_query
    ? item.pre_generated_query.split("\n")[0]?.slice(0, 120) ?? null
    : null;

  return (
    <div
      data-testid="review-item-row"
      data-item-id={item.id}
      style={{
        ...style,
        height: ROW_HEIGHT,
        padding: "8px 16px",
        borderBottom: "1px solid #21262d",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        boxSizing: "border-box",
      }}
    >
      {/* Row 1: type badge + page title + timestamp */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <ItemTypeBadge itemType={item.item_type} t={t} />
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: "#e6edf3",
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={item.page_title ?? item.page_id ?? ""}
        >
          {item.page_title ?? item.page_id ?? t("review.noPage")}
        </span>
        <span
          style={{ fontSize: 10, color: "#484f58", flexShrink: 0 }}
          title={item.created_at}
        >
          {relativeTime}
        </span>
      </div>

      {/* Row 2: pre-generated query (first line) or placeholder */}
      <div
        style={{
          fontSize: 11,
          color: queryPreview ? "#8b949e" : "#30363d",
          fontStyle: queryPreview ? "normal" : "italic",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          flex: 1,
        }}
        title={item.pre_generated_query ?? ""}
      >
        {queryPreview ?? t("review.noQuery")}
      </div>

      {/* Row 3: action buttons + per-item error */}
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <ActionButton
          label={
            inFlight === "approve" ? t("common.loading") : t("review.approve")
          }
          onClick={() => onApprove(item.id)}
          disabled={isDisabled}
          variant="approve"
        />
        <ActionButton
          label={
            inFlight === "skip" ? t("common.loading") : t("review.skip")
          }
          onClick={() => onSkip(item.id)}
          disabled={isDisabled}
          variant="skip"
        />
        <ActionButton
          label={
            inFlight === "deep-research"
              ? t("common.loading")
              : t("review.deepResearch")
          }
          onClick={() => onDeepResearch(item.id)}
          disabled={isDisabled}
          variant="deep-research"
        />

        {actionError && (
          <span
            role="alert"
            style={{ fontSize: 10, color: "#f85149", marginLeft: 4 }}
          >
            {actionError}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Item list (virtualised — I4) ─────────────────────────────────────────────

interface ReviewItemListProps {
  vaultId: string;
}

function ReviewItemList({ vaultId }: ReviewItemListProps) {
  const { t, i18n } = useTranslation();
  const items = useReviewStore(useShallow(selectReviewItems));
  const total = useReviewStore(selectReviewTotal);
  const loading = useReviewStore(selectReviewLoading);
  const actionInFlight = useReviewStore(useShallow(selectReviewActionInFlight));
  const actionError = useReviewStore(useShallow(selectReviewActionError));
  const fetchMore = useReviewStore(selectFetchMoreReview);
  const approve = useReviewStore(selectApprove);
  const skip = useReviewStore(selectSkip);
  const deepResearch = useReviewStore(selectDeepResearch);

  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 5,
  });

  const handleApprove = useCallback(
    (id: string) => { void approve(id); },
    [approve],
  );
  const handleSkip = useCallback(
    (id: string) => { void skip(id); },
    [skip],
  );
  const handleDeepResearch = useCallback(
    (id: string) => { void deepResearch(id); },
    [deepResearch],
  );

  if (items.length === 0 && !loading) {
    return (
      <div
        data-testid="review-empty"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "#484f58",
          fontSize: 13,
          padding: 24,
          textAlign: "center",
        }}
      >
        {t("review.empty")}
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
            <ReviewRow
              key={item.id}
              item={item}
              style={{ position: "absolute", top: vRow.start, width: "100%" }}
              inFlight={actionInFlight[item.id]}
              actionError={actionError[item.id]}
              onApprove={handleApprove}
              onSkip={handleSkip}
              onDeepResearch={handleDeepResearch}
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
              border: "1px solid #21262d",
              borderRadius: 6,
              background: "#161b22",
              color: "#8b949e",
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

// ─── Main ReviewQueueView ─────────────────────────────────────────────────────

export function ReviewQueueView() {
  const { t } = useTranslation();
  const vaultId = useGraphStore(selectVaultId);
  const setActiveSection = useGraphStore(selectSetActiveSection);

  const fetchFresh = useReviewStore(selectFetchFreshReview);
  const total = useReviewStore(selectReviewTotal);
  const loading = useReviewStore(selectReviewLoading);
  const error = useReviewStore(selectReviewError);
  const deepResearchError = useReviewStore(selectDeepResearchError);
  const lastDeepResearch = useReviewStore(selectLastDeepResearch);
  const clearDeepResearchError = useReviewStore(selectClearDeepResearchError);
  const clearLastDeepResearch = useReviewStore(selectClearLastDeepResearch);

  const effectiveVaultId = vaultId ?? "default";

  // Fetch on mount
  useEffect(() => {
    const ctrl = new AbortController();
    void fetchFresh(effectiveVaultId, ctrl.signal);
    return () => ctrl.abort();
  }, [effectiveVaultId, fetchFresh]);

  const handleGoToDeepSearch = useCallback(() => {
    clearLastDeepResearch();
    setActiveSection("deep-search");
  }, [clearLastDeepResearch, setActiveSection]);

  return (
    <div
      data-testid="review-queue-view"
      style={{
        display: "flex",
        flex: 1,
        flexDirection: "column",
        overflow: "hidden",
        width: "100%",
        height: "100%",
        background: "#0d1117",
      }}
    >
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 16px",
          borderBottom: "1px solid #21262d",
          flexShrink: 0,
          background: "#161b22",
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 600,
            color: "#e6edf3",
            flex: 1,
          }}
        >
          {t("review.title")}
          {total > 0 && !loading && (
            <span
              aria-label={`${total} pending`}
              style={{
                marginLeft: 8,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: 18,
                height: 18,
                padding: "0 5px",
                borderRadius: 9,
                background: "#d29922",
                color: "#0d1117",
                fontSize: 10,
                fontWeight: 700,
              }}
            >
              {total > 999 ? "999+" : total}
            </span>
          )}
        </h2>

        {/* Refresh button */}
        <button
          onClick={() => void fetchFresh(effectiveVaultId)}
          disabled={loading}
          aria-label={t("common.retry")}
          data-testid="review-refresh-btn"
          style={{
            padding: "4px 10px",
            fontSize: 11,
            border: "1px solid #21262d",
            borderRadius: 5,
            background: "transparent",
            color: loading ? "#484f58" : "#8b949e",
            cursor: loading ? "wait" : "pointer",
          }}
        >
          {loading ? t("common.loading") : t("review.refresh")}
        </button>
      </div>

      {/* ── 503 / SEARXNG unavailable banner ────────────────────────────── */}
      {deepResearchError && (
        <div
          role="alert"
          data-testid="review-searxng-error"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 16px",
            borderBottom: "1px solid #f8514933",
            background: "#1a0f0f",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, color: "#f85149", flex: 1 }}>
            {t("review.searxngUnavailable")}
          </span>
          <button
            onClick={clearDeepResearchError}
            style={{
              fontSize: 11,
              color: "#8b949e",
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: 0,
              textDecoration: "underline",
            }}
          >
            {t("common.close")}
          </button>
        </div>
      )}

      {/* ── Deep-research success banner ─────────────────────────────────── */}
      {lastDeepResearch && (
        <div
          data-testid="review-deep-research-success"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 16px",
            borderBottom: "1px solid #3fb95033",
            background: "#0d1f0d",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, color: "#3fb950", flex: 1 }}>
            {t("review.deepResearchStarted")}
            <span
              style={{
                marginLeft: 6,
                fontFamily: "monospace",
                fontSize: 11,
                color: "#8b949e",
              }}
            >
              run:{lastDeepResearch.runId.slice(0, 8)}…
            </span>
          </span>
          <button
            onClick={handleGoToDeepSearch}
            data-testid="review-goto-deepsearch"
            style={{
              fontSize: 11,
              color: "#58a6ff",
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: 0,
              textDecoration: "underline",
            }}
          >
            {t("review.viewRun")}
          </button>
          <button
            onClick={clearLastDeepResearch}
            style={{
              fontSize: 11,
              color: "#484f58",
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: 0,
            }}
            aria-label={t("common.close")}
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Load error ───────────────────────────────────────────────────── */}
      {error && !loading && (
        <div
          role="alert"
          data-testid="review-load-error"
          style={{
            padding: "8px 16px",
            borderBottom: "1px solid #21262d",
            flexShrink: 0,
            fontSize: 12,
            color: "#f85149",
            background: "#1a0f0f",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {error}
          <button
            onClick={() => void fetchFresh(effectiveVaultId)}
            style={{
              marginLeft: 4,
              fontSize: 12,
              color: "#8b949e",
              background: "none",
              border: "none",
              cursor: "pointer",
              textDecoration: "underline",
              padding: 0,
            }}
          >
            {t("common.retry")}
          </button>
        </div>
      )}

      {/* ── Hint row ─────────────────────────────────────────────────────── */}
      {!loading && !error && (
        <div
          style={{
            padding: "6px 16px",
            flexShrink: 0,
            borderBottom: "1px solid #21262d",
            fontSize: 11,
            color: "#484f58",
          }}
        >
          {t("review.hint")}
        </div>
      )}

      {/* ── Virtualised item list (I4) ───────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <ReviewItemList vaultId={effectiveVaultId} />
      </div>
    </div>
  );
}
