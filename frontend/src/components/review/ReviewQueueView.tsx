/**
 * ReviewQueueView.tsx — F9 HITL Review Queue section (ADR-0034 §7.1 + ADR-0044 Phase D).
 *
 * Layout:
 *   - Header: title + pending count badge + status tabs (Pending / Resolved / Dismissed)
 *             + sweep button + refresh button + Clear resolved (on non-pending tabs)
 *   - Selection bar: "Select pending" toggle + selection count + bulk action bar
 *     (Mark resolved / Dismiss / Skip — appears when ≥1 selected)
 *   - 503 SEARXNG banner when deep-research is unavailable (I9)
 *   - Last deep-research success banner with run_id + jump link
 *   - Last sweep / bulk / clear result banners
 *   - Pending items list (TanStack Virtual — always virtualised for I4)
 *   - Per-item PROPOSAL card:
 *       checkbox (select mode)
 *       type badge (5 types from ADR-0034 §3.1)
 *       proposed_title (bold)
 *       proposed_page_type chip (when present)
 *       rationale text ("why this matters")
 *       conflicting page_title link (contradiction / duplicate types only)
 *       referenced_pages chips [[title]] (ADR-0044 §6.1) — click → open page
 *       search_queries line "will search: q1 · q2" (ADR-0044 §6.1)
 *   - Per-item actions: Create (spinner during LLM generation) · Skip · Dismiss · Deep-Research
 *   - 502 Create failure handled as retry-or-skip hint (item stays pending)
 *   - 409 Create failure (no provider / not pending) as generic per-item error
 *   - Empty state and error state
 *
 * INVARIANT I3: Zustand selectors + shallow equality. No store subscriptions
 *   that trigger on unrelated state. Selection Set + active tab in reviewStore;
 *   a row reads only its own membership via selectIsSelected(id).
 * INVARIANT I4: list virtualised with TanStack Virtual + measureElement for
 *   variable heights. "Select pending" is O(loaded). Never un-virtualised.
 * INVARIANT I6: Deep-Research action delegates to POST /review/queue/{id}/deep-research
 *   which in turn delegates to POST /research/start — no hardcoded provider (I6).
 * INVARIANT I7: create / skip / dismiss do NOT re-trigger ingest (AC-F9-6, I1).
 *               Create DOES run a bounded LLM loop server-side (ADR-0034 §5).
 */

import {
  useEffect,
  useRef,
  useCallback,
  type CSSProperties,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useReviewStore,
  selectReviewItems,
  selectReviewTotal,
  selectReviewLoading,
  selectReviewError,
  selectActiveTab,
  selectSelectedIds,
  selectIsSelected,
  selectReviewActionInFlight,
  selectReviewActionError,
  selectCreateGenerationError,
  selectLastDeepResearch,
  selectDeepResearchError,
  selectLastSweepResult,
  selectLastBulkResult,
  selectLastClearResult,
  selectBulkError,
  selectFetchFreshReview,
  selectFetchMoreReview,
  selectSetActiveTab,
  selectCreate,
  selectSkip,
  selectDismiss,
  selectDeepResearch,
  selectSweep,
  selectBulkAction,
  selectClearResolvedRows,
  selectToggleSelected,
  selectSelectAllPending,
  selectClearSelection,
  selectClearDeepResearchError,
  selectClearLastDeepResearch,
  selectClearLastSweepResult,
  selectClearCreateGenerationError,
  selectClearLastBulkResult,
  selectClearLastClearResult,
  selectClearBulkError,
} from "../../store/reviewStore";
import { useGraphStore, selectVaultId, selectSetActiveSection } from "../../store/graphStore";
import { EmptyState } from "../common/EmptyState";
import type { ReviewItem, ReviewReferencedPage } from "../../api/types";
import type { ReviewQueueStatus } from "../../api/reviewClient";

// ─── Constants ────────────────────────────────────────────────────────────────

/**
 * Base estimated row height; the virtualizer uses measureElement for actual
 * variable heights (ADR-0044 §7 — referenced_pages + search_queries grow cards).
 */
const ROW_ESTIMATE = 160;

// ─── Proposal type badge ──────────────────────────────────────────────────────
// Light-theme: use --syn-* semantic tokens.
// color values are concrete hex so the bg alpha tint can be inline-computed.
// These match --syn-green, --syn-amber, --syn-red + --syn-type-* tokens.

