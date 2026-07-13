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
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";
import {
  HelpCircle,
  FileQuestion,
  Lightbulb,
  AlertTriangle,
  Copy,
  MessageSquare,
  Target,
  FileCog,
  X,
  type LucideIcon,
} from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useReviewStore,
  selectReviewItems,
  selectReviewTotal,
  selectReviewLoading,
  selectReviewError,
  selectActiveTab,
  selectReviewFilters,
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
  selectSetReviewFilters,
  selectClearReviewFilters,
  selectCreate,
  selectSkip,
  selectDismiss,
  selectApprove,
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
import { ReviewDeepResearchPanel } from "./ReviewDeepResearchPanel";
import { PanelDrawer } from "../panels/PanelDrawer";
import { useViewport } from "../../hooks/useViewport";
import {
  useGraphStore,
  selectVaultId,
  selectSetActiveSection,
  selectSelectPage,
} from "../../store/graphStore";
import { EmptyState } from "../common/EmptyState";
import type {
  PageType,
  ReviewItem,
  ReviewItemStatus,
  ReviewItemType,
  ReviewProposalOrigin,
  ReviewReferencedPage,
} from "../../api/types";
import type { ReviewQueueStatus } from "../../api/reviewClient";

// ─── Constants ────────────────────────────────────────────────────────────────

/**
 * Base estimated row height; the virtualizer uses measureElement for actual
 * variable heights (ADR-0044 §7 — referenced_pages + search_queries grow cards).
 * Raised for the llm_wiki card layout (rounded bordered card + multi-line wrapping
 * description + inter-card gap) which is taller than the old dense flat row.
 */
const ROW_ESTIMATE = 200;

type Translate = TFunction;

const REVIEW_ITEM_TYPES: ReviewItemType[] = [
  "missing-page",
  "suggestion",
  "contradiction",
  "duplicate",
  "confirm",
  "purpose-suggestion",
  "schema-suggestion",
];
const REVIEW_ORIGINS: ReviewProposalOrigin[] = ["rule", "ai", "corpus", "system", "lint", "legacy"];
const REVIEW_PAGE_TYPES: PageType[] = [
  "concept",
  "entity",
  "source",
  "synthesis",
  "comparison",
  "query",
];

function formatRelativeTime(value: string, lang: string): string {
  const date = new Date(value);
  const diffMs = date.getTime() - Date.now();
  if (!Number.isFinite(diffMs)) return "";
  const formatter = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  const minutes = Math.round(diffMs / 60_000);
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(diffMs / 3_600_000);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  return date.toLocaleDateString(lang);
}

// ─── Proposal type badge ──────────────────────────────────────────────────────
// Light-theme: use --syn-* semantic tokens.
// color values are concrete hex so the bg alpha tint can be inline-computed.
// These match --syn-green, --syn-amber, --syn-red + --syn-type-* tokens.

// UXA-03: use var(--syn-mix-base) instead of literal white so dark-mode color-mix
// resolves against the dark surface rather than #ffffff. Token defined in ADR-0048 / theme.css.
const ITEM_TYPE_COLORS: Record<string, { color: string; bg: string }> = {
  // v1.3.14 icon-colour parity with llm_wiki 0.6.0: missing-page=purple, suggestion=green.
  "missing-page": {
    color: "#8250df",
    bg: "color-mix(in srgb, #8250df 10%, var(--syn-mix-base) 90%)",
  }, // purple — llm_wiki missing-page
  suggestion: { color: "#1a7f37", bg: "color-mix(in srgb, #1a7f37 10%, var(--syn-mix-base) 90%)" }, // green — llm_wiki suggestion
  // llm_wiki review-view.tsx:28-30 parity: contradiction=amber, duplicate=blue (was red/teal).
  contradiction: {
    color: "#d97706",
    bg: "color-mix(in srgb, #d97706 10%, var(--syn-mix-base) 90%)",
  }, // amber — llm_wiki contradiction
  duplicate: { color: "#3b82f6", bg: "color-mix(in srgb, #3b82f6 10%, var(--syn-mix-base) 90%)" }, // blue — llm_wiki duplicate
  // confirm: llm_wiki uses neutral foreground (near-black); Synapse keeps the brand accent per the
  // never-black brand policy (substitute --syn-accent for llm_wiki black elements). Flagged for review.
  confirm: { color: "#2563eb", bg: "color-mix(in srgb, #2563eb 10%, var(--syn-mix-base) 90%)" }, // --syn-accent (brand never-black)
  // R5 — real bug fix: backend may emit these two types; previously fell to grey fallback.
  "purpose-suggestion": {
    color: "#9a6700",
    bg: "color-mix(in srgb, #9a6700 10%, var(--syn-mix-base) 90%)",
  }, // --syn-amber (purpose-level suggestion)
  "schema-suggestion": {
    color: "#0969da",
    bg: "color-mix(in srgb, #0969da 10%, var(--syn-mix-base) 90%)",
  }, // --syn-blue (schema-level suggestion)
};

