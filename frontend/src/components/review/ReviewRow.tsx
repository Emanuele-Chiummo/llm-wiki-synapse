/**
 * ReviewRow.tsx — a single F9 Review Queue proposal card (pending or resolved
 * presentation). Extracted from ReviewQueueView.tsx (FE-ARCH-1/FE-TEST-10 —
 * mechanical move, no behavior change).
 */

import { useState, type CSSProperties } from "react";
import { ConfirmDialog } from "../common/ConfirmDialog";
import type { ReviewItem } from "../../api/types";
import {
  type Translate,
  formatRelativeTime,
  isPending,
  ItemTypeIcon,
  ItemTypeBadge,
  ProposalMetadata,
  ResolutionBadge,
  ReferencedPageChip,
} from "./ReviewBadges";
import { ActionButton } from "./ReviewActionButton";

/**
 * Returns a short evidence string for the decision-trace row (v1.7.0).
 * Priority: conflicting page_title → first referenced page → first search query → "—".
 */
export function getTraceEvidence(item: ReviewItem): string {
  if (item.page_title != null) return item.page_title;
  if (item.referenced_pages != null && item.referenced_pages.length > 0) {
    return item.referenced_pages.map((p) => p.title).join(", ");
  }
  if (item.search_queries != null && item.search_queries.length > 0) {
    return item.search_queries[0] ?? "—";
  }
  return "—";
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

export function ReviewRow({
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
  // Dismiss confirm dialog state (UXA-10 — irreversible action guard)
  const [showDismissConfirm, setShowDismissConfirm] = useState(false);

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

  // ── Pending card (decision-trace card, v1.7.0) ─────────────────────────────
  // Structure: .r-top (checkbox + page-type badge + title + rationale + metadata)
  //           → .trace (4 labelled mono steps)
  //           → data rows (conflict · referenced pages · search queries · error)
  //           → .r-actions (Create/Approve primary · Deep Research · Skip · [spacer] · Dismiss danger)
  //           → ConfirmDialog (conditional — UXA-10 irreversible-dismiss guard)
  return (
    <div
      ref={measureRef}
      data-testid="review-item-row"
      data-item-id={item.id}
      data-status={item.status}
      className="review-card-wrapper"
      style={{
        ...style,
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
        {/* .r-top: checkbox + page-type badge + content (title + rationale + metadata) */}
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
          {/* Item-type badge: coloured Lucide icon using --syn-type-* tokens (F3 v1.7.0).
              proposed_page_type is shown in the .trace row below — not duplicated here. */}
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
            {/* Rationale (why this matters) — multi-line, no truncation */}
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
        </div>

        {/* .trace: 4 labelled mono steps — origin · evidence · proposed type · search */}
        <div
          style={{
            display: "flex",
            alignItems: "stretch",
            flexWrap: "wrap",
            background: "var(--syn-surface-sunken)",
            border: "1px solid var(--syn-border)",
            borderRadius: "var(--syn-radius-sm)",
            overflow: "hidden",
            fontSize: 11,
          }}
        >
          {[
            { key: "origin", value: item.proposal_origin ?? "legacy" },
            { key: "evidence", value: getTraceEvidence(item) },
            { key: "proposedType", value: item.proposed_page_type ?? "—" },
            { key: "search", value: item.search_queries?.[0] ?? "—" },
          ].map((step, idx) => (
            <div
              key={step.key}
              style={{
                flex: "1 1 80px",
                padding: "5px 8px",
                borderLeft: idx > 0 ? "1px solid var(--syn-border)" : "none",
              }}
            >
              <div
                style={{
                  fontFamily: "var(--syn-font-mono)",
                  fontSize: 9,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  color: "var(--syn-text-dim)",
                  marginBottom: 2,
                }}
              >
                {t(`review.trace.${step.key}`)}
              </div>
              <div
                style={{
                  color: "var(--syn-text-muted)",
                  overflowWrap: "anywhere",
                  lineHeight: 1.35,
                  maxHeight: "2.7em",
                  overflow: "hidden",
                }}
              >
                {step.value}
              </div>
            </div>
          ))}
        </div>

        {/* Conflict page (contradiction / duplicate) */}
        {showConflictPage && (
          <div
            style={{
              fontSize: 11,
              color: "var(--syn-type-concept)",
              overflowWrap: "anywhere",
            }}
            title={item.page_title ?? ""}
          >
            {t("review.conflictsWith")}: <em>{item.page_title}</em>
          </div>
        )}

        {/* Referenced pages chips (ADR-0044 §6.1) */}
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

        {/* Search queries line (ADR-0044 §6.1) */}
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

        {/* 502 generation error — retry-or-skip hint */}
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

        {/* .r-actions: Create/Approve (primary) · Deep Research · Skip · [spacer] · Dismiss (danger) */}
        <div
          className="review-card-actions"
          style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 2 }}
        >
          {/* Create / Approve — primary accent CTA */}
          {isApproveType ? (
            <ActionButton
              label={isApproving ? t("review.approving") : t("review.approve")}
              onClick={() => onApprove(item.id)}
              disabled={isAnyInFlight}
              loading={isApproving}
              variant="approve"
              emphasis="primary"
            />
          ) : (
            <ActionButton
              label={isCreating ? t("review.creating") : t("review.create")}
              onClick={() => onCreate(item.id)}
              disabled={isAnyInFlight}
              loading={isCreating}
              variant="create"
              emphasis="primary"
            />
          )}
          {/* Deep Research — ghost (suggestion/missing-page only) */}
          {(item.item_type === "suggestion" || item.item_type === "missing-page") && (
            <ActionButton
              label={inFlight === "deep-research" ? t("common.loading") : t("review.deepResearch")}
              onClick={() => onDeepResearch(item.id)}
              disabled={isAnyInFlight}
              variant="deep-research"
            />
          )}
          {/* Skip — ghost */}
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

          {/* Spacer pushes Dismiss to the far right */}
          <div style={{ flex: 1 }} />

          {/* Dismiss — danger-outline, always last; opens ConfirmDialog gate (UXA-10) */}
          <ActionButton
            label={t("review.dismiss")}
            onClick={() => setShowDismissConfirm(true)}
            disabled={isAnyInFlight}
            variant="dismiss"
          />
        </div>
      </div>

      {/* Dismiss confirm dialog — rendered outside the card so fixed overlay covers full viewport */}
      {showDismissConfirm && (
        <ConfirmDialog
          title={t("review.dismissConfirm.title")}
          body={t("review.dismissConfirm.body")}
          confirmLabel={t("review.dismiss")}
          cancelLabel={t("common.cancel")}
          danger
          onConfirm={() => {
            setShowDismissConfirm(false);
            onDismiss(item.id);
          }}
          onCancel={() => setShowDismissConfirm(false)}
        />
      )}
    </div>
  );
}