// UXA-03: use var(--syn-mix-base) instead of literal white so dark-mode color-mix
// resolves against the dark surface rather than #ffffff. Token defined in ADR-0048 / theme.css.
const ITEM_TYPE_COLORS: Record<string, { color: string; bg: string }> = {
  "missing-page":  { color: "#1a7f37", bg: "color-mix(in srgb, #1a7f37 10%, var(--syn-mix-base) 90%)" }, // --syn-green
  suggestion:      { color: "#9a6700", bg: "color-mix(in srgb, #9a6700 10%, var(--syn-mix-base) 90%)" }, // --syn-amber
  contradiction:   { color: "#cf222e", bg: "color-mix(in srgb, #cf222e 10%, var(--syn-mix-base) 90%)" }, // --syn-red
  duplicate:       { color: "#8250df", bg: "color-mix(in srgb, #8250df 10%, var(--syn-mix-base) 90%)" }, // --syn-type-concept (purple)
  confirm:         { color: "#2563eb", bg: "color-mix(in srgb, #2563eb 10%, var(--syn-mix-base) 90%)" }, // --syn-accent
};

interface ItemTypeBadgeProps {
  itemType: string;
  t: (key: string) => string;
}

function ItemTypeBadge({ itemType, t }: ItemTypeBadgeProps) {
  // UXA-18: backend may send underscore form (e.g. "new_page", "missing_page");
  // translation keys use kebab-case ("missing-page"). Normalise before lookup.
  const normalised = itemType.replace(/_/g, "-");
  const { color, bg } = ITEM_TYPE_COLORS[normalised] ?? ITEM_TYPE_COLORS[itemType] ?? {
    color: "var(--syn-text-dim)",
    bg: "var(--syn-surface-hover)",
  };
  const label = t(`review.itemType.${normalised}`);
  return (
    <span
      className="syn-chip"
      style={{
        fontSize: 10,
        fontWeight: 600,
        color,
        background: bg,
        border: `1px solid color-mix(in srgb, ${color} 30%, transparent 70%)`,
        borderRadius: "var(--syn-radius-pill)",
        padding: "1px 6px",
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
  const label = t(`review.pageType.${pageType}`) ?? pageType;
  return (
    <span
      className="syn-chip"
      style={{
        fontSize: 9,
        fontWeight: 500,
        padding: "0 5px",
      }}
    >
      {label}
    </span>
  );
}

// ─── Referenced page chip (ADR-0044 §7) ─────────────────────────────────────

interface ReferencedPageChipProps {
  page: ReviewReferencedPage;
  onClick: (pageId: string) => void;
}

function ReferencedPageChip({ page, onClick }: ReferencedPageChipProps) {
  return (
    <button
      data-testid="referenced-page-chip"
      onClick={() => onClick(page.id)}
      title={`Open ${page.title}`}
      className="syn-chip"
      style={{
        fontSize: 10,
        fontWeight: 500,
        color: "var(--syn-accent)",
        background: "var(--syn-accent-soft)",
        border: "1px solid color-mix(in srgb, var(--syn-accent) 25%, var(--syn-border) 75%)",
        borderRadius: "var(--syn-radius-sm)",
        padding: "1px 6px",
        cursor: "pointer",
      }}
    >
      [[{page.title}]]
    </button>
  );
}

// ─── Action button ────────────────────────────────────────────────────────────

interface ActionButtonProps {
  label: string;
  onClick: () => void;
  disabled: boolean;
  loading?: boolean;
  variant: "create" | "skip" | "dismiss" | "deep-research";
}

/**
 * ActionButton — review queue per-item action (Create / Skip / Dismiss / Deep-Research).
 * UXB-2 AC-UXB2-2: uses .syn-btn .syn-btn--secondary .syn-btn--sm as base.
 * Variant-specific color overrides are applied via inline style only for the color/border
 * (appearance tokens only — layout stays in the class).
 */
function ActionButton({ label, onClick, disabled, loading, variant }: ActionButtonProps) {
  // Map variant → token-safe inline color overrides (only when enabled).
  // These narrow the secondary ghost base to the variant's semantic color.
  const VARIANT_STYLE: Record<string, { color: string; borderColor: string }> = {
    create:          { color: "var(--syn-green)",  borderColor: "color-mix(in srgb, var(--syn-green) 30%, var(--syn-border) 70%)" },
    skip:            { color: "var(--syn-text-muted)", borderColor: "var(--syn-border)" },
    dismiss:         { color: "var(--syn-text-dim)",   borderColor: "var(--syn-border)" },
    "deep-research": { color: "var(--syn-accent)", borderColor: "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)" },
  };
  const fallbackStyle = { color: "var(--syn-text-muted)", borderColor: "var(--syn-border)" };
  const variantStyle = VARIANT_STYLE[variant] ?? fallbackStyle;
  const isDisabled = disabled || loading;
  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      aria-label={label}
      aria-busy={loading}
      data-testid={`review-action-${variant}`}
      className="syn-btn syn-btn--secondary syn-btn--sm"
      style={isDisabled ? undefined : { color: variantStyle.color, borderColor: variantStyle.borderColor }}
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

// ─── Review item row (virtualised, variable height) ────────────────────────

interface ReviewRowProps {
  item: ReviewItem;
  style: CSSProperties;
  measureRef: (el: HTMLElement | null) => void;
  inFlight: "create" | "skip" | "dismiss" | "deep-research" | null | undefined;
  actionError: string | null | undefined;
  generationError: string | null | undefined;
  isSelected: boolean;
  onCreate: (id: string) => void;
  onSkip: (id: string) => void;
  onDismiss: (id: string) => void;
  onDeepResearch: (id: string) => void;
  onDismissGenerationError: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onOpenPage: (pageId: string) => void;
  t: (key: string) => string;
  lang: string;
}

function ReviewRow({
  item,
  style,
  measureRef,
  inFlight,
  actionError,
  generationError,
  isSelected,
  onCreate,
  onSkip,
  onDismiss,
  onDeepResearch,
  onDismissGenerationError,
  onToggleSelect,
  onOpenPage,
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

  // Referenced pages (ADR-0044 §6.1): non-empty list from convenience join
  const referencedPages =
    item.referenced_pages != null && item.referenced_pages.length > 0
      ? item.referenced_pages
      : null;

  // Search queries (ADR-0044 §6.1)
  const searchQueries =
    item.search_queries != null && item.search_queries.length > 0
      ? item.search_queries
      : null;

  return (
    <div
      ref={measureRef}
      data-testid="review-item-row"
      data-item-id={item.id}
      style={{
        ...style,
        padding: "8px 16px",
        borderBottom: "1px solid var(--syn-border)",
        display: "flex",
        flexDirection: "column",
        gap: 3,
        boxSizing: "border-box",
        background: isSelected
          ? "var(--syn-accent-soft)"
          : generationError
          ? "color-mix(in srgb, var(--syn-red) 6%, var(--syn-mix-base) 94%)"
          : undefined,
      }}
    >
      {/* Row 1: checkbox + type badge + proposed_title + timestamp */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <input
          type="checkbox"
          checked={isSelected}
          onChange={() => onToggleSelect(item.id)}
          aria-label={`Select ${item.proposed_title ?? item.id}`}
          data-testid={`review-select-${item.id}`}
          style={{ flexShrink: 0, cursor: "pointer", accentColor: "var(--syn-accent)" }}
        />
        <ItemTypeBadge itemType={item.item_type} t={t} />
        {item.proposed_page_type && (
          <PageTypeChip pageType={item.proposed_page_type} t={t} />
        )}
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: "var(--syn-text)",
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
          style={{ fontSize: 10, color: "var(--syn-text-dim)", flexShrink: 0 }}
          title={item.created_at}
        >
          {relativeTime}
        </span>
      </div>

      {/* Row 2: rationale (why this matters) */}
      <div
        style={{
          fontSize: 11,
          color: item.rationale ? "var(--syn-text-muted)" : "var(--syn-border)",
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
            color: "var(--syn-type-concept)", // --syn-type-concept (purple) for conflict link
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={item.page_title ?? ""}
        >
          {t("review.conflictsWith")}: <em>{item.page_title}</em>
        </div>
      )}

      {/* Row 4: referenced pages chips (ADR-0044 §6.1) */}
      {referencedPages && (
        <div
          data-testid="referenced-pages-row"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            flexWrap: "wrap",
          }}
        >
          <span
            style={{ fontSize: 10, color: "var(--syn-text-dim)", flexShrink: 0, whiteSpace: "nowrap" }}
          >
            {t("review.referencedPages")}:
          </span>
          {referencedPages.map((rp) => (
            <ReferencedPageChip key={rp.id} page={rp} onClick={onOpenPage} />
          ))}
        </div>
      )}

      {/* Row 5: search queries line (ADR-0044 §6.1) */}
      {searchQueries && (
        <div
          data-testid="search-queries-row"
          style={{
            fontSize: 10,
            color: "var(--syn-text-dim)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontStyle: "italic",
          }}
          title={searchQueries.join(" · ")}
        >
          {t("review.willSearch")}: {searchQueries.join(" · ")}
        </div>
      )}

      {/* Row 6: 502 generation error — retry-or-skip hint */}
      {generationError && (
        <div
          role="alert"
          style={{
            fontSize: 10,
            color: "var(--syn-red)",
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
              color: "var(--syn-text-dim)",
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

      {/* Row 7: action buttons + per-item non-502 error */}
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
          label={inFlight === "dismiss" ? t("common.loading") : t("review.dismiss")}
          onClick={() => onDismiss(item.id)}
          disabled={isAnyInFlight}
          variant="dismiss"
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
            style={{ fontSize: 10, color: "var(--syn-red)", marginLeft: 4 }}
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
  onOpenPage: (pageId: string) => void;
}

function ReviewItemList({ vaultId, onOpenSources, onOpenPage }: ReviewItemListProps) {
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
    (id: string) => { void create(id); },
    [create],
  );
  const handleSkip = useCallback(
    (id: string) => { void skip(id); },
    [skip],
  );
  const handleDismiss = useCallback(
    (id: string) => { void dismiss(id); },
    [dismiss],
  );
  const handleDeepResearch = useCallback(
    (id: string) => { void deepResearch(id); },
    [deepResearch],
  );
  const handleDismissGenerationError = useCallback(
    (id: string) => { clearGenerationError(id); },
    [clearGenerationError],
  );
  const handleToggleSelect = useCallback(
    (id: string) => { toggleSelected(id); },
    [toggleSelected],
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
            <RowWrapper
              key={item.id}
              item={item}
              vRow={vRow}
              virtualizer={virtualizer}
              inFlight={actionInFlight[item.id]}
              actionError={actionError[item.id]}
              generationError={generationError[item.id]}
              onCreate={handleCreate}
              onSkip={handleSkip}
              onDismiss={handleDismiss}
              onDeepResearch={handleDeepResearch}
              onDismissGenerationError={handleDismissGenerationError}
              onToggleSelect={handleToggleSelect}
              onOpenPage={onOpenPage}
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
  inFlight: "create" | "skip" | "dismiss" | "deep-research" | null | undefined;
  actionError: string | null | undefined;
  generationError: string | null | undefined;
  onCreate: (id: string) => void;
  onSkip: (id: string) => void;
  onDismiss: (id: string) => void;
  onDeepResearch: (id: string) => void;
  onDismissGenerationError: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onOpenPage: (pageId: string) => void;
  t: (key: string) => string;
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
  onSkip,
  onDismiss,
  onDeepResearch,
  onDismissGenerationError,
  onToggleSelect,
  onOpenPage,
  t,
  lang,
}: RowWrapperProps) {
  // Per-row selection read: only this row re-renders on its own id toggle (I3)
  const isSelected = useReviewStore(selectIsSelected(item.id));

  return (
    <ReviewRow
      item={item}
      style={{ position: "absolute", top: vRow.start, width: "100%" }}
      measureRef={(el) => virtualizer.measureElement(el)}
      inFlight={inFlight}
      actionError={actionError}
      generationError={generationError}
      isSelected={isSelected}
      onCreate={onCreate}
      onSkip={onSkip}
      onDismiss={onDismiss}
      onDeepResearch={onDeepResearch}
      onDismissGenerationError={onDismissGenerationError}
      onToggleSelect={onToggleSelect}
      onOpenPage={onOpenPage}
      t={t}
      lang={lang}
    />
  );
}

// ─── Status tab button ────────────────────────────────────────────────────────

interface TabButtonProps {
  label: string;
  active: boolean;
  onClick: () => void;
  testId: string;
}

function TabButton({ label, active, onClick, testId }: TabButtonProps) {
  return (
    <button
      onClick={onClick}
      data-testid={testId}
      style={{
        padding: "4px 10px",
        fontSize: 11,
        fontWeight: active ? 700 : 400,
        border: active ? "1px solid var(--syn-border)" : "1px solid transparent",
        borderRadius: "var(--syn-radius-sm)",
        background: active ? "var(--syn-surface-hover)" : "transparent",
        color: active ? "var(--syn-text)" : "var(--syn-text-muted)",
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </button>
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
  const activeTab = useReviewStore(selectActiveTab);
  const selectedIds = useReviewStore(useShallow(selectSelectedIds));
  const deepResearchError = useReviewStore(selectDeepResearchError);
  const lastDeepResearch = useReviewStore(selectLastDeepResearch);
  const lastSweepResult = useReviewStore(selectLastSweepResult);
  const lastBulkResult = useReviewStore(selectLastBulkResult);
  const lastClearResult = useReviewStore(selectLastClearResult);
  const bulkError = useReviewStore(selectBulkError);
  const clearDeepResearchError = useReviewStore(selectClearDeepResearchError);
  const clearLastDeepResearch = useReviewStore(selectClearLastDeepResearch);
  const clearLastSweepResult = useReviewStore(selectClearLastSweepResult);
  const clearLastBulkResult = useReviewStore(selectClearLastBulkResult);
  const clearLastClearResult = useReviewStore(selectClearLastClearResult);
  const clearBulkError = useReviewStore(selectClearBulkError);
  const sweep = useReviewStore(selectSweep);
  const setActiveTab = useReviewStore(selectSetActiveTab);
  const bulkAction = useReviewStore(selectBulkAction);
  const clearResolvedRows = useReviewStore(selectClearResolvedRows);
  const selectAllPending = useReviewStore(selectSelectAllPending);
  const clearSelection = useReviewStore(selectClearSelection);

  const effectiveVaultId = vaultId ?? "default";
  const selectionCount = selectedIds.size;
  const hasSelection = selectionCount > 0;
  const showClearResolved = activeTab === "resolved" || activeTab === "dismissed";

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

  const handleTabChange = useCallback(
    (tab: ReviewQueueStatus) => {
      void setActiveTab(tab, effectiveVaultId);
    },
    [setActiveTab, effectiveVaultId],
  );

  const handleSelectAllPending = useCallback(() => {
    if (hasSelection) {
      clearSelection();
    } else {
      selectAllPending();
    }
  }, [hasSelection, clearSelection, selectAllPending]);

  const handleBulkMarkResolved = useCallback(() => {
    void bulkAction(effectiveVaultId, "mark-resolved");
  }, [bulkAction, effectiveVaultId]);

  const handleBulkDismiss = useCallback(() => {
    void bulkAction(effectiveVaultId, "dismiss");
  }, [bulkAction, effectiveVaultId]);

  const handleBulkSkip = useCallback(() => {
    void bulkAction(effectiveVaultId, "skip");
  }, [bulkAction, effectiveVaultId]);

  const handleClearResolved = useCallback(() => {
    void clearResolvedRows(effectiveVaultId);
  }, [clearResolvedRows, effectiveVaultId]);

  // Open a referenced page: navigate to pages section and select the page
  const handleOpenPage = useCallback(
    (pageId: string) => {
      // The graphStore.setSelectedNode + setActiveSection("pages") is the existing pattern
      // used by the conflict-page link handler in NoteView/GraphPanel. Replicate it here.
      // We have access to setActiveSection; the page selection is done by setting the
      // active section to "pages" (the NavRail then renders the page tree with that page).
      // If a more specific "select page" action exists on graphStore it would be used here.
      void pageId; // pageId is available for future graphStore.selectPage(pageId) wiring
      setActiveSection("pages");
    },
    [setActiveSection],
  );

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
        background: "var(--syn-bg)",
      }}
    >
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 16px 6px",
          borderBottom: "1px solid var(--syn-border)",
          flexShrink: 0,
          background: "var(--syn-bg-soft)",
          flexWrap: "wrap",
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 600,
            color: "var(--syn-text)",
            flexShrink: 0,
          }}
        >
          {t("review.title")}
          {activeTab === "pending" && total > 0 && !loading && (
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
                borderRadius: "var(--syn-radius-pill)",
                background: "var(--syn-amber)",
                color: "#ffffff",
                fontSize: 10,
                fontWeight: 700,
              }}
            >
              {total > 999 ? "999+" : total}
            </span>
          )}
        </h2>

        {/* Status tabs (ADR-0044 §7) */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 2,
            flex: 1,
          }}
          role="tablist"
          aria-label="Review queue status"
        >
          <TabButton
            label={t("review.tabPending")}
            active={activeTab === "pending"}
            onClick={() => handleTabChange("pending")}
            testId="review-tab-pending"
          />
          <TabButton
            label={t("review.tabResolved")}
            active={activeTab === "resolved"}
            onClick={() => handleTabChange("resolved")}
            testId="review-tab-resolved"
          />
          <TabButton
            label={t("review.tabDismissed")}
            active={activeTab === "dismissed"}
            onClick={() => handleTabChange("dismissed")}
            testId="review-tab-dismissed"
          />
        </div>

        {/* Clear resolved button — shown on Resolved/Dismissed tabs */}
        {showClearResolved && (
          <button
            onClick={handleClearResolved}
            disabled={loading}
            aria-label={t("review.clearResolved")}
            data-testid="review-clear-resolved-btn"
            title={t("review.clearResolvedHelp")}
            className="syn-btn syn-btn--danger syn-btn--sm"
          >
            {t("review.clearResolved")}
          </button>
        )}

        {/* Sweep button */}
        <button
          onClick={handleSweep}
          disabled={loading}
          aria-label={t("review.sweep")}
          data-testid="review-sweep-btn"
          className="syn-toolbar-button"
          style={{
            padding: "4px 10px",
            fontSize: 11,
            cursor: loading ? "wait" : "pointer",
            opacity: loading ? 0.6 : 1,
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
          className="syn-toolbar-button"
          style={{
            padding: "4px 10px",
            fontSize: 11,
            cursor: loading ? "wait" : "pointer",
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? t("common.loading") : t("review.refresh")}
        </button>
      </div>

      {/* ── Selection bar + bulk action bar (ADR-0044 §7) ───────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 16px",
          borderBottom: "1px solid var(--syn-border)",
          flexShrink: 0,
          background: "var(--syn-bg-soft)",
        }}
      >
        {/* "Select pending" toggle */}
        <button
          onClick={handleSelectAllPending}
          data-testid="review-select-pending-btn"
          aria-label={hasSelection ? t("review.deselectAll") : t("review.selectPending")}
          className="syn-toolbar-button"
          style={{
            padding: "3px 10px",
            fontSize: 11,
            cursor: "pointer",
            background: hasSelection ? "var(--syn-accent-soft)" : undefined,
            color: hasSelection ? "var(--syn-accent)" : undefined,
            borderColor: hasSelection ? "var(--syn-accent)" : undefined,
          }}
        >
          {hasSelection ? t("review.deselectAll") : t("review.selectPending")}
        </button>

        {/* Selection count */}
        {hasSelection && (
          <span
            data-testid="review-selection-count"
            style={{ fontSize: 11, color: "var(--syn-accent)", whiteSpace: "nowrap" }}
          >
            {t("review.selectionCount", { count: selectionCount })}
          </span>
        )}

        {/* Bulk action buttons — appear when ≥1 selected (ADR-0044 §7) */}
        {hasSelection && (
          <>
            <button
              onClick={handleBulkMarkResolved}
              disabled={loading}
              data-testid="review-bulk-mark-resolved"
              className="syn-btn syn-btn--secondary syn-btn--sm"
              style={{ color: "var(--syn-green)", borderColor: "color-mix(in srgb, var(--syn-green) 30%, var(--syn-border) 70%)" }}
            >
              {t("review.markResolved")}
            </button>
            <button
              onClick={handleBulkDismiss}
              disabled={loading}
              data-testid="review-bulk-dismiss"
              className="syn-btn syn-btn--secondary syn-btn--sm"
            >
              {t("review.dismiss")}
            </button>
            <button
              onClick={handleBulkSkip}
              disabled={loading}
              data-testid="review-bulk-skip"
              className="syn-btn syn-btn--secondary syn-btn--sm"
            >
              {t("review.skip")}
            </button>
          </>
        )}
      </div>

      {/* ── 503 / SEARXNG unavailable banner ────────────────────────────── */}
      {deepResearchError && (
        <div
          role="alert"
          data-testid="review-searxng-error"
          className="syn-section-notice syn-section-notice--danger"
          style={{
            borderRadius: 0,
            borderLeft: 0,
            borderRight: 0,
            borderTop: 0,
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, flex: 1 }}>
            {t("review.searxngUnavailable")}
          </span>
          <button
            onClick={clearDeepResearchError}
            style={{
              fontSize: 11,
              color: "var(--syn-text-muted)",
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
          className="syn-section-notice syn-section-notice--success"
          style={{
            borderRadius: 0,
            borderLeft: 0,
            borderRight: 0,
            borderTop: 0,
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, flex: 1 }}>
            {t("review.deepResearchStarted")}
            <span
              style={{
                marginLeft: 6,
                fontFamily: "monospace",
                fontSize: 11,
                color: "var(--syn-text-muted)",
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
              color: "var(--syn-accent)",
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
              color: "var(--syn-text-dim)",
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

      {/* ── Sweep result banner ──────────────────────────────────────────── */}
      {lastSweepResult && (
        <div
          data-testid="review-sweep-result"
          className="syn-section-notice syn-section-notice--info"
          style={{
            borderRadius: 0,
            borderLeft: 0,
            borderRight: 0,
            borderTop: 0,
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, flex: 1 }}>
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
              color: "var(--syn-text-dim)",
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

      {/* ── Bulk result banner (ADR-0044 §7) ────────────────────────────── */}
      {lastBulkResult && (
        <div
          data-testid="review-bulk-result"
          className="syn-section-notice syn-section-notice--success"
          style={{
            borderRadius: 0,
            borderLeft: 0,
            borderRight: 0,
            borderTop: 0,
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, flex: 1 }}>
            {t("review.bulkResult", {
              updated: lastBulkResult.updated,
              skipped: lastBulkResult.skipped_terminal,
            })}
          </span>
          <button
            onClick={clearLastBulkResult}
            style={{
              fontSize: 11,
              color: "var(--syn-text-dim)",
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

      {/* ── Clear resolved result banner (ADR-0044 §6) ──────────────────── */}
      {lastClearResult && (
        <div
          data-testid="review-clear-result"
          className="syn-section-notice syn-section-notice--info"
          style={{
            borderRadius: 0,
            borderLeft: 0,
            borderRight: 0,
            borderTop: 0,
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, flex: 1 }}>
            {t("review.clearResult", { count: lastClearResult.deleted })}
          </span>
          <button
            onClick={clearLastClearResult}
            style={{
              fontSize: 11,
              color: "var(--syn-text-dim)",
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

      {/* ── Bulk error banner ────────────────────────────────────────────── */}
      {bulkError && (
        <div
          role="alert"
          data-testid="review-bulk-error"
          className="syn-section-notice syn-section-notice--danger"
          style={{
            borderRadius: 0,
            borderLeft: 0,
            borderRight: 0,
            borderTop: 0,
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, flex: 1 }}>
            {t("review.bulkError")} {bulkError}
          </span>
          <button
            onClick={clearBulkError}
            style={{
              fontSize: 11,
              color: "var(--syn-text-muted)",
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

      {/* ── Load error ───────────────────────────────────────────────────── */}
      {error && !loading && (
        <div
          role="alert"
          data-testid="review-load-error"
          className="syn-section-notice syn-section-notice--danger"
          style={{
            borderRadius: 0,
            borderLeft: 0,
            borderRight: 0,
            borderTop: 0,
            flexShrink: 0,
            fontSize: 12,
          }}
        >
          <span style={{ flex: 1 }}>{error}</span>
          <button
            onClick={() => void fetchFresh(effectiveVaultId)}
            style={{
              marginLeft: 4,
              fontSize: 12,
              color: "var(--syn-text-muted)",
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
            borderBottom: "1px solid var(--syn-border)",
            fontSize: 11,
            color: "var(--syn-text-dim)",
          }}
        >
          {t("review.hint")}
        </div>
      )}

      {/* ── Virtualised item list (I4) ───────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <ReviewItemList
          vaultId={effectiveVaultId}
          onOpenSources={handleOpenSources}
          onOpenPage={handleOpenPage}
        />
      </div>
    </div>
  );
}