// R1 — Per-type Lucide icon mapping (llm_wiki parity).
// Icon + colour replace the plain-text pill; accessible label still delivered via
// title= + sr-only span so screen-readers and test queries still work.
const ITEM_TYPE_ICONS: Record<string, LucideIcon> = {
  // llm_wiki review-view.tsx:28-32 icon parity: missing-page=FileQuestion, confirm=MessageSquare.
  "missing-page": FileQuestion,
  suggestion: Lightbulb,
  contradiction: AlertTriangle,
  duplicate: Copy,
  confirm: MessageSquare,
  "purpose-suggestion": Target,
  "schema-suggestion": FileCog,
};

// ─── Item type icon (R1 — llm_wiki parity) ───────────────────────────────────
// Replaces the plain text pill with a small coloured Lucide icon.
// Accessibility: title= + aria-label on the wrapper + sr-only text inside ensure
// the type name is always in the accessibility tree and in textContent (UXA-18
// tests query .syn-chip textContent to confirm the human-readable label).

interface ItemTypeIconProps {
  itemType: string;
  t: Translate;
}

function ItemTypeIcon({ itemType, t }: ItemTypeIconProps) {
  // UXA-18: backend may send underscore form (e.g. "missing_page");
  // translation keys and icon keys use kebab-case. Normalise before lookup.
  const normalised = itemType.replace(/_/g, "-");
  const { color, bg } = ITEM_TYPE_COLORS[normalised] ??
    ITEM_TYPE_COLORS[itemType] ?? {
      color: "var(--syn-text-dim)",
      bg: "var(--syn-surface-hover)",
    };
  const label = t(`review.itemType.${normalised}`);
  const Icon: LucideIcon = ITEM_TYPE_ICONS[normalised] ?? ITEM_TYPE_ICONS[itemType] ?? HelpCircle;

  return (
    <span
      className="syn-chip"
      title={label}
      aria-label={label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 22,
        height: 22,
        flexShrink: 0,
        color,
        background: bg,
        border: `1px solid color-mix(in srgb, ${color} 30%, transparent 70%)`,
        borderRadius: "var(--syn-radius-pill)",
        padding: 0,
      }}
    >
      {/* aria-hidden: accessible name comes from the wrapper aria-label + sr-only below */}
      <Icon size={12} aria-hidden="true" />
      {/* sr-only text keeps textContent = label for tests + screen-reader announcements */}
      <span
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          padding: 0,
          margin: -1,
          overflow: "hidden",
          clip: "rect(0,0,0,0)",
          whiteSpace: "nowrap",
          border: 0,
        }}
      >
        {label}
      </span>
    </span>
  );
}

/** Back-compat alias so any code still referencing ItemTypeBadge compiles. */
const ItemTypeBadge = ItemTypeIcon;

// ─── Page type chip ───────────────────────────────────────────────────────────

