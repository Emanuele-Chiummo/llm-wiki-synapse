/**
 * LintView.tsx — K2 Lint-fix section UI (ADR-0037 §6).
 *
 * Layout:
 *   - Header: title + "Run Lint" button (spinner while scanning) + refresh
 *   - Run-history cost line: last run's total_cost_usd at 4dp (I7) + status + timestamp
 *   - Scan error banner
 *   - Findings list grouped by category (TanStack Virtual — I4)
 *   - Per-finding row: severity chip + category badge + target_title + description
 *     + proposed_action (when present) + Apply / Dismiss buttons
 *   - Apply label is "Fix" for missing-xref/missing-page (real write).
 *     Apply label is "Acknowledge" for orphan-page/contradiction/stale-claim (flag-only).
 *   - Empty state when no open findings.
 *
 * INVARIANT I3: Zustand selectors + shallow equality. No store subscriptions on
 *   unrelated state. Descriptions displayed as plain text — no per-token parsing.
 * INVARIANT I4: findings list virtualised with TanStack Virtual always.
 * INVARIANT I7: total_cost_usd rendered at 4dp; scan is bounded (max_iter/token_budget
 *   frozen by the backend before the scan starts).
 */

import { useEffect, useRef, useCallback, type CSSProperties } from "react";
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
} from "../../store/lintStore";
import { useGraphStore, selectVaultId } from "../../store/graphStore";
import { EmptyState } from "../common/EmptyState";
import { showToast } from "../common/Toast";
import type { LintFinding } from "../../api/types";
import { LINT_FLAG_ONLY_CATEGORIES } from "../../api/types";

// ─── Constants ────────────────────────────────────────────────────────────────

/** Row height: severity chip + title row + description row + action row. */
const ROW_HEIGHT = 120;

/** Format cost at 4 decimal places (I7). */
function formatCost(usd: number): string {
  return `$${usd.toFixed(4)}`;
}

// ─── Category badge ───────────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<string, { color: string; bg: string }> = {
  "orphan-page":   { color: "var(--syn-text-muted)",  bg: "var(--syn-surface-hover)" },
  "missing-xref":  { color: "var(--syn-amber)",        bg: "color-mix(in srgb, var(--syn-amber) 10%, white 90%)" },
  "contradiction": { color: "var(--syn-red)",          bg: "color-mix(in srgb, var(--syn-red) 10%, white 90%)" },
  "stale-claim":   { color: "var(--syn-type-concept)", bg: "color-mix(in srgb, var(--syn-type-concept) 10%, white 90%)" },
  "missing-page":  { color: "var(--syn-green)",        bg: "color-mix(in srgb, var(--syn-green) 10%, white 90%)" },
};

interface CategoryBadgeProps {
  category: string;
  t: (key: string) => string;
}

function CategoryBadge({ category, t }: CategoryBadgeProps) {
  const { color, bg } = CATEGORY_COLORS[category] ?? { color: "var(--syn-text-muted)", bg: "var(--syn-surface-hover)" };
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontSize: 10,
        fontWeight: 600,
        color,
        background: bg,
        border: `1px solid color-mix(in srgb, ${color} 30%, transparent 70%)`,
        borderRadius: 8,
        padding: "1px 6px",
        whiteSpace: "nowrap",
        userSelect: "none",
        flexShrink: 0,
      }}
    >
      {t(`lint.category.${category}`)}
    </span>
  );
}

// ─── Severity chip ────────────────────────────────────────────────────────────

const SEVERITY_COLORS: Record<string, string> = {
  info:    "var(--syn-text-muted)",
  warning: "var(--syn-amber)",
  error:   "var(--syn-red)",
};

interface SeverityChipProps {
  severity: string;
}

function SeverityChip({ severity }: SeverityChipProps) {
  const color = SEVERITY_COLORS[severity] ?? "var(--syn-text-muted)";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontSize: 9,
        fontWeight: 700,
        color,
        background: `color-mix(in srgb, ${color} 12%, white 88%)`,
        border: `1px solid color-mix(in srgb, ${color} 30%, transparent 70%)`,
        borderRadius: 4,
        padding: "0 5px",
        whiteSpace: "nowrap",
        userSelect: "none",
        flexShrink: 0,
        textTransform: "uppercase",
        letterSpacing: "0.04em",
      }}
    >
      {severity}
    </span>
  );
}

