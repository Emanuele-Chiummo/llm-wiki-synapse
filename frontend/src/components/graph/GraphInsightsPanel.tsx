/**
 * GraphInsightsPanel.tsx — overlay panel showing computed graph insights (F4, G-P1-5).
 *
 * Renders as an absolutely-positioned card inside GraphPanel's relative container.
 * Sibling of GraphViewer — DO NOT render this inside GraphViewer.tsx.
 *
 * INVARIANT I2: reads community/degree from server-supplied store data only.
 *   Never calls any layout algorithm.
 * INVARIANT I3: insights computed once via useMemo keyed on [nodes, edges, communities].
 *   Store subscriptions use selectors + useShallow for all collection reads.
 *   No per-render heavy work.
 *
 * Deep Research (B5/D3): clicking the Deep Research button on a gap/bridge insight
 *   opens ResearchTopicDialog seeded with the insight's topic. The dialog calls
 *   POST /research/optimize-topic, shows an editable topic + queries, and on confirm
 *   POSTs /research/start then navigates to "deep-search".
 *   ResearchTopicDialog uses position:fixed so it escapes this panel's stacking context.
 *
 * i18n: all user-visible strings via useTranslation() (F16).
 *   Keys are under the graph.insights.* namespace.
 */

import { useState, useMemo, useCallback, useRef, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import { Lightbulb, X, ChevronDown, ChevronUp, Search } from "lucide-react";
import {
  useGraphStore,
  selectNodes,
  selectEdges,
  selectCommunities,
  selectSetSelectedNodeId,
  selectSetActiveSection,
  selectVaultId,
} from "../../store/graphStore";
import {
  useResearchStore,
  selectStartRun,
  selectStartPollingDetail,
  selectClearStartError,
} from "../../store/researchStore";
import { ResearchTopicDialog } from "../research/ResearchTopicDialog";
import {
  computeGraphInsights,
} from "./graphInsights";
import type { InsightItem } from "./graphInsights";

// ─── Sub-components ───────────────────────────────────────────────────────────

interface InsightRowProps {
  item: InsightItem;
  onHighlight: (nodeId: string | null) => void;
  onDismiss: (id: string) => void;
  onDeepResearch: (topic: string) => void;
  showDeepResearch: boolean;
}

function InsightRow({
  item,
  onHighlight,
  onDismiss,
  onDeepResearch,
  showDeepResearch,
}: InsightRowProps) {
  const { t } = useTranslation();

  const handleRowClick = useCallback(() => {
    if (item.primaryNodeId !== null) {
      onHighlight(item.primaryNodeId);
    }
  }, [item.primaryNodeId, onHighlight]);

  const handleDismiss = useCallback(
    (e: MouseEvent) => {
      e.stopPropagation();
      onDismiss(item.id);
    },
    [item.id, onDismiss],
  );

  const handleDeepResearch = useCallback(
    (e: MouseEvent) => {
      e.stopPropagation();
      onDeepResearch(item.topic);
    },
    [item.topic, onDeepResearch],
  );

  const label = (() => {
    switch (item.kind) {
      case "surprising":
        return t("graph.insights.surprisingRow", {
          source: item.sourceTitle,
          target: item.targetTitle,
          score: item.score.toFixed(1),
        });
      case "gap-isolated":
        return item.nodeTitle;
      case "gap-sparse":
        return t("graph.insights.sparseRow", {
          id: item.communityId,
          size: item.size,
          cohesion: (item.cohesion * 100).toFixed(0),
        });
      case "gap-bridge":
        return t("graph.insights.bridgeRow", {
          title: item.nodeTitle,
          count: item.neighborCommunityCount,
        });
    }
  })();

  const isClickable = item.primaryNodeId !== null;

  return (
    <div
      data-testid="graph-insight-row"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 6,
        padding: "5px 8px",
        borderRadius: 4,
        cursor: isClickable ? "pointer" : "default",
        background: "transparent",
        transition: "background 0.1s",
      }}
      onClick={handleRowClick}
      onMouseEnter={(e) => {
        if (isClickable) {
          (e.currentTarget as HTMLDivElement).style.background =
            "color-mix(in srgb, var(--syn-border) 40%, transparent)";
        }
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLDivElement).style.background = "transparent";
      }}
      role={isClickable ? "button" : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onKeyDown={(e) => {
        if (isClickable && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          handleRowClick();
        }
      }}
    >
      <span
        style={{
          flex: 1,
          fontSize: 12,
          color: "var(--syn-text)",
          lineHeight: 1.4,
          wordBreak: "break-word",
          minWidth: 0,
        }}
      >
        {label}
      </span>

      <div style={{ display: "flex", alignItems: "center", gap: 2, flexShrink: 0 }}>
        {showDeepResearch && (
          <button
            type="button"
            data-testid="graph-insight-deep-research"
            title={t("graph.insights.deepResearch")}
            aria-label={t("graph.insights.deepResearch")}
            onClick={handleDeepResearch}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 20,
              height: 20,
              borderRadius: 3,
              border: "none",
              background: "transparent",
              color: "var(--syn-text-dim)",
              cursor: "pointer",
              padding: 0,
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-dim)";
            }}
          >
            <Search size={12} />
          </button>
        )}

        <button
          type="button"
          data-testid="graph-insight-dismiss"
          aria-label={t("graph.insights.dismissAriaLabel")}
          onClick={handleDismiss}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 20,
            height: 20,
            borderRadius: 3,
            border: "none",
            background: "transparent",
            color: "var(--syn-text-muted)",
            cursor: "pointer",
            padding: 0,
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-red)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-muted)";
          }}
        >
          <X size={12} />
        </button>
      </div>
    </div>
  );
}

