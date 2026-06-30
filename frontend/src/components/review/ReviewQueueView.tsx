/**
 * ReviewQueueView.tsx — F9 HITL Review Queue section (ADR-0034 §7.1).
 *
 * Layout:
 *   - Header with title + pending count badge + sweep button
 *   - 503 SEARXNG banner when deep-research is unavailable (I9)
 *   - Last deep-research success banner with run_id + jump link
 *   - Last sweep result banner (rule_resolved + llm_resolved counts)
 *   - Pending items list (TanStack Virtual — always virtualised for I4)
 *   - Per-item PROPOSAL card:
 *       type badge (5 types from ADR-0034 §3.1)
 *       proposed_title (bold)
 *       proposed_page_type chip (when present)
 *       rationale text ("why this matters")
 *       conflicting page_title link (contradiction / duplicate types only)
 *   - Per-item actions: Create (spinner during LLM generation) · Skip · Deep-Research
 *   - 502 Create failure handled as retry-or-skip hint (item stays pending)
 *   - 409 Create failure (no provider / not pending) as generic per-item error
 *   - Empty state and error state
 *
 * INVARIANT I3: Zustand selectors + shallow equality. No store subscriptions
 *   that trigger on unrelated state.
 * INVARIANT I4: list virtualised with TanStack Virtual; always on for the full list.
 * INVARIANT I6: Deep-Research action delegates to POST /review/queue/{id}/deep-research
 *   which in turn delegates to POST /research/start — no hardcoded provider (I6).
 * INVARIANT I7: create / skip do NOT re-trigger ingest (AC-F9-6, I1).
 *               Create DOES run a bounded LLM loop server-side (ADR-0034 §5).
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
  selectCreateGenerationError,
  selectLastDeepResearch,
  selectDeepResearchError,
  selectLastSweepResult,
  selectFetchFreshReview,
  selectFetchMoreReview,
  selectCreate,
  selectSkip,
  selectDeepResearch,
  selectSweep,
  selectClearDeepResearchError,
  selectClearLastDeepResearch,
  selectClearLastSweepResult,
  selectClearCreateGenerationError,
} from "../../store/reviewStore";
import { useGraphStore, selectVaultId, selectSetActiveSection } from "../../store/graphStore";
import { EmptyState } from "../common/EmptyState";
import type { ReviewItem } from "../../api/types";

// ─── Constants ────────────────────────────────────────────────────────────────

/**
 * Row height must cover: type badge + title row, proposed_page_type chip + rationale,
 * optional conflict row, action row. 132px gives comfortable padding.
 */
const ROW_HEIGHT = 132;

// ─── Proposal type badge ──────────────────────────────────────────────────────

/**
 * Five proposal types from ADR-0034 §3.1.
 * Colors chosen for semantic meaning: green=create, yellow=investigate, red=conflict,
 * purple=merge, blue=confirm.
 */
const ITEM_TYPE_COLORS: Record<string, { color: string; bg: string }> = {
  "missing-page":  { color: "#3fb950", bg: "#3fb95022" },
  suggestion:      { color: "#d29922", bg: "#d2992222" },
  contradiction:   { color: "#f85149", bg: "#f8514922" },
  duplicate:       { color: "#bc8cff", bg: "#bc8cff22" },
  confirm:         { color: "#58a6ff", bg: "#58a6ff22" },
};

interface ItemTypeBadgeProps {
  itemType: string;
  t: (key: string) => string;
}

function ItemTypeBadge({ itemType, t }: ItemTypeBadgeProps) {
  const { color, bg } = ITEM_TYPE_COLORS[itemType] ?? { color: "#8b949e", bg: "#8b949e22" };
  // i18n key: review.itemType.missing-page etc.
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
        flexShrink: 0,
      }}
    >
      {label}
    </span>
  );
}

// ─── Page type chip ───────────────────────────────────────────────────────────

interface PageTypeChipProps {
  pageType: string;
  t: (key: string) => string;
}

function PageTypeChip({ pageType, t }: PageTypeChipProps) {
  // i18n key: review.pageType.entity etc.
  const label = t(`review.pageType.${pageType}`) ?? pageType;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontSize: 9,
        fontWeight: 500,
        color: "#8b949e",
        background: "#21262d",
        border: "1px solid #30363d",
        borderRadius: 4,
        padding: "0 5px",
        whiteSpace: "nowrap",
        userSelect: "none",
        flexShrink: 0,
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
  loading?: boolean;
  variant: "create" | "skip" | "deep-research";
}

