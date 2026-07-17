/**
 * GraphHeader.tsx — GR1-GR5, GR7 top toolbar for the graph viewer.
 * Move-only extraction from GraphViewer.tsx (FE-ARCH-1, 1.9.3 W2) — no behavior change.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Filter,
  Layers,
  Lightbulb,
  Maximize,
  Network,
  RefreshCw,
  RotateCcw,
  Search,
  Tag,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ColorMode } from "../graphPalette";
import type { GraphEdge, GraphNode } from "../../api/types";
import { edgeVisibilityThreshold } from "../../api/graphTransform";
import {
  ALL_NODE_TYPES,
  DEFAULT_NODE_COLOR,
  META_NODE_TYPES,
  TYPE_COLORS,
  edgeKey,
} from "./graphViewerShared";

// ─── GraphHeader (GR1–GR5, GR7) ─────────────────────────────────────────────
// ONE top toolbar row matching nashsu/llm_wiki 0.6.0 chrome layout.
// LEFT: Network icon + graph.title + stat pills (pages/links/hidden).
// RIGHT: icon-button group — Search (expands), Filter, Reset, Type, Community,
//        Insights (+ count badge), Refresh, Fullscreen.
// Color-mode toggle, regenerate and insights are consolidated here (removed from canvas overlays).
// All operations are client-side (I2 — no layout mutation).

interface GraphHeaderProps {
  /** In-graph nodes from the store payload (GET /graph nodes array) */
  nodes: GraphNode[];
  /** In-graph edges from the store payload (full graph edge set) */
  edges: GraphEdge[];
  /**
   * GR1: All live vault pages (pre-graph-inclusion, from GET /graph total_nodes field).
   * null = old backend that doesn't expose this field yet.
   */
  totalNodes: number | null;
  filterNodeTypes: Set<string>;
  toggleFilterNodeType: (type: string) => void;
  clearFilterNodeTypes: () => void;
  onSearch: (query: string) => void;
  onReset: () => void;
  onFullscreen: () => void;
  graphContainerRef: React.RefObject<HTMLDivElement | null>;
  /** Current color mode — drives active state on Type/Community buttons. */
  colorMode: ColorMode;
  /** Called when the user toggles color mode. */
  onSetColorMode: (mode: ColorMode) => void;
  /** Whether the GraphInsightsPanel overlay is currently visible. */
  showInsights: boolean;
  /** Called when the Insights button is clicked. */
  onToggleInsights: () => void;
  /** Total insight count for the badge (0 = no badge shown). */
  insightCount: number;
  /** Called to regenerate the graph (reconnect + recompute FA2). */
  onRefresh: () => void;
  /** True while regeneration is in flight (spins the RefreshCw icon). */
  regenerating: boolean;
  /** Optional status message to show briefly after regeneration. */
  regenMsg: string | null;
  // ── GI-2 (v1.3.14) visual filter props ─────────────────────────────────
  hideMetaTypes: boolean;
  onSetHideMetaTypes: (v: boolean) => void;
  hideIsolated: boolean;
  onSetHideIsolated: (v: boolean) => void;
  minLinks: number | null;
  onSetMinLinks: (v: number | null) => void;
  maxLinks: number | null;
  onSetMaxLinks: (v: number | null) => void;
  nodeSizeScale: number;
  onSetNodeSizeScale: (v: number) => void;
  spacingScale: number;
  onSetSpacingScale: (v: number) => void;
  /** Clear all active filter state (type + GI-2 fields). Called from the popover "Reset filters" button. */
  onClearAllFilters: () => void;
}