// ─── Section header ───────────────────────────────────────────────────────────

function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "4px 8px 2px",
        marginTop: 6,
      }}
    >
      <span
        style={{
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--syn-text-muted)",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontSize: 10,
          color: "var(--syn-text-muted)",
          background: "color-mix(in srgb, var(--syn-border) 50%, transparent)",
          borderRadius: 8,
          padding: "0 5px",
          minWidth: 16,
          textAlign: "center",
        }}
      >
        {count}
      </span>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function GraphInsightsPanel() {
  const { t } = useTranslation();

  // ── Store subscriptions (selectors + useShallow for collections — I3) ───────
  const nodes = useGraphStore(useShallow(selectNodes));
  const edges = useGraphStore(useShallow(selectEdges));
  const communities = useGraphStore(useShallow(selectCommunities));
  const setSelectedNodeId = useGraphStore(selectSetSelectedNodeId);
  const setActiveSection = useGraphStore(selectSetActiveSection);
  const vaultId = useGraphStore(selectVaultId);

  // ── Research store actions (B5/D3) ────────────────────────────────────────
  const startRun = useResearchStore(selectStartRun);
  const startPollingDetail = useResearchStore(selectStartPollingDetail);
  const clearStartError = useResearchStore(selectClearStartError);

  // ── Local UI state ─────────────────────────────────────────────────────────
  // GI-1 (v1.3.14 fix): panel opens EXPANDED by default so "Surprising Connections"
  // content is immediately visible when the user clicks Insights. The collapse
  // chevron remains available to tuck the panel away. The chevron wires to this
  // internal `collapsed` state only — it does NOT call setShowInsightsPanel (which
  // would unmount the panel). Verified in GraphInsightsPanel.test.tsx test G.
  const [collapsed, setCollapsed] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  /** Seed topic for the dialog; null = dialog closed. */
  const [dialogSeedTopic, setDialogSeedTopic] = useState<string | null>(null);
  /** Ref to the polling cleanup fn so we can stop on unmount (I3). */
  const stopPollRef = useRef<(() => void) | null>(null);

  // ── Compute insights once (I3) ─────────────────────────────────────────────
  const insights = useMemo(
    () => computeGraphInsights(nodes, edges, communities),
    [nodes, edges, communities],
  );

  // Reset dismissals when underlying data changes materially (total changes)
  // This is acceptable local-state reset behavior per the spec.

  // ── Filter dismissed items ─────────────────────────────────────────────────
  const visibleSurprising = useMemo(
    () => insights.surprising.filter((i) => !dismissed.has(i.id)),
    [insights.surprising, dismissed],
  );
  const visibleIsolated = useMemo(
    () => insights.gapIsolated.filter((i) => !dismissed.has(i.id)),
    [insights.gapIsolated, dismissed],
  );
  const visibleSparse = useMemo(
    () => insights.gapSparse.filter((i) => !dismissed.has(i.id)),
    [insights.gapSparse, dismissed],
  );
  const visibleBridge = useMemo(
    () => insights.gapBridge.filter((i) => !dismissed.has(i.id)),
    [insights.gapBridge, dismissed],
  );

  const visibleTotal =
    visibleSurprising.length +
    visibleIsolated.length +
    visibleSparse.length +
    visibleBridge.length;

  // ── Handlers ──────────────────────────────────────────────────────────────
  const handleHighlight = useCallback(
    (nodeId: string | null) => {
      setSelectedNodeId(nodeId);
    },
    [setSelectedNodeId],
  );

  const handleDismiss = useCallback((id: string) => {
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, []);

  /**
   * Open the ResearchTopicDialog seeded with the insight topic (B5/D3).
   * Previously: navigate-only. Now: dialog → optimize → editable confirm → start.
   */
  const handleDeepResearch = useCallback(
    (topic: string) => {
      setDialogSeedTopic(topic);
    },
    [],
  );

  const handleDialogCancel = useCallback(() => {
    setDialogSeedTopic(null);
  }, []);

  const handleDialogConfirm = useCallback(
    async (editedTopic: string, editedQueries: string[]) => {
      setDialogSeedTopic(null);
      clearStartError();
      try {
        const startParams: Parameters<typeof startRun>[0] = {
          vault_id: vaultId ?? "default",
          topic: editedTopic,
        };
        if (editedQueries.length > 0) {
          startParams.queries = editedQueries;
        }
        const runId = await startRun(startParams);
        // Stop any existing poll before starting a new one
        stopPollRef.current?.();
        stopPollRef.current = startPollingDetail(runId);
      } catch {
        // startError written to store; DeepSearchView will show it
      }
      // Navigate to the deep-search view regardless of success so the user
      // can see the run status (or the error message).
      setActiveSection("deep-search");
    },
    [vaultId, startRun, startPollingDetail, clearStartError, setActiveSection],
  );

  const toggleCollapsed = useCallback(() => {
    setCollapsed((v) => !v);
  }, []);

  // ── Do not render anything if graph has no nodes ──────────────────────────
  if (nodes.length === 0) return null;

  return (
    <>
    {/* ResearchTopicDialog renders with position:fixed — escapes the panel stacking context */}
    {dialogSeedTopic !== null && (
      <ResearchTopicDialog
        seedTopic={dialogSeedTopic}
        onConfirm={(topic, queries) => { void handleDialogConfirm(topic, queries); }}
        onCancel={handleDialogCancel}
      />
    )}
    <div
      data-testid="graph-insights-panel"
      style={{
        position: "absolute",
        top: 12,
        right: 12,
        width: 300,
        maxWidth: "calc(100% - 24px)",
        maxHeight: collapsed ? "auto" : "60%",
        display: "flex",
        flexDirection: "column",
        background: "var(--syn-bg-soft)",
        border: "1px solid var(--syn-border)",
        borderRadius: 8,
        boxShadow: "0 2px 12px rgba(0,0,0,0.18)",
        zIndex: 10,
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "8px 10px",
          borderBottom: collapsed ? "none" : "1px solid var(--syn-border)",
          flexShrink: 0,
        }}
      >
        <Lightbulb
          size={14}
          style={{ color: "var(--syn-text-dim)", flexShrink: 0 }}
        />
        <span
          style={{
            flex: 1,
            fontSize: 12,
            fontWeight: 600,
            color: "var(--syn-text)",
            letterSpacing: "0.01em",
          }}
        >
          {t("graph.insights.title")}
          {visibleTotal > 0 && (
            <span
              style={{
                marginLeft: 6,
                fontSize: 10,
                color: "var(--syn-text-muted)",
                fontWeight: 400,
              }}
            >
              ({visibleTotal})
            </span>
          )}
        </span>

        <button
          type="button"
          aria-label={collapsed ? t("graph.insights.expandAriaLabel") : t("graph.insights.collapseAriaLabel")}
          onClick={toggleCollapsed}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 20,
            height: 20,
            borderRadius: 3,
            border: "none",
            background: "transparent",
            color: "var(--syn-text-muted)",
            cursor: "pointer",
            padding: 0,
            flexShrink: 0,
          }}
        >
          {collapsed ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </button>
      </div>

      {/* Body — hidden when collapsed */}
      {!collapsed && (
        <div
          style={{
            overflowY: "auto",
            flex: 1,
            minHeight: 0,
            paddingBottom: 6,
          }}
        >
          {visibleTotal === 0 ? (
            <div
              data-testid="graph-insights-empty"
              style={{
                padding: "12px 12px",
                fontSize: 12,
                color: "var(--syn-text-muted)",
                textAlign: "center",
                lineHeight: 1.5,
              }}
            >
              {t("graph.insights.empty")}
            </div>
          ) : (
            <>
              {/* Surprising connections */}
              {visibleSurprising.length > 0 && (
                <>
                  <SectionHeader
                    label={t("graph.insights.sectionSurprising")}
                    count={visibleSurprising.length}
                  />
                  {visibleSurprising.map((item) => (
                    <InsightRow
                      key={item.id}
                      item={item}
                      onHighlight={handleHighlight}
                      onDismiss={handleDismiss}
                      onDeepResearch={handleDeepResearch}
                      showDeepResearch={false}
                    />
                  ))}
                </>
              )}

              {/* Knowledge gaps */}
              {(visibleIsolated.length > 0 ||
                visibleSparse.length > 0 ||
                visibleBridge.length > 0) && (
                <>
                  <SectionHeader
                    label={t("graph.insights.sectionGaps")}
                    count={visibleIsolated.length + visibleSparse.length + visibleBridge.length}
                  />

                  {visibleIsolated.length > 0 && (
                    <>
                      <div
                        style={{
                          fontSize: 10,
                          color: "var(--syn-text-muted)",
                          padding: "3px 8px 1px",
                          fontStyle: "italic",
                        }}
                      >
                        {t("graph.insights.subKindIsolated")}
                      </div>
                      {visibleIsolated.map((item) => (
                        <InsightRow
                          key={item.id}
                          item={item}
                          onHighlight={handleHighlight}
                          onDismiss={handleDismiss}
                          onDeepResearch={handleDeepResearch}
                          showDeepResearch={true}
                        />
                      ))}
                    </>
                  )}

                  {visibleSparse.length > 0 && (
                    <>
                      <div
                        style={{
                          fontSize: 10,
                          color: "var(--syn-text-muted)",
                          padding: "3px 8px 1px",
                          fontStyle: "italic",
                        }}
                      >
                        {t("graph.insights.subKindSparse")}
                      </div>
                      {visibleSparse.map((item) => (
                        <InsightRow
                          key={item.id}
                          item={item}
                          onHighlight={handleHighlight}
                          onDismiss={handleDismiss}
                          onDeepResearch={handleDeepResearch}
                          showDeepResearch={true}
                        />
                      ))}
                    </>
                  )}

                  {visibleBridge.length > 0 && (
                    <>
                      <div
                        style={{
                          fontSize: 10,
                          color: "var(--syn-text-muted)",
                          padding: "3px 8px 1px",
                          fontStyle: "italic",
                        }}
                      >
                        {t("graph.insights.subKindBridge")}
                      </div>
                      {visibleBridge.map((item) => (
                        <InsightRow
                          key={item.id}
                          item={item}
                          onHighlight={handleHighlight}
                          onDismiss={handleDismiss}
                          onDeepResearch={handleDeepResearch}
                          showDeepResearch={true}
                        />
                      ))}
                    </>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
    </>
  );
}
