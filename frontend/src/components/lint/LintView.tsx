/**
 * LintView.tsx — K2 Lint-fix section UI (ADR-0037 §6, B1).
 *
 * Layout (B1-aligned, matching nashsu/llm_wiki lint page):
 *   - Header: title + count badge + "Semantic (LLM)" checkbox [L8] + "Run Lint" + Refresh
 *   - Run-history cost line: last run's total_cost_usd at 4dp (I7) + status + timestamp
 *   - Scan error banner
 *   - Batch bar [L5]: "Select all" checkbox + "{n} selected" + Fix/Ignore/Send-to-Review buttons
 *   - Findings list grouped by severity (Errors / Warnings / Info) with sticky group headers [L7]
 *   - Per-finding row: checkbox + severity chip + category badge + target_title + description
 *     + green "Suggested target:" strip [L2] + proposed_action + Apply/Dismiss/Send-to-Review [L4/L6]
 *     + "Open" navigation button [L4] + "Delete" (orphan-page only, two-stage confirm) [L9]
 *   - Empty state when no open findings.
 *
 * INVARIANT I3: Zustand selectors + shallow equality. No store subscriptions on
 *   unrelated state. Descriptions displayed as plain text — no per-token parsing.
 * INVARIANT I4: findings list virtualised with TanStack Virtual always.
 *   Severity group headers are synthetic rows with distinct height (variable-size virtualiser).
 * INVARIANT I7: total_cost_usd rendered at 4dp; scan is bounded (max_iter/token_budget
 *   frozen by the backend before the scan starts). Batch cap = 200 (enforced by backend).
 * B1-K8: Delete = human double-confirm (armed-red pattern). Fixes stay human-gated.
 */

import { useEffect, useRef, useCallback, useState, type CSSProperties } from "react";
import {
  Unlink,
  Link2Off,
  ArrowUpRight,
  FileQuestion,
  AlertTriangle,
  Clock,
  Lightbulb,
  BrainCircuit,
  type LucideIcon,
} from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useLintStore,
  selectLintFindings,
  selectLintFindingsTotal,
  selectLintFindingsLoading,
  selectLintFindingsError,
  selectLintCurrentRun,
  selectLintScanning,
  selectLintScanError,
  selectLintActionInFlight,
  selectLintActionError,
  selectLintScan,
  selectLintApply,
  selectLintDismiss,
  selectLintRefresh,
  selectLintFetchMoreFindings,
  selectClearLintScanError,
  // B1 selectors
  selectLintSemanticEnabled,
  selectLintSelectedIds,
  selectLintBatchInFlight,
  selectLintSetSemanticEnabled,
  selectLintToggleSelect,
  selectLintSelectAll,
  selectLintClearSelection,
  selectLintApplyBatch,
  selectLintDismissBatch,
  selectLintSendToReviewBatch,
  selectLintSendToReview,
  selectLintDeleteOrphanPage,
  selectLintSeverityTotals,
} from "../../store/lintStore";
import {
  selectVaultId,
  selectSelectPage,
  selectSetActiveSection,
  useAppStore,
} from "../../store/appStore";
import { useProviderConfigured } from "../../hooks/useProviderConfigured";
import { EmptyState } from "../common/EmptyState";
import { ErrorState } from "../common/ErrorState";
import { Skeleton } from "../ui/Skeleton";
import { showToast } from "../common/Toast";
import type { LintFinding } from "../../api/types";
import { LINT_FLAG_ONLY_CATEGORIES } from "../../api/types";

// ─── Constants ────────────────────────────────────────────────────────────────

/**
 * Estimated height of a finding CARD (px). The virtualiser uses measureElement for the real
 * variable height (llm_wiki card layout: rounded box + multi-line description + green suggested-
 * target strip + inter-card gap), so this is only the initial estimate.
 */
const ROW_HEIGHT = 190;

/** Height of a severity group header row (px). */
const GROUP_HEADER_HEIGHT = 34;

/** Format cost at 4 decimal places (I7). */
function formatCost(usd: number): string {
  return `$${usd.toFixed(4)}`;
}

// ─── Virtual row types ────────────────────────────────────────────────────────

type SeverityLevel = "error" | "warning" | "info";

interface GroupHeaderRow {
  kind: "group-header";
  severity: SeverityLevel;
  count: number;
}

interface FindingRow_ {
  kind: "finding";
  finding: LintFinding;
}

type VirtualRow = GroupHeaderRow | FindingRow_;

/**
 * Build a flat list of virtual rows grouped error→warning→info with synthetic headers.
 *
 * L11: The group header count is the TRUE total from the backend (`severityTotals[sev]`)
 * when available, so it always reflects the full count — not just the loaded page.
 * Falls back to `items.length` (currently-loaded count) when `severityTotals` is absent
 * (pre-v0.6 backend).
 */