// ─── Action button ────────────────────────────────────────────────────────────

interface LintActionButtonProps {
  label: string;
  onClick: () => void;
  disabled: boolean;
  loading?: boolean;
  variant: "apply" | "acknowledge" | "dismiss";
}

function LintActionButton({ label, onClick, disabled, loading, variant }: LintActionButtonProps) {
  const COLORS: Record<string, { border: string; color: string }> = {
    apply:       { border: "var(--syn-green)",      color: "var(--syn-green)" },
    acknowledge: { border: "var(--syn-accent)",     color: "var(--syn-accent)" },
    dismiss:     { border: "var(--syn-border)",     color: "var(--syn-text-muted)" },
  };
  const { border, color } = COLORS[variant] ?? (COLORS["dismiss"] as { border: string; color: string });
  const isDisabled = disabled || loading;
  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      aria-label={label}
      aria-busy={loading}
      data-testid={`lint-action-${variant}`}
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
  inFlight: "apply" | "dismiss" | null | undefined;
  actionErr: string | null | undefined;
  onApply: (id: string) => void;
  onDismiss: (id: string) => void;
  t: (key: string) => string;
  lang: string;
}

function FindingRow({
  finding,
  style,
  inFlight,
  actionErr,
  onApply,
  onDismiss,
  t,
  lang,
}: FindingRowProps) {
  const isAnyInFlight = inFlight !== null && inFlight !== undefined;
  const isApplying = inFlight === "apply";
  const isDismissing = inFlight === "dismiss";

  // Determine if this category is flag-only (acknowledge) vs real fix
  const isFlagOnly = LINT_FLAG_ONLY_CATEGORIES.has(finding.category as Parameters<typeof LINT_FLAG_ONLY_CATEGORIES["has"]>[0]);

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
    ? (isApplying ? t("common.loading") : t("lint.acknowledge"))
    : (isApplying ? t("lint.fixing") : t("lint.fix"));

  const applyVariant: "apply" | "acknowledge" = isFlagOnly ? "acknowledge" : "apply";

  return (
    <div
      data-testid="lint-finding-row"
      data-finding-id={finding.id}
      data-category={finding.category}
      style={{
        ...style,
        height: ROW_HEIGHT,
        padding: "8px 16px",
        borderBottom: "1px solid var(--syn-border)",
        display: "flex",
        flexDirection: "column",
        gap: 3,
        boxSizing: "border-box",
      }}
    >
      {/* Row 1: severity chip + category badge + target_title + timestamp */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <SeverityChip severity={finding.severity} />
        <CategoryBadge category={finding.category} t={t} />
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
          title={finding.target_title ?? ""}
        >
          {finding.target_title ?? t("lint.noTarget")}
        </span>
        <span
          style={{ fontSize: 10, color: "var(--syn-text-dim)", flexShrink: 0 }}
          title={finding.created_at}
        >
          {relTime}
        </span>
      </div>

      {/* Row 2: description (plain text — I3) */}
      <div
        style={{
          fontSize: 11,
          color: "var(--syn-text-muted)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={finding.description}
      >
        {finding.description}
      </div>

      {/* Row 3: proposed_action (only for real-fix categories when present) */}
      {!isFlagOnly && finding.proposed_action && (
        <div
          style={{
            fontSize: 10,
            color: "var(--syn-accent)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontFamily: "monospace",
          }}
          title={finding.proposed_action}
        >
          {finding.proposed_action}
        </div>
      )}

      {/* Row 4: flag-only hint */}
      {isFlagOnly && (
        <div
          style={{
            fontSize: 10,
            color: "var(--syn-text-dim)",
            fontStyle: "italic",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {t("lint.flagOnly")}
        </div>
      )}

      {/* Row 5: action buttons + per-finding error */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
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
        {actionErr && (
          <span
            role="alert"
            style={{ fontSize: 10, color: "var(--syn-red)", marginLeft: 4 }}
          >
            {actionErr}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Findings list (virtualised — I4) ─────────────────────────────────────────

interface FindingsListProps {
  vaultId: string;
}

function FindingsList({ vaultId }: FindingsListProps) {
  const { t, i18n } = useTranslation();
  const findings = useLintStore(useShallow(selectLintFindings));
  const total = useLintStore(selectLintFindingsTotal);
  const loading = useLintStore(selectLintFindingsLoading);
  const actionInFlight = useLintStore(useShallow(selectLintActionInFlight));
  const actionError = useLintStore(useShallow(selectLintActionError));
  const apply = useLintStore(selectLintApply);
  const dismiss = useLintStore(selectLintDismiss);
  const fetchMore = useLintStore(selectLintFetchMoreFindings);

  const scrollRef = useRef<HTMLDivElement>(null);

  // Always virtualise — efficient regardless of list size (I4).
  const virtualizer = useVirtualizer({
    count: findings.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 5,
  });

  const handleApply = useCallback(
    (id: string) => { void apply(id); },
    [apply],
  );
  const handleDismiss = useCallback(
    (id: string) => { void dismiss(id); },
    [dismiss],
  );

  if (findings.length === 0 && !loading) {
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
  const virtualItems = virtualizer.getVirtualItems();
  const hasMore = findings.length < total;

  return (
    <div
      ref={scrollRef}
      data-testid="lint-finding-list"
      style={{ overflow: "auto", height: "100%", flex: 1, minHeight: 0 }}
    >
      <div style={{ height: totalHeight + (hasMore ? 48 : 0), position: "relative" }}>
        {virtualItems.map((vRow) => {
          const finding = findings[vRow.index];
          if (!finding) return null;
          return (
            <FindingRow
              key={finding.id}
              finding={finding}
              style={{ position: "absolute", top: vRow.start, width: "100%" }}
              inFlight={actionInFlight[finding.id]}
              actionErr={actionError[finding.id]}
              onApply={handleApply}
              onDismiss={handleDismiss}
              t={t}
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
  const vaultId = useGraphStore(selectVaultId);

  const scan = useLintStore(selectLintScan);
  const refresh = useLintStore(selectLintRefresh);
  const scanning = useLintStore(selectLintScanning);
  const scanError = useLintStore(selectLintScanError);
  const clearScanError = useLintStore(selectClearLintScanError);
  const currentRun = useLintStore(selectLintCurrentRun);
  const findingsTotal = useLintStore(selectLintFindingsTotal);
  const findingsLoading = useLintStore(selectLintFindingsLoading);
  const findingsError = useLintStore(selectLintFindingsError);

  const effectiveVaultId = vaultId ?? "default";

  // Fetch open findings on mount (without running a scan)
  useEffect(() => {
    const ctrl = new AbortController();
    void refresh(effectiveVaultId, ctrl.signal);
    return () => ctrl.abort();
  }, [effectiveVaultId, refresh]);

  const handleScan = useCallback(() => {
    void scan(effectiveVaultId);
    // Toast will fire after scan completes via the scanError watcher below
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
      {/* Spinner keyframe — injected once */}
      <style>{`@keyframes syn-spin { to { transform: rotate(360deg); } }`}</style>

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
          <span style={{ color: currentRun.status === "error" ? "var(--syn-red)" : "var(--syn-text-muted)" }}>
            {t(`lint.runStatus.${currentRun.status}`)}
          </span>
          <span style={{ color: "var(--syn-border)" }}>&middot;</span>
          <span>{t("lint.cost")}: {formatCost(currentRun.total_cost_usd)}</span>
          <span style={{ color: "var(--syn-border)" }}>&middot;</span>
          <span>{t("lint.findings")}: {currentRun.findings_count}</span>
          <span style={{ color: "var(--syn-border)" }}>&middot;</span>
          <span>{t("lint.iterations")}: {currentRun.iterations_used}</span>
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
            background: "color-mix(in srgb, var(--syn-red) 6%, white 94%)",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 12, color: "var(--syn-red)", flex: 1 }}>
            {scanError}
          </span>
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
        <div
          role="alert"
          data-testid="lint-findings-error"
          style={{
            padding: "8px 16px",
            borderBottom: "1px solid var(--syn-border)",
            flexShrink: 0,
            fontSize: 12,
            color: "var(--syn-red)",
            background: "color-mix(in srgb, var(--syn-red) 6%, white 94%)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {findingsError}
          <button
            onClick={handleRefresh}
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
        <FindingsList vaultId={effectiveVaultId} />
      </div>
    </div>
  );
}