interface PageTypeChipProps {
  pageType: string;
  t: Translate;
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

type QueryQuality = "absent" | "titleOnly" | "contextual";

function getQueryQuality(item: ReviewItem): QueryQuality {
  const queries = item.search_queries?.map((query) => query.trim()).filter(Boolean) ?? [];
  if (queries.length === 0) return "absent";
  const title = item.proposed_title?.trim().toLocaleLowerCase() ?? "";
  if (title && queries.every((query) => query.toLocaleLowerCase() === title)) return "titleOnly";
  return "contextual";
}

interface ProposalMetadataProps {
  item: ReviewItem;
  t: Translate;
}

function ProposalMetadata({ item, t }: ProposalMetadataProps) {
  const origin = item.proposal_origin ?? "legacy";
  const quality = getQueryQuality(item);
  return (
    <div className="review-proposal-metadata">
      <span data-testid="review-origin" className="syn-chip review-metadata-chip">
        {t(`review.origin.${origin}`)}
      </span>
      {item.proposed_page_type && (
        <span data-testid="review-proposed-type" className="review-type-trace">
          {t("review.proposedType")}: <PageTypeChip pageType={item.proposed_page_type} t={t} />
        </span>
      )}
      {item.created_page_type && (
        <span data-testid="review-created-type" className="review-type-trace">
          {t("review.createdType")}: <PageTypeChip pageType={item.created_page_type} t={t} />
        </span>
      )}
      <span data-testid="review-query-quality" className="syn-chip review-metadata-chip">
        {t(`review.queryQuality.${quality}`)}
      </span>
    </div>
  );
}

// ─── Resolution status badge (WS-B) ──────────────────────────────────────────

/**
 * Maps a terminal ReviewItemStatus to its badge appearance.
 * "pending" is not terminal; all other statuses are.
 */
const STATUS_BADGE_STYLE: Partial<Record<ReviewItemStatus, { color: string; bg: string }>> = {
  auto_resolved: {
    color: "#2563eb",
    bg: "color-mix(in srgb, #2563eb 10%, var(--syn-mix-base) 90%)",
  },
  created: { color: "#1a7f37", bg: "color-mix(in srgb, #1a7f37 10%, var(--syn-mix-base) 90%)" },
  deep_researched: {
    color: "#9a6700",
    bg: "color-mix(in srgb, #9a6700 10%, var(--syn-mix-base) 90%)",
  },
  skipped: { color: "var(--syn-text-dim)", bg: "var(--syn-surface-hover)" },
  dismissed: { color: "var(--syn-text-dim)", bg: "var(--syn-surface-hover)" },
};

interface ResolutionBadgeProps {
  status: ReviewItemStatus;
  t: Translate;
}

function ResolutionBadge({ status, t }: ResolutionBadgeProps) {
  const style = STATUS_BADGE_STYLE[status] ?? {
    color: "var(--syn-text-dim)",
    bg: "var(--syn-surface-hover)",
  };
  const label = t(`review.statusBadge.${status}`);
  return (
    <span
      data-testid={`review-status-badge-${status}`}
      className="syn-chip"
      style={{
        fontSize: 10,
        fontWeight: 700,
        color: style.color,
        background: style.bg,
        border: `1px solid color-mix(in srgb, ${typeof style.color === "string" && style.color.startsWith("var") ? "currentColor" : style.color} 30%, transparent 70%)`,
        borderRadius: "var(--syn-radius-pill)",
        padding: "1px 6px",
        textTransform: "uppercase",
        letterSpacing: "0.03em",
      }}
    >
      {label}
    </span>
  );
}

/** Returns true only when the item requires action (still pending). */
function isPending(status: ReviewItemStatus): boolean {
  return status === "pending";
}

// ─── Referenced page chip (ADR-0044 §7) ─────────────────────────────────────

interface ReferencedPageChipProps {
  page: ReviewReferencedPage;
  onClick: (pageId: string) => void;
  t: Translate;
}

function ReferencedPageChip({ page, onClick, t }: ReferencedPageChipProps) {
  return (
    <button
      data-testid="referenced-page-chip"
      onClick={() => onClick(page.id)}
      title={t("review.openPage", { title: page.title })}
      aria-label={t("review.openPage", { title: page.title })}
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
  variant: "create" | "approve" | "skip" | "dismiss" | "deep-research";
  /**
   * Visual weight. "primary" renders the filled accent CTA (llm_wiki review parity:
   * the leading Deep-Research action is the dark/filled primary button — Synapse
   * substitutes the brand accent per the never-black policy). Default "secondary"
   * keeps the ghost/outline appearance for Create / Approve / Skip.
   */
  emphasis?: "primary" | "secondary";
}

/**
 * ActionButton — review queue per-item action (Create / Skip / Dismiss / Deep-Research).
 * UXB-2 AC-UXB2-2: uses .syn-btn .syn-btn--secondary .syn-btn--sm as base.
 * Variant-specific color overrides are applied via inline style only for the color/border
 * (appearance tokens only — layout stays in the class).
 */
function ActionButton({
  label,
  onClick,
  disabled,
  loading,
  variant,
  emphasis = "secondary",
}: ActionButtonProps) {
  // Map variant → token-safe inline color overrides (only when enabled).
  // These narrow the secondary ghost base to the variant's semantic color.
  const VARIANT_STYLE: Record<string, { color: string; borderColor: string }> = {
    create: {
      color: "var(--syn-green)",
      borderColor: "color-mix(in srgb, var(--syn-green) 30%, var(--syn-border) 70%)",
    },
    approve: {
      color: "var(--syn-accent)",
      borderColor: "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)",
    },
    skip: { color: "var(--syn-text-muted)", borderColor: "var(--syn-border)" },
    dismiss: { color: "var(--syn-text-dim)", borderColor: "var(--syn-border)" },
    "deep-research": {
      color: "var(--syn-accent)",
      borderColor: "color-mix(in srgb, var(--syn-accent) 30%, var(--syn-border) 70%)",
    },
  };
  const fallbackStyle = { color: "var(--syn-text-muted)", borderColor: "var(--syn-border)" };
  const variantStyle = VARIANT_STYLE[variant] ?? fallbackStyle;
  const isDisabled = disabled || loading;
  const isPrimary = emphasis === "primary";
  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      aria-label={label}
      aria-busy={loading}
      data-testid={`review-action-${variant}`}
      // Primary → filled accent CTA (never-black brand); secondary → ghost/outline.
      className={`syn-btn ${isPrimary ? "syn-btn--primary" : "syn-btn--secondary"} syn-btn--sm`}
      // The filled primary already carries its own accent bg + white text — no per-variant
      // color override (that would fight the fill). Only ghost/secondary buttons get tinted.
      style={
        isDisabled || isPrimary
          ? undefined
          : { color: variantStyle.color, borderColor: variantStyle.borderColor }
      }
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
  inFlight: "create" | "approve" | "skip" | "dismiss" | "deep-research" | null | undefined;
  actionError: string | null | undefined;
  generationError: string | null | undefined;
  isSelected: boolean;
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

function ReviewRow({
  item,
  style,
  measureRef,
  inFlight,
  actionError,
  generationError,
  isSelected,
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
}: ReviewRowProps) {
  const isAnyInFlight = inFlight !== null && inFlight !== undefined;
  const isCreating = inFlight === "create";
  const isApproving = inFlight === "approve";
  const isItemPending = isPending(item.status);

  // R2: confirm + contradiction use "Approve" (acknowledge/resolve), not "Create" (generate page).
  // Normalise so backend underscore variants ("confirm", "contradiction") work too.
  const normalisedType = item.item_type.replace(/_/g, "-");
  // R2 (v1.3.14): "Approve" (acknowledge → mark-resolved) only for `confirm` items.
  // `contradiction` keeps "Create" so the user can author a resolution page.
  const isApproveType = normalisedType === "confirm";

  const relativeTime = formatRelativeTime(item.created_at, lang);

  const resolvedAtLabel = (() => {
    if (item.reviewed_at == null) return null;
    try {
      const dateStr = formatRelativeTime(item.reviewed_at, lang);
      return t("review.resolvedAt").replace("{{date}}", dateStr);
    } catch {
      return null;
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
    item.search_queries != null && item.search_queries.length > 0 ? item.search_queries : null;

  // ── Resolved / dismissed read-only card (WS-B) ──────────────────────────────
  // Non-pending items get a distinct read-only presentation:
  //  - resolution badge (auto_resolved / created / deep_researched / …)
  //  - reviewed_at timestamp
  //  - link to created page when created_page_id is present
  //  - NO Crea / Salta / Ignora / Ricerca Profonda buttons
  if (!isItemPending) {
    return (
      <div
        ref={measureRef}
        data-testid="review-item-row"
        data-item-id={item.id}
        data-status={item.status}
        className="review-card-wrapper"
        style={{ ...style, padding: "0 16px 10px", boxSizing: "border-box" }}
      >
        {/* Resolved cards reuse the llm_wiki card shell, dimmed + sunken to read as read-only. */}
        <div
          style={{
            border: "1px solid var(--syn-border)",
            borderRadius: "var(--syn-radius-md)",
            background: "var(--syn-surface-sunken)",
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            opacity: 0.85,
          }}
        >
          {/* Row 1: type badge + resolution badge + title + resolved timestamp */}
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8, minWidth: 0 }}>
            <span style={{ marginTop: 1, flexShrink: 0 }}>
              <ItemTypeBadge itemType={item.item_type} t={t} />
            </span>
            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  lineHeight: 1.35,
                  color: "var(--syn-text-muted)",
                  overflowWrap: "anywhere",
                }}
                title={item.proposed_title ?? item.page_title ?? ""}
              >
                {item.proposed_title ?? item.page_title ?? t("review.noTitle")}
              </span>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <ResolutionBadge status={item.status} t={t} />
              </div>
              <ProposalMetadata item={item} t={t} />
            </div>
            {resolvedAtLabel && (
              <span
                data-testid="review-resolved-at"
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-dim)",
                  flexShrink: 0,
                  marginTop: 2,
                  whiteSpace: "nowrap",
                }}
                title={item.reviewed_at ?? ""}
              >
                {resolvedAtLabel}
              </span>
            )}
            {!resolvedAtLabel && (
              <span
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-dim)",
                  flexShrink: 0,
                  marginTop: 2,
                }}
                title={item.created_at}
              >
                {relativeTime}
              </span>
            )}
          </div>

          {/* Row 2: rationale (multi-line, llm_wiki parity) */}
          <div
            style={{
              fontSize: 12,
              lineHeight: 1.5,
              color: "var(--syn-text-dim)",
              fontStyle: item.rationale ? "normal" : "italic",
              overflowWrap: "anywhere",
              whiteSpace: "pre-wrap",
            }}
          >
            {item.rationale ?? t("review.noRationale")}
          </div>

          {/* Row 3: conflict page (contradiction / duplicate) */}
          {showConflictPage && (
            <div
              style={{ fontSize: 11, color: "var(--syn-text-dim)", overflowWrap: "anywhere" }}
              title={item.page_title ?? ""}
            >
              {t("review.conflictsWith")}: <em>{item.page_title}</em>
            </div>
          )}

          {/* Row 4: link to created page (when status=created and created_page_id present) */}
          {item.created_page_id != null && (
            <div>
              <button
                data-testid="review-view-created-page"
                onClick={() => {
                  if (item.created_page_id != null) onOpenCreatedPage(item.created_page_id);
                }}
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
                {t("review.viewCreatedPage")}
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Pending card (full actionable card) ──────────────────────────────────────
  // llm_wiki review-view.tsx card parity: discrete rounded/bordered card with a soft shadow,
  // vertical spacing between cards (via the wrapper's bottom padding — measured by the
  // virtualizer so heights stay correct), a larger bold title, and a MULTI-LINE description
  // (no single-line ellipsis truncation). Replaces the previous dense flat-row layout.
  return (
    <div
      ref={measureRef}
      data-testid="review-item-row"
      data-item-id={item.id}
      data-status={item.status}
      className="review-card-wrapper"
      style={{
        ...style,
        // Horizontal gutter + bottom gap between cards (the airy llm_wiki list rhythm).
        padding: "0 16px 10px",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          border: isSelected
            ? "1px solid var(--syn-accent)"
            : generationError
              ? "1px solid color-mix(in srgb, var(--syn-red) 40%, var(--syn-border) 60%)"
              : "1px solid var(--syn-border)",
          borderRadius: "var(--syn-radius-md)",
          background: isSelected
            ? "var(--syn-accent-soft)"
            : generationError
              ? "color-mix(in srgb, var(--syn-red) 6%, var(--syn-mix-base) 94%)"
              : "var(--syn-surface)",
          boxShadow: "var(--syn-shadow-soft)",
          padding: "12px 14px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {/* Row 1: checkbox + type icon + proposed_title + timestamp + ✕ dismiss (R1, R3) */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8, minWidth: 0 }}>
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect(item.id)}
            aria-label={t("review.selectItem", { title: item.proposed_title ?? item.id })}
            data-testid={`review-select-${item.id}`}
            style={{
              flexShrink: 0,
              cursor: "pointer",
              accentColor: "var(--syn-accent)",
              marginTop: 3,
            }}
          />
          {/* R1: coloured Lucide icon instead of text pill; label in title + sr-only */}
          <span style={{ marginTop: 1, flexShrink: 0 }}>
            <ItemTypeIcon itemType={item.item_type} t={t} />
          </span>
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
            <span
              style={{
                fontSize: 14,
                fontWeight: 650,
                lineHeight: 1.35,
                color: "var(--syn-text)",
                overflowWrap: "anywhere",
              }}
              title={item.proposed_title ?? item.page_title ?? ""}
            >
              {item.proposed_title ?? item.page_title ?? t("review.noTitle")}
            </span>
            <ProposalMetadata item={item} t={t} />
          </div>
          <span
            style={{
              fontSize: 10,
              color: "var(--syn-text-dim)",
              flexShrink: 0,
              marginTop: 2,
              whiteSpace: "nowrap",
            }}
            title={item.created_at}
          >
            {relativeTime}
          </span>
          {/* R3: ✕ dismiss at top-right of card header (llm_wiki parity) */}
          <button
            data-testid="review-action-dismiss"
            onClick={() => onDismiss(item.id)}
            disabled={isAnyInFlight}
            aria-label={t("review.dismiss")}
            title={t("review.dismiss")}
            style={{
              flexShrink: 0,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 22,
              height: 22,
              padding: 0,
              border: "none",
              borderRadius: "var(--syn-radius-sm)",
              background: "transparent",
              color: "var(--syn-text-dim)",
              cursor: isAnyInFlight ? "not-allowed" : "pointer",
              opacity: isAnyInFlight ? 0.4 : 0.6,
            }}
            onMouseEnter={(e) => {
              if (!isAnyInFlight) (e.currentTarget as HTMLElement).style.opacity = "1";
            }}
            onMouseLeave={(e) => {
              if (!isAnyInFlight) (e.currentTarget as HTMLElement).style.opacity = "0.6";
            }}
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>

        {/* Row 2: rationale (why this matters) — MULTI-LINE (llm_wiki parity, no truncation) */}
        <div
          style={{
            fontSize: 12.5,
            lineHeight: 1.5,
            color: item.rationale ? "var(--syn-text-muted)" : "var(--syn-border)",
            fontStyle: item.rationale ? "normal" : "italic",
            overflowWrap: "anywhere",
            whiteSpace: "pre-wrap",
          }}
        >
          {item.rationale ?? t("review.noRationale")}
        </div>

        {/* Row 3: conflict page (contradiction / duplicate) */}
        {showConflictPage && (
          <div
            style={{
              fontSize: 11,
              color: "var(--syn-type-concept)", // --syn-type-concept (purple) for conflict link
              overflowWrap: "anywhere",
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
              style={{
                fontSize: 11,
                color: "var(--syn-text-dim)",
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              {t("review.referencedPages")}:
            </span>
            {referencedPages.map((rp) => (
              <ReferencedPageChip key={rp.id} page={rp} onClick={onOpenPage} t={t} />
            ))}
          </div>
        )}

        {/* Row 5: search queries line (ADR-0044 §6.1) */}
        {searchQueries && (
          <div
            data-testid="search-queries-row"
            style={{
              fontSize: 11,
              lineHeight: 1.45,
              color: "var(--syn-text-dim)",
              overflowWrap: "anywhere",
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
              fontSize: 11,
              color: "var(--syn-red)",
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <span style={{ flex: 1, overflowWrap: "anywhere" }}>{t("review.createFailed")}</span>
            <button
              onClick={() => onDismissGenerationError(item.id)}
              style={{
                fontSize: 11,
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
        {/* R2: confirm + contradiction get "Approve" (acknowledge/resolve); others get "Create". */}
        {/* R3: Dismiss moved to ✕ icon at top-right of Row 1 — removed from here. */}
        {/* llm_wiki review-view.tsx button order + emphasis parity: the leading action is
            Deep Research (filled primary), then Create/Approve, then Skip. Deep Research is
            only offered on suggestion + missing-page; when absent, Create/Approve leads. */}
        <div
          className="review-card-actions"
          style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 2 }}
        >
          {/* Deep Research — FIRST + filled primary (llm_wiki parity), suggestion/missing-page only */}
          {(item.item_type === "suggestion" || item.item_type === "missing-page") && (
            <ActionButton
              label={inFlight === "deep-research" ? t("common.loading") : t("review.deepResearch")}
              onClick={() => onDeepResearch(item.id)}
              disabled={isAnyInFlight}
              variant="deep-research"
              emphasis="primary"
            />
          )}
          {isApproveType ? (
            <ActionButton
              label={isApproving ? t("review.approving") : t("review.approve")}
              onClick={() => onApprove(item.id)}
              disabled={isAnyInFlight}
              loading={isApproving}
              variant="approve"
            />
          ) : (
            <ActionButton
              label={isCreating ? t("review.creating") : t("review.create")}
              onClick={() => onCreate(item.id)}
              disabled={isAnyInFlight}
              loading={isCreating}
              variant="create"
            />
          )}
          <ActionButton
            label={inFlight === "skip" ? t("common.loading") : t("review.skip")}
            onClick={() => onSkip(item.id)}
            disabled={isAnyInFlight}
            variant="skip"
          />

          {actionError && !generationError && (
            <span role="alert" style={{ fontSize: 11, color: "var(--syn-red)", marginLeft: 4 }}>
              {actionError}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Item list (virtualised — I4) ─────────────────────────────────────────────

interface ReviewItemListProps {
  vaultId: string;
  onOpenSources: () => void;
  onOpenPage: (pageId: string) => void;
  onOpenCreatedPage: (pageId: string) => void;
}

function ReviewItemList({
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
      id={testId}
      role="tab"
      aria-selected={active}
      aria-controls="review-tabpanel"
      tabIndex={active ? 0 : -1}
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
  const selectPage = useGraphStore(selectSelectPage);

  const fetchFresh = useReviewStore(selectFetchFreshReview);
  const total = useReviewStore(selectReviewTotal);
  const loading = useReviewStore(selectReviewLoading);
  const error = useReviewStore(selectReviewError);
  const activeTab = useReviewStore(selectActiveTab);
  const filters = useReviewStore(useShallow(selectReviewFilters));
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
  const setFilters = useReviewStore(selectSetReviewFilters);
  const clearFilters = useReviewStore(selectClearReviewFilters);
  const bulkAction = useReviewStore(selectBulkAction);
  const clearResolvedRows = useReviewStore(selectClearResolvedRows);
  const selectAllPending = useReviewStore(selectSelectAllPending);
  const clearSelection = useReviewStore(selectClearSelection);
  const viewport = useViewport();
  const [researchDrawerOpen, setResearchDrawerOpen] = useState(false);

  const effectiveVaultId = vaultId ?? "default";
  const selectionCount = selectedIds.size;
  const hasSelection = selectionCount > 0;
  const showClearResolved = activeTab === "resolved" || activeTab === "dismissed";
  const hasFilters = Object.values(filters).some((value) => value !== null);

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

  const handleTabKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    if (tabs.length === 0) return;
    const currentIndex = Math.max(0, tabs.indexOf(document.activeElement as HTMLButtonElement));
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    event.preventDefault();
    tabs[nextIndex]?.focus();
    tabs[nextIndex]?.click();
  }, []);

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
      // Same pattern as SearchView: select the page in the tree, then switch section.
      selectPage(pageId, "tree");
      setActiveSection("pages");
    },
    [selectPage, setActiveSection],
  );

  // Open the page that was created from a resolved review item (WS-B).
  // Same navigation pattern as handleOpenPage — selects the page, then navigates.
  const handleOpenCreatedPage = useCallback(
    (pageId: string) => {
      selectPage(pageId, "tree");
      setActiveSection("pages");
    },
    [selectPage, setActiveSection],
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
        className="review-header"
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
              aria-label={t("review.pendingCount", { count: total })}
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
          aria-orientation="horizontal"
          aria-label={t("review.statusTabsAria")}
          onKeyDown={handleTabKeyDown}
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

        {viewport !== "desktop" && (
          <button
            type="button"
            data-testid="review-open-research"
            className="syn-toolbar-button"
            aria-label={t("review.deepResearchPanel.open")}
            aria-expanded={researchDrawerOpen}
            onClick={() => setResearchDrawerOpen(true)}
          >
            {t("review.deepResearchPanel.panelTitle")}
          </button>
        )}
      </div>

      {/* ── Server-side v1.6 proposal filters ─────────────────────────── */}
      <div className="review-filter-bar" data-testid="review-filter-bar">
        <label className="review-filter-field">
          <span>{t("review.filters.itemType")}</span>
          <select
            data-testid="review-filter-item-type"
            value={filters.itemType ?? ""}
            onChange={(event) =>
              void setFilters(
                { itemType: (event.target.value || null) as ReviewItemType | null },
                effectiveVaultId,
              )
            }
          >
            <option value="">{t("review.filters.all")}</option>
            {REVIEW_ITEM_TYPES.map((itemType) => (
              <option key={itemType} value={itemType}>
                {t(`review.itemType.${itemType}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="review-filter-field">
          <span>{t("review.filters.origin")}</span>
          <select
            data-testid="review-filter-origin"
            value={filters.proposalOrigin ?? ""}
            onChange={(event) =>
              void setFilters(
                { proposalOrigin: (event.target.value || null) as ReviewProposalOrigin | null },
                effectiveVaultId,
              )
            }
          >
            <option value="">{t("review.filters.all")}</option>
            {REVIEW_ORIGINS.map((origin) => (
              <option key={origin} value={origin}>
                {t(`review.origin.${origin}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="review-filter-field">
          <span>{t("review.filters.pageType")}</span>
          <select
            data-testid="review-filter-page-type"
            value={filters.proposedPageType ?? ""}
            onChange={(event) =>
              void setFilters(
                { proposedPageType: (event.target.value || null) as PageType | null },
                effectiveVaultId,
              )
            }
          >
            <option value="">{t("review.filters.all")}</option>
            {REVIEW_PAGE_TYPES.map((pageType) => (
              <option key={pageType} value={pageType}>
                {t(`review.pageType.${pageType}`)}
              </option>
            ))}
          </select>
        </label>
        {hasFilters && (
          <button
            type="button"
            className="syn-btn syn-btn--ghost syn-btn--sm"
            onClick={() => void clearFilters(effectiveVaultId)}
          >
            {t("review.filters.clear")}
          </button>
        )}
      </div>

      {/* ── Selection bar + bulk action bar (ADR-0044 §7) ───────────────── */}
      <div
        className="review-selection-bar"
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
              style={{
                color: "var(--syn-green)",
                borderColor: "color-mix(in srgb, var(--syn-green) 30%, var(--syn-border) 70%)",
              }}
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
          <span style={{ fontSize: 12, flex: 1 }}>{t("review.searxngUnavailable")}</span>
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

      {/* ── Main content: virtualised item list + Deep Research panel (R4) ── */}
      <div
        id="review-tabpanel"
        role="tabpanel"
        aria-labelledby={`review-tab-${activeTab}`}
        className="review-content"
        style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex" }}
      >
        {/* Item list occupies remaining horizontal space (I4 — always virtualised) */}
        <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          <ReviewItemList
            vaultId={effectiveVaultId}
            onOpenSources={handleOpenSources}
            onOpenPage={handleOpenPage}
            onOpenCreatedPage={handleOpenCreatedPage}
          />
        </div>

        {/* R4: Persistent Deep Research panel (right side, llm_wiki parity).
            Receives lastResearchRunId so it auto-refreshes when a per-item
            "Ricerca Profonda" action completes. Same researchStore as the
            "Ricerca Profonda" rail section — superset, NOT a replacement. */}
        {viewport === "desktop" && (
          <ReviewDeepResearchPanel
            vaultId={effectiveVaultId}
            lastResearchRunId={lastDeepResearch?.runId ?? null}
          />
        )}
      </div>

      {viewport !== "desktop" && researchDrawerOpen && (
        <PanelDrawer
          open
          side="right"
          label={t("review.deepResearchPanel.panelTitle")}
          onClose={() => setResearchDrawerOpen(false)}
        >
          <ReviewDeepResearchPanel
            vaultId={effectiveVaultId}
            lastResearchRunId={lastDeepResearch?.runId ?? null}
            onClose={() => setResearchDrawerOpen(false)}
          />
        </PanelDrawer>
      )}
    </div>
  );
}