export const GraphHeader: React.FC<GraphHeaderProps> = ({
  nodes,
  edges,
  totalNodes,
  filterNodeTypes,
  toggleFilterNodeType,
  clearFilterNodeTypes: _clearFilterNodeTypes,
  onSearch,
  onReset,
  onFullscreen,
  graphContainerRef: _graphContainerRef,
  colorMode,
  onSetColorMode,
  showInsights,
  onToggleInsights,
  insightCount,
  onRefresh,
  regenerating,
  regenMsg,
  // GI-2 (v1.3.14) visual filter props
  hideMetaTypes,
  onSetHideMetaTypes,
  hideIsolated,
  onSetHideIsolated,
  minLinks,
  onSetMinLinks,
  maxLinks,
  onSetMaxLinks,
  nodeSizeScale,
  onSetNodeSizeScale,
  spacingScale,
  onSetSpacingScale,
  onClearAllFilters,
}) => {
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // GR1 + GI-2 — PAGES chip: counts nodes passing ALL active filters.
  // visibleNodeSet is computed once and reused for both node count and edge filtering (I3).
  const visibleNodeSet = React.useMemo(() => {
    const s = new Set<string>();
    for (const n of nodes) {
      const typeKey = n.type ?? "other";
      if (filterNodeTypes.size > 0 && !filterNodeTypes.has(typeKey)) continue;
      if (hideMetaTypes && META_NODE_TYPES.has(n.type ?? "")) continue;
      if (hideIsolated && (n.degree ?? 0) === 0) continue;
      if (minLinks !== null && (n.degree ?? 0) < minLinks) continue;
      if (maxLinks !== null && (n.degree ?? 0) > maxLinks) continue;
      s.add(n.id);
    }
    return s;
  }, [nodes, filterNodeTypes, hideMetaTypes, hideIsolated, minLinks, maxLinks]);

  const visibleNodes = visibleNodeSet.size;
  const displayTotalNodes = totalNodes ?? nodes.length;
  const hiddenCount = displayTotalNodes - visibleNodes;

  // GR1 — LINKS chip:
  //   denominator = edges.length (full graph edge set incl. source-overlap, e.g. 4213)
  //   numerator   = edges that are NOT culled by GL1 AND pass the type filter.
  //   GL1-culled edges (normalizedWeight < edgeVisibilityThreshold) count as hidden,
  //   matching nashsu/llm_wiki's "shown/total" display (P3 graph link-chip fix).
  //   GI-2: visibleNodeSet (computed above) is the single source of truth for node
  //   visibility — it accounts for all active filters (type, meta, isolated, min/max links).
  //   Both endpoints of an edge must be in visibleNodeSet for the edge to be counted.

  // GL1 normalised weights for the header chip — same formula as graphTransform.ts.
  // Computed once per edges change; NOT per frame (I3).
  // Key format: "sourceId__targetId" (order-independent via edgeKey helper below).
  const normalizedWeightMap = React.useMemo(() => {
    const m = new Map<string, number>();
    if (edges.length === 0) return m;
    let wMin = Infinity;
    let wMax = -Infinity;
    for (const e of edges) {
      if (e.weight < wMin) wMin = e.weight;
      if (e.weight > wMax) wMax = e.weight;
    }
    const range = wMax - wMin;
    for (const e of edges) {
      const nw = range === 0 ? 0.5 : (e.weight - wMin) / range;
      m.set(edgeKey(e.source, e.target), nw);
    }
    return m;
  }, [edges]);

  const visibleEdges = React.useMemo(() => {
    // GL1: edges below edgeVisibilityThreshold(nodeCount) are culled at rest.
    // The chip numerator must exclude GL1-culled edges AND edges whose endpoints are
    // filtered out by ANY active filter (type, meta, isolated, min/max links).
    // GI-2: use visibleNodeSet (which accounts for all active filters) instead of
    // a type-only check. Both endpoints must be in visibleNodeSet.
    // INVARIANT I2: read-only; no coord mutation.
    const threshold = edgeVisibilityThreshold(nodes.length);
    return edges.filter((e) => {
      const nw = normalizedWeightMap.get(edgeKey(e.source, e.target)) ?? 0.5;
      if (nw < threshold) return false;
      return visibleNodeSet.has(e.source) && visibleNodeSet.has(e.target);
    }).length;
  }, [edges, visibleNodeSet, nodes.length, normalizedWeightMap]);

  const totalEdgesCount = edges.length;

  // GR2: search handler — called on input change; finds the first matching node
  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const q = e.target.value;
      setSearchQuery(q);
      onSearch(q);
    },
    [onSearch],
  );

  const handleSearchClear = useCallback(() => {
    setSearchQuery("");
    onSearch("");
  }, [onSearch]);

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        setSearchQuery("");
        onSearch("");
        setSearchOpen(false);
        (e.currentTarget as HTMLInputElement).blur();
      }
    },
    [onSearch],
  );

  // Focus the search input when the search panel opens
  useEffect(() => {
    if (searchOpen && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [searchOpen]);

  // GR3: close filter popover on outside click
  useEffect(() => {
    if (!filterOpen) return;
    const handler = (e: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setFilterOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [filterOpen]);

  // GI-2: Filter button highlights when ANY filter is active (type, meta, isolated, links, size, spacing)
  const hasActiveFilter =
    filterNodeTypes.size > 0 ||
    hideMetaTypes ||
    hideIsolated ||
    minLinks !== null ||
    maxLinks !== null ||
    nodeSizeScale !== 1.0 ||
    spacingScale !== 1.0;
  const searchActive = searchQuery.length > 0;

  // Shared style factory for icon-only toolbar buttons (ghost / active states).
  // Following the reference: ghost = subtle bg + muted text; active = accent tint.
  const iconBtnStyle = (active = false, disabled = false): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    padding: 0,
    width: 26,
    height: 26,
    border: `1px solid ${active ? "var(--syn-accent)" : "var(--syn-border)"}`,
    borderRadius: 4,
    background: active
      ? "color-mix(in srgb, var(--syn-accent) 12%, var(--syn-surface))"
      : "var(--syn-surface)",
    color: active ? "var(--syn-accent)" : "var(--syn-text-muted)",
    cursor: disabled ? "default" : "pointer",
    opacity: disabled ? 0.55 : 1,
  });

  return (
    <div
      data-testid="graph-header"
      role="toolbar"
      aria-label={t("graph.title")}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
        padding: "5px 10px",
        borderBottom: "1px solid var(--syn-border)",
        background: "var(--syn-surface)",
        flexShrink: 0,
        minHeight: 38,
        // Must be above canvas overlays
        position: "relative",
        zIndex: 15,
      }}
    >
      {/* LEFT: Network icon + title + stat pills ───────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0, minWidth: 0 }}>
        {/* Title */}
        <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
          <Network
            size={14}
            style={{ color: "var(--syn-text-muted)", flexShrink: 0 }}
            aria-hidden="true"
          />
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "var(--syn-text)",
              whiteSpace: "nowrap",
            }}
          >
            {t("graph.title")}
          </span>
        </div>

        {/* GR1 stat pills — pages/links/hidden */}
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span
            data-testid="graph-header-nodes"
            style={{
              fontSize: 11,
              color: "var(--syn-text-muted)",
              background: "color-mix(in srgb, var(--syn-border) 40%, transparent)",
              borderRadius: 3,
              padding: "1px 5px",
              whiteSpace: "nowrap",
            }}
          >
            {visibleNodes}/{displayTotalNodes} {t("graph.header.pages")}
          </span>
          {totalEdgesCount > 0 && (
            <span
              data-testid="graph-header-edges"
              style={{
                fontSize: 11,
                color: "var(--syn-text-muted)",
                background: "color-mix(in srgb, var(--syn-border) 40%, transparent)",
                borderRadius: 3,
                padding: "1px 5px",
                whiteSpace: "nowrap",
              }}
            >
              {visibleEdges}/{totalEdgesCount} {t("graph.header.links")}
            </span>
          )}
          {hiddenCount > 0 && (
            <span
              data-testid="graph-header-hidden"
              style={{
                fontSize: 11,
                color: "var(--syn-warn-text)",
                background: "color-mix(in srgb, var(--syn-amber) 12%, var(--syn-mix-base) 90%)",
                border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, transparent)",
                borderRadius: 3,
                padding: "1px 5px",
                whiteSpace: "nowrap",
              }}
            >
              {hiddenCount} {t("graph.header.hidden")}
            </span>
          )}
        </div>
      </div>

      {/* RIGHT: control icon buttons ────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 3, flexShrink: 0 }}>
        {/* GR2: Search — expanding input; collapses back to icon when closed */}
        {(searchOpen || searchActive) && (
          <div style={{ position: "relative", flexShrink: 0, marginRight: 2 }}>
            <Search
              size={12}
              style={{
                position: "absolute",
                left: 7,
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--syn-text-muted)",
                pointerEvents: "none",
              }}
              aria-hidden="true"
            />
            <input
              ref={searchInputRef}
              data-testid="graph-search-input"
              type="text"
              value={searchQuery}
              onChange={handleSearchChange}
              onKeyDown={handleSearchKeyDown}
              placeholder={t("graph.header.searchPlaceholder")}
              aria-label={t("graph.header.searchPlaceholder")}
              style={{
                fontSize: 11,
                padding: "3px 22px 3px 22px",
                border: "1px solid var(--syn-border)",
                borderRadius: 4,
                background: "var(--syn-bg)",
                color: "var(--syn-text)",
                width: 160,
              }}
            />
            {/* Clear / collapse button */}
            <button
              type="button"
              onClick={() => {
                if (searchActive) {
                  handleSearchClear();
                } else {
                  setSearchOpen(false);
                }
              }}
              aria-label={searchActive ? t("graph.header.clearFilter") : t("common.close")}
              style={{
                position: "absolute",
                right: 4,
                top: "50%",
                transform: "translateY(-50%)",
                background: "none",
                border: "none",
                color: "var(--syn-text-dim)",
                cursor: "pointer",
                padding: 0,
                display: "flex",
                alignItems: "center",
                lineHeight: 1,
              }}
            >
              <X size={11} aria-hidden="true" />
            </button>
          </div>
        )}

        {/* Search toggle icon */}
        <button
          type="button"
          data-testid="graph-search-toggle"
          onClick={() => setSearchOpen((o) => !o)}
          aria-label={t("graph.header.searchPlaceholder")}
          title={t("graph.header.searchPlaceholder")}
          style={iconBtnStyle(searchOpen || searchActive)}
        >
          <Search size={13} strokeWidth={1.8} aria-hidden="true" />
        </button>

        {/* GR3: Filter — icon button with expanding popover */}
        <div ref={filterRef} style={{ position: "relative", flexShrink: 0 }}>
          {/* G1 (v1.3.14 parity): icon + text label, matching llm_wiki toolbar style */}
          <button
            type="button"
            data-testid="graph-filter-button"
            onClick={() => setFilterOpen((o) => !o)}
            aria-label={t("graph.header.filter")}
            title={t("graph.header.filter")}
            aria-expanded={filterOpen}
            style={{
              ...iconBtnStyle(hasActiveFilter || filterOpen),
              width: "auto",
              padding: "0 6px",
              gap: 4,
            }}
          >
            <Filter size={13} strokeWidth={1.8} aria-hidden="true" />
            <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>{t("graph.header.filter")}</span>
          </button>

          {filterOpen && (
            /* GI-2 (v1.3.14): expanded filter popover matching llm_wiki panel structure.
               Sections: (1) Quick filters, (2) Min/Max links, (3) Display tuning,
               (4) Node types (existing), (5) Summary line. */
            <div
              data-testid="graph-filter-popover"
              style={{
                position: "absolute",
                top: "calc(100% + 4px)",
                right: 0,
                background: "var(--syn-surface)",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                padding: "8px 10px",
                zIndex: 20,
                minWidth: 200,
                maxWidth: 280,
                boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
              }}
            >
              {/* ── 1. Quick Filters ── */}
              <div
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  letterSpacing: "0.06em",
                  fontWeight: 600,
                  marginBottom: 5,
                }}
              >
                {t("graph.filter.quickFilters")}
              </div>
              <label
                data-testid="graph-filter-hide-meta"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "3px 2px",
                  cursor: "pointer",
                  fontSize: 11,
                  color: "var(--syn-text)",
                  marginBottom: 2,
                }}
              >
                <input
                  type="checkbox"
                  checked={hideMetaTypes}
                  onChange={() => onSetHideMetaTypes(!hideMetaTypes)}
                  style={{ width: 12, height: 12, cursor: "pointer", flexShrink: 0 }}
                />
                {t("graph.filter.hideMeta")}
              </label>
              <label
                data-testid="graph-filter-hide-isolated"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "3px 2px",
                  cursor: "pointer",
                  fontSize: 11,
                  color: "var(--syn-text)",
                }}
              >
                <input
                  type="checkbox"
                  checked={hideIsolated}
                  onChange={() => onSetHideIsolated(!hideIsolated)}
                  style={{ width: 12, height: 12, cursor: "pointer", flexShrink: 0 }}
                />
                {t("graph.filter.hideIsolated")}
              </label>

              {/* ── 2. Min / Max Links ── */}
              <div
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  letterSpacing: "0.06em",
                  fontWeight: 600,
                  marginTop: 8,
                  marginBottom: 4,
                }}
              >
                {t("graph.filter.minLinks")} / {t("graph.filter.maxLinks")}
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input
                  type="number"
                  data-testid="graph-filter-min-links"
                  value={minLinks ?? ""}
                  placeholder={t("graph.filter.any")}
                  min={0}
                  onChange={(e) => {
                    const v = e.target.value.trim();
                    onSetMinLinks(v === "" ? null : Math.max(0, parseInt(v, 10)));
                  }}
                  aria-label={t("graph.filter.minLinks")}
                  style={{
                    width: 72,
                    fontSize: 11,
                    padding: "2px 4px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 3,
                    background: "var(--syn-bg)",
                    color: "var(--syn-text)",
                  }}
                />
                <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>–</span>
                <input
                  type="number"
                  data-testid="graph-filter-max-links"
                  value={maxLinks ?? ""}
                  placeholder={t("graph.filter.any")}
                  min={0}
                  onChange={(e) => {
                    const v = e.target.value.trim();
                    onSetMaxLinks(v === "" ? null : Math.max(0, parseInt(v, 10)));
                  }}
                  aria-label={t("graph.filter.maxLinks")}
                  style={{
                    width: 72,
                    fontSize: 11,
                    padding: "2px 4px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 3,
                    background: "var(--syn-bg)",
                    color: "var(--syn-text)",
                  }}
                />
              </div>

              {/* ── 3. Display Tuning ── */}
              <div
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  letterSpacing: "0.06em",
                  fontWeight: 600,
                  marginTop: 8,
                  marginBottom: 4,
                }}
              >
                {t("graph.filter.displayTuning")}
              </div>
              <div style={{ marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                  <span style={{ fontSize: 11, color: "var(--syn-text)" }}>
                    {t("graph.filter.nodeSize")}
                  </span>
                  <span
                    style={{
                      fontSize: 10,
                      color: "var(--syn-text-muted)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {Math.round(nodeSizeScale * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  data-testid="graph-filter-node-size"
                  min={0}
                  max={2}
                  step={0.05}
                  value={nodeSizeScale}
                  onChange={(e) => onSetNodeSizeScale(parseFloat(e.target.value))}
                  aria-label={t("graph.filter.nodeSize")}
                  style={{ width: "100%", cursor: "pointer" }}
                />
              </div>
              <div style={{ marginBottom: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                  <span style={{ fontSize: 11, color: "var(--syn-text)" }}>
                    {t("graph.filter.spacing")}
                  </span>
                  <span
                    style={{
                      fontSize: 10,
                      color: "var(--syn-text-muted)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {Math.round(spacingScale * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  data-testid="graph-filter-spacing"
                  min={0}
                  max={2}
                  step={0.05}
                  value={spacingScale}
                  onChange={(e) => onSetSpacingScale(parseFloat(e.target.value))}
                  aria-label={t("graph.filter.spacing")}
                  style={{ width: "100%", cursor: "pointer" }}
                />
              </div>
              <p
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  margin: "2px 0 0",
                  lineHeight: 1.4,
                }}
              >
                {t("graph.filter.spacingHelp")}
              </p>

              {/* ── 4. Node Types ── */}
              <div
                style={{
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  letterSpacing: "0.06em",
                  fontWeight: 600,
                  marginTop: 8,
                  marginBottom: 5,
                }}
              >
                {t("graph.header.filterNodeTypes")}
              </div>
              {ALL_NODE_TYPES.map((type) => {
                const checked = filterNodeTypes.size === 0 || filterNodeTypes.has(type);
                const count = nodes.filter((n) => (n.type ?? "other") === type).length;
                if (count === 0) return null;
                const color = TYPE_COLORS[type] ?? DEFAULT_NODE_COLOR;
                return (
                  <label
                    key={type}
                    data-testid={`graph-filter-type-${type}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "3px 2px",
                      cursor: "pointer",
                      borderRadius: 3,
                      fontSize: 11,
                      color: "var(--syn-text)",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleFilterNodeType(type)}
                      style={{ width: 12, height: 12, cursor: "pointer", flexShrink: 0 }}
                    />
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: color,
                        flexShrink: 0,
                      }}
                      aria-hidden="true"
                    />
                    <span style={{ flex: 1, textTransform: "capitalize" }}>{type}</span>
                    <span style={{ fontSize: 10, color: "var(--syn-text-dim)", flexShrink: 0 }}>
                      {count}
                    </span>
                  </label>
                );
              })}

              {/* ── 5. Summary + Reset ── */}
              <div
                data-testid="graph-filter-summary"
                style={{
                  marginTop: 8,
                  fontSize: 10,
                  color: "var(--syn-text-muted)",
                  lineHeight: 1.4,
                  borderTop: "1px solid var(--syn-border)",
                  paddingTop: 6,
                }}
              >
                {t("graph.filter.summary", {
                  visibleNodes,
                  totalNodes: displayTotalNodes,
                  visibleEdges,
                  totalEdges: totalEdgesCount,
                })}
              </div>
              {hasActiveFilter && (
                <button
                  type="button"
                  onClick={() => {
                    onClearAllFilters();
                    setFilterOpen(false);
                  }}
                  style={{
                    marginTop: 6,
                    fontSize: 11,
                    padding: "3px 8px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 3,
                    background: "none",
                    color: "var(--syn-text-muted)",
                    cursor: "pointer",
                    width: "100%",
                  }}
                >
                  {t("graph.header.clearFilter")}
                </button>
              )}
            </div>
          )}
        </div>

        {/* GR4: Reset — icon + text label (G1 v1.3.14 parity) */}
        <button
          type="button"
          data-testid="graph-header-reset"
          onClick={onReset}
          aria-label={t("graph.header.reset")}
          title={t("graph.header.reset")}
          style={{ ...iconBtnStyle(), width: "auto", padding: "0 6px", gap: 4 }}
        >
          <RotateCcw size={13} strokeWidth={1.8} aria-hidden="true" />
          <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>{t("graph.header.reset")}</span>
        </button>

        {/* Color mode: Type (Tag icon + text label, G1 v1.3.14 parity) */}
        <button
          type="button"
          data-testid="color-mode-type"
          onClick={() => onSetColorMode("type")}
          aria-pressed={colorMode === "type"}
          aria-label={t("graph.colorModeType")}
          title={t("graph.colorModeType")}
          style={{ ...iconBtnStyle(colorMode === "type"), width: "auto", padding: "0 6px", gap: 4 }}
        >
          <Tag size={13} strokeWidth={1.8} aria-hidden="true" />
          <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>{t("graph.colorModeType")}</span>
        </button>

        {/* Color mode: Community (Layers icon + text label, G1 v1.3.14 parity) */}
        <button
          type="button"
          data-testid="color-mode-community"
          onClick={() => onSetColorMode("community")}
          aria-pressed={colorMode === "community"}
          aria-label={t("graph.colorModeCommunity")}
          title={t("graph.colorModeCommunity")}
          style={{
            ...iconBtnStyle(colorMode === "community"),
            width: "auto",
            padding: "0 6px",
            gap: 4,
          }}
        >
          <Layers size={13} strokeWidth={1.8} aria-hidden="true" />
          <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>
            {t("graph.colorModeCommunity")}
          </span>
        </button>

        {/* Insights toggle — Lightbulb + text label + count badge (G1 v1.3.14 parity).
            Label reads "Insights" with inline count when > 0: "Insights 13" style.
            NOTE (deliberate Synapse improvement): insightCount is NOT computed on load —
            it's populated lazily when the panel opens (I3: avoids main-thread work at load time). */}
        {nodes.length > 0 && (
          <button
            type="button"
            data-testid="graph-insights-toggle"
            onClick={onToggleInsights}
            aria-pressed={showInsights}
            aria-label={t("graph.insightsButton")}
            title={t("graph.insightsButton")}
            style={{
              ...iconBtnStyle(showInsights),
              width: "auto",
              padding: "0 6px",
              gap: 4,
              display: "flex",
              alignItems: "center",
            }}
          >
            <Lightbulb size={13} strokeWidth={1.8} aria-hidden="true" />
            <span style={{ fontSize: 11, whiteSpace: "nowrap" }}>{t("graph.insightsButton")}</span>
            {insightCount > 0 && (
              <span
                style={{
                  fontSize: 10,
                  lineHeight: 1.4,
                  minWidth: 14,
                  textAlign: "center",
                  borderRadius: 8,
                  padding: "0 4px",
                  background: showInsights
                    ? "color-mix(in srgb, var(--syn-accent) 20%, transparent)"
                    : "color-mix(in srgb, var(--syn-border) 50%, transparent)",
                  color: showInsights ? "var(--syn-accent)" : "var(--syn-text-muted)",
                }}
              >
                {insightCount}
              </span>
            )}
          </button>
        )}

        {/* Optional regen status message (brief; auto-cleared by parent) */}
        {regenMsg !== null && (
          <span
            style={{
              fontSize: 10,
              color: "var(--syn-text-muted)",
              maxWidth: 110,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            role="status"
            aria-live="polite"
            data-testid="graph-regenerate-msg"
          >
            {regenMsg}
          </span>
        )}

        {/* Refresh / Regenerate — RefreshCw icon (spins while regenerating) */}
        <button
          type="button"
          data-testid="graph-regenerate"
          onClick={onRefresh}
          disabled={regenerating}
          aria-label={t("graph.regenerate")}
          title={t("graph.regenerateTitle")}
          style={iconBtnStyle(false, regenerating)}
        >
          <RefreshCw
            size={13}
            strokeWidth={1.8}
            aria-hidden="true"
            style={regenerating ? { animation: "syn-spin 0.9s linear infinite" } : undefined}
          />
        </button>

        {/* GR7: Fullscreen */}
        <button
          type="button"
          data-testid="graph-header-fullscreen"
          onClick={onFullscreen}
          aria-label={t("graph.header.fullscreen")}
          title={t("graph.header.fullscreen")}
          style={iconBtnStyle()}
        >
          <Maximize size={13} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
};
