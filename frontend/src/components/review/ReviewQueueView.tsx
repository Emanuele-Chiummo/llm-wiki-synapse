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

import { useEffect, useCallback, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useReviewStore,
  selectReviewTotal,
  selectReviewLoading,
  selectReviewError,
  selectActiveTab,
  selectReviewFilters,
  selectSelectedIds,
  selectLastDeepResearch,
  selectDeepResearchError,
  selectLastSweepResult,
  selectLastBulkResult,
  selectLastClearResult,
  selectBulkError,
  selectFetchFreshReview,
  selectSetActiveTab,
  selectSetReviewFilters,
  selectClearReviewFilters,
  selectSweep,
  selectBulkAction,
  selectClearResolvedRows,
  selectSelectAllPending,
  selectClearSelection,
  selectClearDeepResearchError,
  selectClearLastDeepResearch,
  selectClearLastSweepResult,
  selectClearLastBulkResult,
  selectClearLastClearResult,
  selectClearBulkError,
} from "../../store/reviewStore";
import { ReviewDeepResearchPanel } from "./ReviewDeepResearchPanel";
import { PanelDrawer } from "../panels/PanelDrawer";
import { useViewport } from "../../hooks/useViewport";
import {
  selectVaultId,
  selectSetActiveSection,
  selectSelectPage,
  useAppStore,
} from "../../store/appStore";
import type { PageType, ReviewItemType, ReviewProposalOrigin } from "../../api/types";
import type { ReviewQueueStatus } from "../../api/reviewClient";
import { TabButton } from "./ReviewTabButton";
import { ReviewItemList } from "./ReviewItemList";
import { ErrorState } from "../common/ErrorState";

// ─── Constants ────────────────────────────────────────────────────────────────

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

// ─── Main ReviewQueueView ─────────────────────────────────────────────────────

export function ReviewQueueView() {
  const { t } = useTranslation();
  const vaultId = useAppStore(selectVaultId);
  const setActiveSection = useAppStore(selectSetActiveSection);
  const selectPage = useAppStore(selectSelectPage);

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
    const tabs = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
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
                fontFamily: "var(--syn-font-mono)",
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
        <div data-testid="review-load-error" style={{ padding: "8px 16px", flexShrink: 0 }}>
          <ErrorState error={error} onRetry={() => void fetchFresh(effectiveVaultId)} />
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