function ActionButton({ label, onClick, disabled, loading, variant }: ActionButtonProps) {
  const COLORS: Record<string, { border: string; color: string }> = {
    create:           { border: "#3fb950", color: "#3fb950" },
    skip:             { border: "#484f58", color: "#8b949e" },
    "deep-research":  { border: "#58a6ff", color: "#58a6ff" },
  };
  const fallback = { border: "#484f58", color: "#8b949e" };
  const { border, color } = COLORS[variant] ?? fallback;
  const isDisabled = disabled || loading;
  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      aria-label={label}
      aria-busy={loading}
      data-testid={`review-action-${variant}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "3px 10px",
        fontSize: 11,
        fontWeight: 600,
        border: `1px solid ${isDisabled ? "#21262d" : border}`,
        borderRadius: 5,
        background: "transparent",
        color: isDisabled ? "#484f58" : color,
        cursor: isDisabled ? "not-allowed" : "pointer",
        whiteSpace: "nowrap",
        transition: "opacity 0.1s",
      }}
    >
      {loading && (
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            border: "1.5px solid currentColor",
            borderTopColor: "transparent",
            animation: "syn-spin 0.7s linear infinite",
          }}
        />
      )}
      {label}
    </button>
  );
}

// ─── Review item row (virtualised) ────────────────────────────────────────────

interface ReviewRowProps {
  item: ReviewItem;
  style: CSSProperties;
  inFlight: "create" | "skip" | "deep-research" | null | undefined;
  actionError: string | null | undefined;
  generationError: string | null | undefined;
  onCreate: (id: string) => void;
  onSkip: (id: string) => void;
  onDeepResearch: (id: string) => void;
  onDismissGenerationError: (id: string) => void;
  t: (key: string) => string;
  lang: string;
}

function ReviewRow({
  item,
  style,
  inFlight,
  actionError,
  generationError,
  onCreate,
  onSkip,
  onDeepResearch,
  onDismissGenerationError,
  t,
  lang,
}: ReviewRowProps) {
  const isAnyInFlight = inFlight !== null && inFlight !== undefined;
  const isCreating = inFlight === "create";

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

  // Show conflict page for contradiction + duplicate types
  const showConflictPage =
    (item.item_type === "contradiction" || item.item_type === "duplicate") &&
    item.page_title !== null;

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
        gap: 3,
        boxSizing: "border-box",
        background: generationError ? "#1a0f0f" : undefined,
      }}
    >
      {/* Row 1: type badge + proposed_title + timestamp */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <ItemTypeBadge itemType={item.item_type} t={t} />
        {item.proposed_page_type && (
          <PageTypeChip pageType={item.proposed_page_type} t={t} />
        )}
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: "#e6edf3",
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            minWidth: 0,
          }}
          title={item.proposed_title ?? item.page_title ?? ""}
        >
          {item.proposed_title ?? item.page_title ?? t("review.noTitle")}
        </span>
        <span
          style={{ fontSize: 10, color: "#484f58", flexShrink: 0 }}
          title={item.created_at}
        >
          {relativeTime}
        </span>
      </div>

      {/* Row 2: rationale (why this matters) */}
      <div
        style={{
          fontSize: 11,
          color: item.rationale ? "#8b949e" : "#30363d",
          fontStyle: item.rationale ? "normal" : "italic",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={item.rationale ?? ""}
      >
        {item.rationale ?? t("review.noRationale")}
      </div>

      {/* Row 3: conflict page (contradiction / duplicate) */}
      {showConflictPage && (
        <div
          style={{
            fontSize: 10,
            color: "#bc8cff",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={item.page_title ?? ""}
        >
          {t("review.conflictsWith")}: <em>{item.page_title}</em>
        </div>
      )}

      {/* Row 4: 502 generation error — retry-or-skip hint */}
      {generationError && (
        <div
          role="alert"
          style={{
            fontSize: 10,
            color: "#f85149",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            display: "flex",
            alignItems: "center",
            gap: 4,
          }}
        >
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
            {t("review.createFailed")}
          </span>
          <button
            onClick={() => onDismissGenerationError(item.id)}
            style={{
              fontSize: 10,
              color: "#484f58",
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: 0,
              flexShrink: 0,
            }}
            aria-label={t("common.close")}
          >
            {t("common.close")}
          </button>
        </div>
      )}

      {/* Row 5: action buttons + per-item non-502 error */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <ActionButton
          label={isCreating ? t("review.creating") : t("review.create")}
          onClick={() => onCreate(item.id)}
          disabled={isAnyInFlight}
          loading={isCreating}
          variant="create"
        />
        <ActionButton
          label={inFlight === "skip" ? t("common.loading") : t("review.skip")}
          onClick={() => onSkip(item.id)}
          disabled={isAnyInFlight}
          variant="skip"
        />
        <ActionButton
          label={
            inFlight === "deep-research"
              ? t("common.loading")
              : t("review.deepResearch")
          }
          onClick={() => onDeepResearch(item.id)}
          disabled={isAnyInFlight}
          variant="deep-research"
        />

        {actionError && !generationError && (
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
  onOpenSources: () => void;
}

function ReviewItemList({ vaultId, onOpenSources }: ReviewItemListProps) {
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
  const deepResearch = useReviewStore(selectDeepResearch);
  const clearGenerationError = useReviewStore(selectClearCreateGenerationError);

  const scrollRef = useRef<HTMLDivElement>(null);

  // Always virtualise — the virtualiser is efficient regardless of list size (I4).
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 5,
  });

  const handleCreate = useCallback(
    (id: string) => { void create(id); },
    [create],
  );
  const handleSkip = useCallback(
    (id: string) => { void skip(id); },
    [skip],
  );
  const handleDeepResearch = useCallback(
    (id: string) => { void deepResearch(id); },
    [deepResearch],
  );
  const handleDismissGenerationError = useCallback(
    (id: string) => { clearGenerationError(id); },
    [clearGenerationError],
  );

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
            <ReviewRow
              key={item.id}
              item={item}
              style={{ position: "absolute", top: vRow.start, width: "100%" }}
              inFlight={actionInFlight[item.id]}
              actionError={actionError[item.id]}
              generationError={generationError[item.id]}
              onCreate={handleCreate}
              onSkip={handleSkip}
              onDeepResearch={handleDeepResearch}
              onDismissGenerationError={handleDismissGenerationError}
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
  const lastSweepResult = useReviewStore(selectLastSweepResult);
  const clearDeepResearchError = useReviewStore(selectClearDeepResearchError);
  const clearLastDeepResearch = useReviewStore(selectClearLastDeepResearch);
  const clearLastSweepResult = useReviewStore(selectClearLastSweepResult);
  const sweep = useReviewStore(selectSweep);

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

  const handleOpenSources = useCallback(() => {
    setActiveSection("ingest");
  }, [setActiveSection]);

  const handleSweep = useCallback(() => {
    void sweep(effectiveVaultId);
  }, [sweep, effectiveVaultId]);

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
      {/* Spinner keyframe — injected once as a style tag */}
      <style>{`@keyframes syn-spin { to { transform: rotate(360deg); } }`}</style>

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

        {/* Sweep button — clean up auto-resolved proposals (ADR-0034 §6) */}
        <button
          onClick={handleSweep}
          disabled={loading}
          aria-label={t("review.sweep")}
          data-testid="review-sweep-btn"
          style={{
            padding: "4px 10px",
            fontSize: 11,
            border: "1px solid #21262d",
            borderRadius: 5,
            background: "transparent",
            color: loading ? "#484f58" : "#8b949e",
            cursor: loading ? "wait" : "pointer",
          }}
          title={t("review.sweepHelp")}
        >
          {t("review.sweep")}
        </button>

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
              run:{lastDeepResearch.runId.slice(0, 8)}&hellip;
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
            &times;
          </button>
        </div>
      )}

      {/* ── Sweep result banner (ADR-0034 §6) ──────────────────────────────── */}
      {lastSweepResult && (
        <div
          data-testid="review-sweep-result"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 16px",
            borderBottom: "1px solid #58a6ff33",
            background: "#0d1624",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, color: "#58a6ff", flex: 1 }}>
            {t("review.sweepResult", {
              rule: lastSweepResult.rule_resolved,
              llm: lastSweepResult.llm_resolved,
              kept: lastSweepResult.kept,
            })}
          </span>
          <button
            onClick={clearLastSweepResult}
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
            &times;
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
        <ReviewItemList vaultId={effectiveVaultId} onOpenSources={handleOpenSources} />
      </div>
    </div>
  );
}