function buildVirtualRows(
  findings: LintFinding[],
  severityTotals: { error?: number; warning?: number; info?: number } | null,
): VirtualRow[] {
  const groups: Record<SeverityLevel, LintFinding[]> = {
    error: [],
    warning: [],
    info: [],
  };
  for (const f of findings) {
    const sev = (f.severity as SeverityLevel) in groups ? (f.severity as SeverityLevel) : "info";
    groups[sev].push(f);
  }

  const rows: VirtualRow[] = [];
  const order: SeverityLevel[] = ["error", "warning", "info"];
  for (const sev of order) {
    const items = groups[sev];
    if (items.length === 0) continue;
    // L11: prefer the true total; fall back to loaded-count when absent.
    const headerCount = severityTotals?.[sev] ?? items.length;
    rows.push({ kind: "group-header", severity: sev, count: headerCount });
    for (const f of items) {
      rows.push({ kind: "finding", finding: f });
    }
  }
  return rows;
}

// ─── Severity colors (shared: category icon + group header) ────────────────────

const SEVERITY_COLORS: Record<string, string> = {
  info: "var(--syn-text-muted)",
  warning: "var(--syn-amber)",
  error: "var(--syn-red)",
};

// ─── Category icon (llm_wiki lint-view parity) ─────────────────────────────────
// llm_wiki renders a per-type Lucide icon coloured by SEVERITY (amber=warning,
// blue/red otherwise) before the title, with the rule name as a plain-grey subtitle —
// NOT coloured chips. We mirror that: icon (by category) + severity colour + plain label.

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  "orphan-page": Unlink, // llm_wiki orphan
  "no-outlinks": ArrowUpRight, // llm_wiki no-outlinks
  "broken-wikilink": Link2Off, // llm_wiki broken-link
  "missing-xref": Link2Off,
  "missing-page": FileQuestion,
  contradiction: AlertTriangle,
  "stale-claim": Clock,
  suggestion: Lightbulb,
};

interface CategoryIconProps {
  category: string;
  severity: string;
}

function CategoryIcon({ category, severity }: CategoryIconProps) {
  const color = SEVERITY_COLORS[severity] ?? "var(--syn-text-muted)";
  // BrainCircuit is llm_wiki's fallback icon for semantic findings.
  const Icon: LucideIcon = CATEGORY_ICONS[category] ?? BrainCircuit;
  return (
    <span style={{ flexShrink: 0, marginTop: 2, color, display: "inline-flex" }}>
      <Icon size={16} aria-hidden="true" />
    </span>
  );
}

// ─── Finding title (llm_wiki parity) ──────────────────────────────────────────
// llm_wiki's lint card title is the page the finding is about; for semantic findings
// with no specific page it falls back to the finding text (detail.slice(0, 80)). We
// mirror that so a card is NEVER titled "(unknown page)" when a description exists.

function findingTitle(finding: LintFinding, t: TranslateFn): string {
  const target = finding.target_title?.trim();
  if (target) return target;
  const desc = finding.description?.trim();
  if (desc) return desc.length > 80 ? `${desc.slice(0, 80).trimEnd()}…` : desc;
  return t("lint.noTarget");
}

// ─── Severity group header ─────────────────────────────────────────────────────

const SEVERITY_LABEL_KEYS: Record<SeverityLevel, string> = {
  error: "lint.groups.errors",
  warning: "lint.groups.warnings",
  info: "lint.groups.info",
};

// Narrow translate signature compatible with both prop-passing and TFunction (I3).
type TranslateFn = (key: string, opts?: Record<string, unknown>) => string;

interface SeverityGroupHeaderProps {
  severity: SeverityLevel;
  count: number;
  style: CSSProperties;
  measureRef: (el: HTMLElement | null) => void;
  t: TranslateFn;
}

function SeverityGroupHeader({ severity, count, style, measureRef, t }: SeverityGroupHeaderProps) {
  const color = SEVERITY_COLORS[severity] ?? "var(--syn-text-muted)";
  return (
    <div
      ref={measureRef}
      data-testid={`lint-group-header-${severity}`}
      style={{
        ...style,
        height: GROUP_HEADER_HEIGHT,
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "0 16px",
        background: `color-mix(in srgb, ${color} 6%, var(--syn-bg-soft) 94%)`,
        borderBottom: `1px solid color-mix(in srgb, ${color} 20%, var(--syn-border) 80%)`,
        boxSizing: "border-box",
        userSelect: "none",
      }}
    >
      <span
        style={{
          fontSize: 10,
          fontWeight: 700,
          color,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
        }}
      >
        {t(SEVERITY_LABEL_KEYS[severity])} ({count})
      </span>
    </div>
  );
}

// ─── Action button ────────────────────────────────────────────────────────────

interface LintActionButtonProps {
  label: string;
  onClick: () => void;
  disabled: boolean;
  loading?: boolean;
  variant:
    "apply" | "acknowledge" | "dismiss" | "open" | "delete" | "delete-armed" | "send-to-review";
  title?: string;
  /** When true, the data-testid is prefixed with "batch-" to disambiguate from row buttons. */
  batchBar?: boolean;
}

