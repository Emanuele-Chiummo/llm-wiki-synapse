/**
 * ReviewBadges.tsx — presentational badges/chips for the F9 Review Queue
 * (ADR-0034 §7.1 + ADR-0044 Phase D). Extracted from ReviewQueueView.tsx
 * (FE-ARCH-1/FE-TEST-10 — mechanical move, no behavior change).
 *
 * ItemTypeIcon (+ ItemTypeBadge alias) · PageTypeChip · ProposalMetadata ·
 * ResolutionBadge · ReferencedPageChip · formatRelativeTime · isPending.
 */

import {
  HelpCircle,
  FileQuestion,
  Lightbulb,
  AlertTriangle,
  Copy,
  MessageSquare,
  Target,
  FileCog,
  type LucideIcon,
} from "lucide-react";
import type { TFunction } from "i18next";
import { pageTypeCssColor } from "../../utils/pageTypeVisuals";
import type { ReviewItem, ReviewItemStatus, ReviewReferencedPage } from "../../api/types";

export type Translate = TFunction;

// ─── Relative time formatting ─────────────────────────────────────────────────

export function formatRelativeTime(value: string, lang: string): string {
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

// Token-based item-type colors (v1.7.0 — replaces hardcoded hex).
// Each item type maps to the nearest --syn-type-* or semantic token.
// color-mix() with var() tokens is valid in modern browsers (CSS Color 5).
// --syn-mix-base resolves to white (light) or dark surface (dark) per ADR-0048.
const ITEM_TYPE_COLORS: Record<string, { color: string; bg: string }> = {
  // missing-page → concept violet (closest to llm_wiki's purple #8250df)
  "missing-page": {
    color: "var(--syn-type-concept)",
    bg: "color-mix(in srgb, var(--syn-type-concept) 10%, var(--syn-mix-base) 90%)",
  },
  // suggestion → success green (llm_wiki #1a7f37 ≈ --syn-green)
  suggestion: {
    color: "var(--syn-green)",
    bg: "color-mix(in srgb, var(--syn-green) 10%, var(--syn-mix-base) 90%)",
  },
  // contradiction → query amber (llm_wiki #d97706 ≈ --syn-type-query)
  contradiction: {
    color: "var(--syn-type-query)",
    bg: "color-mix(in srgb, var(--syn-type-query) 10%, var(--syn-mix-base) 90%)",
  },
  // duplicate → entity blue (llm_wiki #3b82f6 = --syn-type-entity)
  duplicate: {
    color: "var(--syn-type-entity)",
    bg: "color-mix(in srgb, var(--syn-type-entity) 10%, var(--syn-mix-base) 90%)",
  },
  // confirm → brand accent (never-black brand policy: substitute --syn-accent)
  confirm: {
    color: "var(--syn-accent)",
    bg: "color-mix(in srgb, var(--syn-accent) 10%, var(--syn-mix-base) 90%)",
  },
  // purpose-suggestion → amber (purpose-level semantic)
  "purpose-suggestion": {
    color: "var(--syn-amber)",
    bg: "color-mix(in srgb, var(--syn-amber) 10%, var(--syn-mix-base) 90%)",
  },
  // schema-suggestion → synthesis indigo (structural/schema semantic)
  "schema-suggestion": {
    color: "var(--syn-type-synthesis)",
    bg: "color-mix(in srgb, var(--syn-type-synthesis) 10%, var(--syn-mix-base) 90%)",
  },
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

export function ItemTypeIcon({ itemType, t }: ItemTypeIconProps) {
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
export const ItemTypeBadge = ItemTypeIcon;

// ─── Page type chip ───────────────────────────────────────────────────────────

interface PageTypeChipProps {
  pageType: string;
  t: Translate;
}

export function PageTypeChip({ pageType, t }: PageTypeChipProps) {
  const label = t(`review.pageType.${pageType}`) ?? pageType;
  const color = pageTypeCssColor(pageType);
  return (
    <span
      className="syn-chip"
      style={{
        fontSize: 9,
        fontWeight: 500,
        padding: "0 5px",
        color,
        background: `color-mix(in srgb, ${color} 10%, var(--syn-mix-base) 90%)`,
        borderColor: `color-mix(in srgb, ${color} 30%, transparent 70%)`,
      }}
    >
      {label}
    </span>
  );
}

export type QueryQuality = "absent" | "titleOnly" | "contextual";

export function getQueryQuality(item: ReviewItem): QueryQuality {
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

export function ProposalMetadata({ item, t }: ProposalMetadataProps) {
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
    color: "var(--syn-accent)",
    bg: "color-mix(in srgb, var(--syn-accent) 10%, var(--syn-mix-base) 90%)",
  },
  created: {
    color: "var(--syn-green)",
    bg: "color-mix(in srgb, var(--syn-green) 10%, var(--syn-mix-base) 90%)",
  },
  deep_researched: {
    color: "var(--syn-amber)",
    bg: "color-mix(in srgb, var(--syn-amber) 10%, var(--syn-mix-base) 90%)",
  },
  skipped: { color: "var(--syn-text-dim)", bg: "var(--syn-surface-hover)" },
  dismissed: { color: "var(--syn-text-dim)", bg: "var(--syn-surface-hover)" },
};

interface ResolutionBadgeProps {
  status: ReviewItemStatus;
  t: Translate;
}

export function ResolutionBadge({ status, t }: ResolutionBadgeProps) {
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
export function isPending(status: ReviewItemStatus): boolean {
  return status === "pending";
}

// ─── Referenced page chip (ADR-0044 §7) ─────────────────────────────────────

interface ReferencedPageChipProps {
  page: ReviewReferencedPage;
  onClick: (pageId: string) => void;
  t: Translate;
}

export function ReferencedPageChip({ page, onClick, t }: ReferencedPageChipProps) {
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