function LintActionButton({
  label,
  onClick,
  disabled,
  loading,
  variant,
  title,
  batchBar,
}: LintActionButtonProps) {
  const COLORS: Record<string, { border: string; color: string }> = {
    apply: { border: "var(--syn-green)", color: "var(--syn-green)" },
    acknowledge: { border: "var(--syn-accent)", color: "var(--syn-accent)" },
    dismiss: { border: "var(--syn-border)", color: "var(--syn-text-muted)" },
    open: { border: "var(--syn-accent)", color: "var(--syn-accent)" },
    delete: { border: "var(--syn-border)", color: "var(--syn-text-muted)" },
    "delete-armed": { border: "var(--syn-red)", color: "var(--syn-red)" },
    "send-to-review": { border: "var(--syn-type-concept)", color: "var(--syn-type-concept)" },
  };
  const { border, color } =
    COLORS[variant] ?? (COLORS["dismiss"] as { border: string; color: string });
  const isDisabled = disabled || loading;
  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      aria-label={label}
      aria-busy={loading}
      data-testid={batchBar ? `lint-batch-action-${variant}` : `lint-action-${variant}`}
      title={title}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "3px 10px",
        fontSize: 11,
        fontWeight: 600,
        border: `1px solid ${isDisabled ? "var(--syn-border)" : border}`,
        borderRadius: 5,
        background: "transparent",
        color: isDisabled ? "var(--syn-text-dim)" : color,
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

// ─── Finding row (virtualised) ────────────────────────────────────────────────

interface FindingRowProps {
  finding: LintFinding;
  style: CSSProperties;
  measureRef: (el: HTMLElement | null) => void;
  inFlight: "apply" | "dismiss" | null | undefined;
  actionErr: string | null | undefined;
  isSelected: boolean;
  onApply: (id: string) => void;
  onDismiss: (id: string) => void;
  onSendToReview: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onOpen: (finding: LintFinding) => void;
  onDelete: (finding: LintFinding) => void;
  t: TranslateFn;
  lang: string;
}

function FindingRowComponent({
  finding,
  style,
  measureRef,
  inFlight,
  actionErr,
  isSelected,
  onApply,
  onDismiss,
  onSendToReview,
  onToggleSelect,
  onOpen,
  onDelete,
  t,
  lang,
}: FindingRowProps) {
  const isAnyInFlight = inFlight !== null && inFlight !== undefined;
  const isApplying = inFlight === "apply";
  const isDismissing = inFlight === "dismiss";

  // Conditional flag-only (v1.3.13, ADR-0058 §L4): broken-wikilink, orphan-page and
  // no-outlinks have a real Fix when a suggestion is present, otherwise acknowledge-only.
  const CONDITIONAL_FIX_CATEGORIES = new Set(["broken-wikilink", "orphan-page", "no-outlinks"]);
  const isFlagOnly =
    LINT_FLAG_ONLY_CATEGORIES.has(
      finding.category as Parameters<(typeof LINT_FLAG_ONLY_CATEGORIES)["has"]>[0],
    ) ||
    (CONDITIONAL_FIX_CATEGORIES.has(finding.category) && !finding.suggested_target);

  // B1-L9: orphan-page has a Delete button (two-stage confirm in parent)
  const isOrphan = finding.category === "orphan-page";

  // B1-L4: Open button visible when target_page_id is present
  const hasTarget = !!finding.target_page_id;

  const relTime = (() => {
    try {
      const date = new Date(finding.created_at);
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

  const applyLabel = isFlagOnly
    ? isApplying
      ? t("common.loading")
      : t("lint.acknowledge")
    : isApplying
      ? t("lint.fixing")
      : t("lint.fix");

  const applyVariant: "apply" | "acknowledge" = isFlagOnly ? "acknowledge" : "apply";

  return (
    // llm_wiki lint card parity: rounded/bordered card with a soft shadow + inter-card gap,
    // a larger bold title (the page path), a category subtitle ("Broken Link"), and a
    // MULTI-LINE description. Replaces the previous dense flat row (matches the Review cards).
    <div
      ref={measureRef}
      data-testid="lint-finding-row"
      data-finding-id={finding.id}
      data-category={finding.category}
      style={{ ...style, padding: "0 16px 10px", boxSizing: "border-box" }}
    >
      <div
        style={{
          border: isSelected ? "1px solid var(--syn-accent)" : "1px solid var(--syn-border)",
          borderRadius: "var(--syn-radius-md)",
          background: isSelected ? "var(--syn-accent-soft)" : "var(--syn-surface)",
          boxShadow: "var(--syn-shadow-soft)",
          padding: "12px 14px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {/* Row 1: checkbox + category icon + title + category subtitle + timestamp
            (llm_wiki lint-view LintCard: icon coloured by severity, page title, plain-grey
            rule-name subtitle). */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8, minWidth: 0 }}>
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect(finding.id)}
            aria-label={t("lint.selectFinding")}
            data-testid={`lint-row-checkbox-${finding.id}`}
            style={{
              flexShrink: 0,
              cursor: "pointer",
              accentColor: "var(--syn-accent)",
              marginTop: 3,
            }}
          />
          <CategoryIcon category={finding.category} severity={finding.severity} />
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
            <span
              style={{
                fontSize: 14,
                fontWeight: 650,
                lineHeight: 1.35,
                color: "var(--syn-text)",
                overflowWrap: "anywhere",
              }}
              title={findingTitle(finding, t)}
            >
              {findingTitle(finding, t)}
            </span>
            {/* Plain-grey category label (llm_wiki subtitle, e.g. "Broken link") */}
            <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t(`lint.category.${finding.category}`)}
            </span>
          </div>
          <span
            style={{
              fontSize: 10,
              color: "var(--syn-text-dim)",
              flexShrink: 0,
              marginTop: 2,
              whiteSpace: "nowrap",
            }}
            title={finding.created_at}
          >
            {relTime}
          </span>
        </div>

        {/* Row 2: description (plain text — I3) — MULTI-LINE (llm_wiki parity, no truncation) */}
        <div
          style={{
            fontSize: 12.5,
            lineHeight: 1.5,
            color: "var(--syn-text-muted)",
            overflowWrap: "anywhere",
            whiteSpace: "pre-wrap",
          }}
        >
          {finding.description}
        </div>

        {/* Row 3: B1-L2 suggested target strip (green, llm_wiki style) */}
        {finding.suggested_target && (
          <div
            data-testid="lint-suggested-target"
            style={{
              fontSize: 12,
              color: "var(--syn-green)",
              background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)",
              border: "1px solid color-mix(in srgb, var(--syn-green) 25%, transparent 75%)",
              borderRadius: "var(--syn-radius-sm)",
              padding: "6px 10px",
              overflowWrap: "anywhere",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
            title={finding.suggested_target}
          >
            <span aria-hidden="true" style={{ flexShrink: 0, fontSize: 11 }}>
              &#128279;
            </span>
            <span style={{ fontWeight: 600 }}>{t("lint.suggestedTarget")}:</span>
            <span style={{ overflowWrap: "anywhere" }}>{finding.suggested_target}</span>
          </div>
        )}

        {/* Row 4: proposed_action (only for real-fix categories when present) */}
        {!isFlagOnly && finding.proposed_action && !finding.suggested_target && (
          <div
            style={{
              fontSize: 11,
              color: "var(--syn-accent)",
              overflowWrap: "anywhere",
              fontFamily: "var(--syn-font-mono)",
            }}
            title={finding.proposed_action}
          >
            {finding.proposed_action}
          </div>
        )}

        {/* Row 5: flag-only hint */}
        {isFlagOnly && !finding.suggested_target && (
          <div style={{ fontSize: 11, color: "var(--syn-text-dim)", fontStyle: "italic" }}>
            {t("lint.flagOnly")}
          </div>
        )}

        {/* Row 6: action buttons + per-finding error */}
        <div
          style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 2 }}
        >
          <LintActionButton
            label={applyLabel}
            onClick={() => onApply(finding.id)}
            disabled={isAnyInFlight}
            loading={isApplying}
            variant={applyVariant}
          />
          <LintActionButton
            label={isDismissing ? t("common.loading") : t("lint.dismiss")}
            onClick={() => onDismiss(finding.id)}
            disabled={isAnyInFlight}
            loading={isDismissing}
            variant="dismiss"
          />
          {/* B1-L6: Send to Review */}
          <LintActionButton
            label={t("lint.sendToReview")}
            onClick={() => onSendToReview(finding.id)}
            disabled={isAnyInFlight}
            variant="send-to-review"
          />
          {/* B1-L4: Open button */}
          {hasTarget && (
            <LintActionButton
              label={t("lint.open")}
              onClick={() => onOpen(finding)}
              disabled={isAnyInFlight}
              variant="open"
              title={t("lint.open")}
            />
          )}
          {/* B1-L9: Delete button (orphan-page only) — two-stage confirm handled in parent */}
          {isOrphan && finding.target_page_id && (
            <LintActionButton
              label={t("lint.delete")}
              onClick={() => onDelete(finding)}
              disabled={isAnyInFlight}
              variant="delete"
              title={t("lint.deleteConfirm")}
            />
          )}
          {actionErr && (
            <span role="alert" style={{ fontSize: 11, color: "var(--syn-red)", marginLeft: 4 }}>
              {actionErr}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Batch bar ────────────────────────────────────────────────────────────────

interface BatchBarProps {
  selectedCount: number;
  totalCount: number;
  batchInFlight: boolean;
  onSelectAll: () => void;
  onClearSelection: () => void;
  onApplyBatch: () => void;
  onDismissBatch: () => void;
  onSendToReviewBatch: () => void;
  t: TranslateFn;
}

function BatchBar({
  selectedCount,
  totalCount,
  batchInFlight,
  onSelectAll,
  onClearSelection,
  onApplyBatch,
  onDismissBatch,
  onSendToReviewBatch,
  t,
}: BatchBarProps) {
  const allSelected = selectedCount === totalCount && totalCount > 0;

  return (
    <div
      data-testid="lint-batch-bar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 16px",
        borderBottom: "1px solid var(--syn-border)",
        flexShrink: 0,
        background:
          selectedCount > 0
            ? "color-mix(in srgb, var(--syn-accent) 4%, var(--syn-bg-soft) 96%)"
            : "var(--syn-bg-soft)",
        fontSize: 11,
      }}
    >
      {/* Select all checkbox */}
      <input
        type="checkbox"
        checked={allSelected}
        onChange={allSelected ? onClearSelection : onSelectAll}
        aria-label={t("lint.selectAll")}
        data-testid="lint-select-all"
        disabled={totalCount === 0}
        style={{
          cursor: totalCount === 0 ? "not-allowed" : "pointer",
          accentColor: "var(--syn-accent)",
        }}
      />
      <span
        style={{
          color: selectedCount > 0 ? "var(--syn-accent)" : "var(--syn-text-dim)",
          minWidth: 60,
          fontWeight: selectedCount > 0 ? 600 : 400,
        }}
      >
        {selectedCount > 0 ? t("lint.selected", { count: selectedCount }) : t("lint.selectAll")}
      </span>

      {/* Batch action buttons — disabled when nothing selected */}
      <LintActionButton
        label={t("lint.fixSelected")}
        onClick={onApplyBatch}
        disabled={selectedCount === 0 || batchInFlight}
        loading={batchInFlight}
        variant="apply"
        batchBar
      />
      <LintActionButton
        label={t("lint.ignoreSelected")}
        onClick={onDismissBatch}
        disabled={selectedCount === 0 || batchInFlight}
        loading={batchInFlight}
        variant="dismiss"
        batchBar
      />
      <LintActionButton
        label={t("lint.sendSelectedToReview")}
        onClick={onSendToReviewBatch}
        disabled={selectedCount === 0 || batchInFlight}
        loading={batchInFlight}
        variant="send-to-review"
        batchBar
      />
    </div>
  );
}

// ─── Two-stage delete confirm state ──────────────────────────────────────────

interface PendingDelete {
  findingId: string;
  pageId: string;
  pageTitle: string;
}

// ─── Findings list (virtualised — I4) ─────────────────────────────────────────

interface FindingsListProps {
  vaultId: string;
  onOpen: (finding: LintFinding) => void;
  onDelete: (finding: LintFinding) => void;
}

function FindingsList({ vaultId, onOpen, onDelete }: FindingsListProps) {
  const { t, i18n } = useTranslation();
  const findings = useLintStore(useShallow(selectLintFindings));
  const total = useLintStore(selectLintFindingsTotal);
  const loading = useLintStore(selectLintFindingsLoading);
  const actionInFlight = useLintStore(useShallow(selectLintActionInFlight));
  const actionError = useLintStore(useShallow(selectLintActionError));
  const selectedIds = useLintStore(selectLintSelectedIds);
  const apply = useLintStore(selectLintApply);
  const dismiss = useLintStore(selectLintDismiss);
  const sendToReview = useLintStore(selectLintSendToReview);
  const toggleSelect = useLintStore(selectLintToggleSelect);
  const fetchMore = useLintStore(selectLintFetchMoreFindings);
  // L11: true per-severity totals from the backend (null = pre-v0.6 server)
  const severityTotals = useLintStore(selectLintSeverityTotals);

  const scrollRef = useRef<HTMLDivElement>(null);

  // Build grouped virtual rows (error → warning → info with synthetic headers).
  // L11: pass severityTotals so headers show the true total, not just loaded count.
  const virtualRows = buildVirtualRows(findings, severityTotals);

  // Variable-size virtualiser: group headers are shorter than finding rows, and the llm_wiki
  // finding CARDS have variable heights (multi-line description + suggested-target strip), so we
  // measure the real height with measureElement (I4 — always virtualised, never un-virtualised).
  const virtualizer = useVirtualizer({
    count: virtualRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => {
      const row = virtualRows[index];
      return row?.kind === "group-header" ? GROUP_HEADER_HEIGHT : ROW_HEIGHT;
    },
    overscan: 5,
    measureElement: (el) => el?.getBoundingClientRect().height ?? ROW_HEIGHT,
  });

  const handleApply = useCallback(
    (id: string) => {
      void apply(id);
    },
    [apply],
  );
  const handleDismiss = useCallback(
    (id: string) => {
      void dismiss(id);
    },
    [dismiss],
  );
  const handleSendToReview = useCallback(
    (id: string) => {
      void sendToReview(id);
    },
    [sendToReview],
  );
  const handleToggleSelect = useCallback((id: string) => toggleSelect(id), [toggleSelect]);

  if (virtualRows.length === 0 && loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 16 }}>
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} height={48} radius={8} />
        ))}
      </div>
    );
  }

  if (virtualRows.length === 0 && !loading) {
    return (
      <div style={{ display: "flex", height: "100%", padding: 16 }}>
        <EmptyState
          testId="lint-empty"
          eyebrow={t("nav.lint")}
          title={t("lint.empty")}
          body={t("lint.emptyBody")}
        />
      </div>
    );
  }

  const totalHeight = virtualizer.getTotalSize();
  const vItems = virtualizer.getVirtualItems();
  const hasMore = findings.length < total;

  return (
    <div
      ref={scrollRef}
      data-testid="lint-finding-list"
      style={{ overflow: "auto", height: "100%", flex: 1, minHeight: 0 }}
    >
      <div style={{ height: totalHeight + (hasMore ? 48 : 0), position: "relative" }}>
        {vItems.map((vItem) => {
          const row = virtualRows[vItem.index];
          if (!row) return null;

          const rowStyle: CSSProperties = {
            position: "absolute",
            top: vItem.start,
            width: "100%",
          };

          // TanStack Virtual maps a measured node to its row via data-index; stamp it before
          // measuring so the variable card heights actually apply (otherwise every row keeps the
          // fixed estimate, leaving gaps between cards).
          const measureRef = (el: HTMLElement | null) => {
            if (el) el.setAttribute("data-index", String(vItem.index));
            virtualizer.measureElement(el);
          };

          if (row.kind === "group-header") {
            return (
              <SeverityGroupHeader
                key={`group-${row.severity}`}
                severity={row.severity}
                count={row.count}
                style={rowStyle}
                measureRef={measureRef}
                t={t as TranslateFn}
              />
            );
          }

          // finding row
          const { finding } = row;
          return (
            <FindingRowComponent
              key={finding.id}
              finding={finding}
              style={rowStyle}
              measureRef={measureRef}
              inFlight={actionInFlight[finding.id]}
              actionErr={actionError[finding.id]}
              isSelected={selectedIds.has(finding.id)}
              onApply={handleApply}
              onDismiss={handleDismiss}
              onSendToReview={handleSendToReview}
              onToggleSelect={handleToggleSelect}
              onOpen={onOpen}
              onDelete={onDelete}
              t={t as TranslateFn}
              lang={i18n.language}
            />
          );
        })}

        {hasMore && (
          <button
            onClick={() => void fetchMore(vaultId)}
            disabled={loading}
            data-testid="lint-load-more"
            style={{
              position: "absolute",
              top: totalHeight,
              left: 0,
              right: 0,
              height: 40,
              margin: "4px 16px",
              border: "1px solid var(--syn-border)",
              borderRadius: 6,
              background: "var(--syn-bg-soft)",
              color: "var(--syn-text-muted)",
              fontSize: 12,
              cursor: loading ? "wait" : "pointer",
            }}
          >
            {loading ? t("common.loading") : t("lint.loadMore")}
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Main LintView ────────────────────────────────────────────────────────────

export function LintView() {
  const { t } = useTranslation();
  const vaultId = useAppStore(selectVaultId);
  const selectPage = useAppStore(selectSelectPage);
  const setActiveSection = useAppStore(selectSetActiveSection);

  // B1-L8: provider gate for semantic checkbox
  const { configured: providerConfigured, loading: providerLoading } = useProviderConfigured();

  const scan = useLintStore(selectLintScan);
  const refresh = useLintStore(selectLintRefresh);
  const scanning = useLintStore(selectLintScanning);
  const scanError = useLintStore(selectLintScanError);
  const clearScanError = useLintStore(selectClearLintScanError);
  const currentRun = useLintStore(selectLintCurrentRun);
  const findingsTotal = useLintStore(selectLintFindingsTotal);
  const findingsLoading = useLintStore(selectLintFindingsLoading);
  const findingsError = useLintStore(selectLintFindingsError);

  // B1 state
  const semanticEnabled = useLintStore(selectLintSemanticEnabled);
  const setSemanticEnabled = useLintStore(selectLintSetSemanticEnabled);
  const selectedIds = useLintStore(selectLintSelectedIds);
  const batchInFlight = useLintStore(selectLintBatchInFlight);
  const selectAll = useLintStore(selectLintSelectAll);
  const clearSelection = useLintStore(selectLintClearSelection);
  const applyBatch = useLintStore(selectLintApplyBatch);
  const dismissBatch = useLintStore(selectLintDismissBatch);
  const sendToReviewBatch = useLintStore(selectLintSendToReviewBatch);
  const deleteOrphanPage = useLintStore(selectLintDeleteOrphanPage);

  // B1-L9: two-stage delete confirm state
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);

  const effectiveVaultId = vaultId ?? "default";

  // Fetch open findings on mount (without running a scan)
  useEffect(() => {
    const ctrl = new AbortController();
    void refresh(effectiveVaultId, ctrl.signal);
    return () => ctrl.abort();
  }, [effectiveVaultId, refresh]);

  const handleScan = useCallback(() => {
    void scan(effectiveVaultId);
  }, [scan, effectiveVaultId]);

  // Toast on scan error
  useEffect(() => {
    if (scanError) {
      showToast(t("lint.toastError", { detail: scanError }), "error");
    }
  }, [scanError, t]);

  const handleRefresh = useCallback(() => {
    void refresh(effectiveVaultId);
  }, [refresh, effectiveVaultId]);

  // B1-L4: Open button — navigate to page in tree panel
  const handleOpen = useCallback(
    (finding: LintFinding) => {
      if (!finding.target_page_id) return;
      selectPage(finding.target_page_id, "tree");
      setActiveSection("pages");
    },
    [selectPage, setActiveSection],
  );

  // B1-L9: Delete handling (two-stage confirm)
  const handleDeleteRequest = useCallback(
    (finding: LintFinding) => {
      if (!finding.target_page_id) return;
      if (pendingDelete?.findingId === finding.id) {
        // Second click: armed → execute
        const { findingId, pageId } = pendingDelete;
        setPendingDelete(null);
        void deleteOrphanPage(findingId, pageId, effectiveVaultId)
          .then(() => {
            showToast(t("lint.deleteSuccess"), "success");
          })
          .catch((err: unknown) => {
            showToast(t("lint.toastError", { detail: (err as Error).message }), "error");
          });
      } else {
        // First click: arm the delete
        setPendingDelete({
          findingId: finding.id,
          pageId: finding.target_page_id,
          pageTitle: finding.target_title ?? finding.target_page_id,
        });
        // Auto-disarm after 4 seconds
        setTimeout(() => {
          setPendingDelete((prev) => (prev?.findingId === finding.id ? null : prev));
        }, 4000);
      }
    },
    [pendingDelete, deleteOrphanPage, effectiveVaultId, t],
  );

  // B1-L5: batch callbacks with toast
  const handleApplyBatch = useCallback(async () => {
    const { ok, err } = await applyBatch(effectiveVaultId);
    if (err > 0) {
      showToast(t("lint.batchPartial", { ok, err }), "error");
    } else {
      showToast(t("lint.batchApplied", { count: ok }), "success");
    }
  }, [applyBatch, effectiveVaultId, t]);

  const handleDismissBatch = useCallback(async () => {
    const { ok, err } = await dismissBatch(effectiveVaultId);
    if (err > 0) {
      showToast(t("lint.batchPartial", { ok, err }), "error");
    } else {
      showToast(t("lint.batchDismissed", { count: ok }), "success");
    }
  }, [dismissBatch, effectiveVaultId, t]);

  const handleSendToReviewBatch = useCallback(async () => {
    const { ok, err } = await sendToReviewBatch(effectiveVaultId);
    if (err > 0) {
      showToast(t("lint.batchPartial", { ok, err }), "error");
    } else {
      showToast(t("lint.batchSentToReview", { count: ok }), "success");
    }
  }, [sendToReviewBatch, effectiveVaultId, t]);

  const selectedCount = selectedIds.size;

  // Semantic toggle disabled when provider not configured
  const semanticDisabled = !providerConfigured || providerLoading || scanning;

  return (
    <div
      data-testid="lint-view"
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
      {/* UXA-28: @keyframes syn-spin is declared globally in theme.css — no inline <style> needed */}

      {/* ── Header ────────────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 16px",
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
            flex: 1,
          }}
        >
          {t("lint.title")}
          {findingsTotal > 0 && !findingsLoading && (
            <span
              aria-label={`${findingsTotal} findings`}
              style={{
                marginLeft: 8,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: 18,
                height: 18,
                padding: "0 5px",
                borderRadius: 9,
                background: "var(--syn-red)",
                color: "#ffffff",
                fontSize: 10,
                fontWeight: 700,
              }}
            >
              {findingsTotal > 999 ? "999+" : findingsTotal}
            </span>
          )}
        </h2>

        {/* B1-L8: Semantic (LLM) checkbox */}
        <label
          title={t("lint.semanticHelp")}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontSize: 11,
            color: semanticDisabled ? "var(--syn-text-dim)" : "var(--syn-text-muted)",
            cursor: semanticDisabled ? "not-allowed" : "pointer",
            userSelect: "none",
            flexShrink: 0,
          }}
        >
          <input
            type="checkbox"
            checked={semanticEnabled}
            disabled={semanticDisabled}
            onChange={(e) => setSemanticEnabled(e.target.checked)}
            data-testid="lint-semantic-checkbox"
            style={{
              accentColor: "var(--syn-accent)",
              cursor: semanticDisabled ? "not-allowed" : "pointer",
            }}
          />
          {t("lint.semantic")}
        </label>

        {/* Run Lint button */}
        <button
          onClick={handleScan}
          disabled={scanning}
          aria-label={t("lint.runLint")}
          aria-busy={scanning}
          data-testid="lint-run-btn"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            padding: "4px 10px",
            fontSize: 11,
            fontWeight: 600,
            border: "1px solid var(--syn-green)",
            borderRadius: 5,
            background: "transparent",
            color: scanning ? "var(--syn-text-dim)" : "var(--syn-green)",
            cursor: scanning ? "wait" : "pointer",
            transition: "opacity 0.1s",
          }}
          title={t("lint.runLintHelp")}
        >
          {scanning && (
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
          {scanning ? t("lint.scanning") : t("lint.runLint")}
        </button>

        {/* Refresh button */}
        <button
          onClick={handleRefresh}
          disabled={findingsLoading || scanning}
          aria-label={t("lint.refresh")}
          data-testid="lint-refresh-btn"
          style={{
            padding: "4px 10px",
            fontSize: 11,
            border: "1px solid var(--syn-border)",
            borderRadius: 5,
            background: "transparent",
            color: findingsLoading || scanning ? "var(--syn-text-dim)" : "var(--syn-text-muted)",
            cursor: findingsLoading || scanning ? "wait" : "pointer",
          }}
        >
          {findingsLoading ? t("common.loading") : t("lint.refresh")}
        </button>
      </div>

      {/* ── Run info line (last run cost + status) ──────────────────── */}
      {currentRun && (
        <div
          data-testid="lint-run-info"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "5px 16px",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
            fontSize: 11,
            color: "var(--syn-text-dim)",
            background: "var(--syn-bg)",
          }}
        >
          <span
            style={{
              color: currentRun.status === "error" ? "var(--syn-red)" : "var(--syn-text-muted)",
            }}
          >
            {t(`lint.runStatus.${currentRun.status}`)}
          </span>
          <span style={{ color: "var(--syn-border)" }}>&middot;</span>
          <span>
            {t("lint.cost")}: {formatCost(currentRun.total_cost_usd)}
          </span>
          <span style={{ color: "var(--syn-border)" }}>&middot;</span>
          <span>
            {t("lint.findings")}: {currentRun.findings_count}
          </span>
          <span style={{ color: "var(--syn-border)" }}>&middot;</span>
          <span>
            {t("lint.iterations")}: {currentRun.iterations_used}
          </span>
        </div>
      )}

      {/* ── Scan error banner ────────────────────────────────────────── */}
      {scanError && (
        <div
          role="alert"
          data-testid="lint-scan-error"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 16px",
            borderBottom: "1px solid color-mix(in srgb, var(--syn-red) 25%, transparent 75%)",
            background: "color-mix(in srgb, var(--syn-red) 6%, var(--syn-mix-base) 94%)",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, color: "var(--syn-red)", flex: 1 }}>{scanError}</span>
          <button
            onClick={clearScanError}
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

      {/* ── Findings load error ──────────────────────────────────────── */}
      {findingsError && !findingsLoading && (
        <div data-testid="lint-findings-error" style={{ padding: "8px 16px", flexShrink: 0 }}>
          <ErrorState error={findingsError} onRetry={handleRefresh} />
        </div>
      )}

      {/* ── B1-L9: Delete confirm banner ─────────────────────────────── */}
      {pendingDelete && (
        <div
          data-testid="lint-delete-confirm-banner"
          role="alert"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 16px",
            borderBottom: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
            background: "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)",
            flexShrink: 0,
            fontSize: 11,
          }}
        >
          <span style={{ color: "var(--syn-red)", flex: 1, fontWeight: 600 }}>
            {t("lint.deleteConfirm")}: &ldquo;{pendingDelete.pageTitle}&rdquo; —{" "}
            {t("lint.deleteConfirmHint")}
          </span>
          <button
            onClick={() => setPendingDelete(null)}
            style={{
              fontSize: 11,
              color: "var(--syn-text-muted)",
              background: "none",
              border: "none",
              cursor: "pointer",
              textDecoration: "underline",
              padding: 0,
            }}
          >
            {t("common.close")}
          </button>
        </div>
      )}

      {/* ── B1-L5: Batch bar (visible whenever findings exist) ──────── */}
      {(findingsTotal > 0 || selectedCount > 0) && (
        <BatchBar
          selectedCount={selectedCount}
          totalCount={findingsTotal}
          batchInFlight={batchInFlight}
          onSelectAll={selectAll}
          onClearSelection={clearSelection}
          onApplyBatch={() => void handleApplyBatch()}
          onDismissBatch={() => void handleDismissBatch()}
          onSendToReviewBatch={() => void handleSendToReviewBatch()}
          t={t as TranslateFn}
        />
      )}

      {/* ── Hint row ─────────────────────────────────────────────────── */}
      {!findingsLoading && !findingsError && (
        <div
          style={{
            padding: "6px 16px",
            flexShrink: 0,
            borderBottom: "1px solid var(--syn-border)",
            fontSize: 11,
            color: "var(--syn-text-dim)",
          }}
        >
          {t("lint.hint")}
        </div>
      )}

      {/* ── Virtualised findings list (I4) ───────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <FindingsList
          vaultId={effectiveVaultId}
          onOpen={handleOpen}
          onDelete={handleDeleteRequest}
        />
      </div>
    </div>
  );
}
