/**
 * GraphViewer.tsx — Obsidian-style sigma.js WebGL knowledge-graph viewer.
 *
 * INVARIANT I2 (ADR-0015 HARD RULE — P0 block):
 *   This component MUST NOT import or call any client-side layout algorithm.
 *   Forbidden: graphology-layout-forceatlas2, d3-force, @antv/layout,
 *              any rAF loop that mutates node x/y.
 *   Node positions come EXCLUSIVELY from the Zustand store (which reflects
 *   the server's precomputed FA2 coords from GET /graph).
 *
 * INVARIANT I4 (ADR-0015 §5):
 *   sigma draws ALL nodes/edges in a SINGLE WebGL canvas.
 *   DOM node count in the graph container is < 20, regardless of graph size.
 *
 * INVARIANT I3 (ADR-0015 §3):
 *   All Zustand subscriptions use typed selectors with shallow equality only.
 *   No whole-store subscription.
 *
 * G2 by construction: no layout → no main-thread long task > 50ms.
 * G4 by construction: single WebGL canvas → bounded DOM (<20 nodes in container).
 *
 * Accessibility:
 *   - container role="application" aria-label="Knowledge graph"
 *   - aria-live="polite" region announces selected node (title, type, neighbor count)
 *   - Label contrast #1f2328 on #ffffff ≈ 18:1 (AAA) — light theme
 *   - CVD-safe type palette with redundant encoding (name in legend + tooltip text)
 *   - prefers-reduced-motion respected for all camera animations
 *
 * LIGHT THEME NOTE (sigma node colors):
 *   sigma.js requires concrete color strings, not CSS custom properties.
 *   TYPE_COLORS below uses hex values that exactly match the --syn-type-* tokens
 *   defined in theme.css. This is the ONE allowed exception to "tokens only"
 *   (documented in ADR-0015 §CVD-SAFE): sigma cannot resolve CSS vars at draw time.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ZoomIn, ZoomOut, Maximize2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  colorForCommunity,
  colorForDomain,
  COMMUNITY_PALETTE,
  LOW_COHESION_THRESHOLD,
} from "./graphPalette";
import type { ColorMode, GraphTheme } from "./graphPalette";
import {
  computeCommunityCentroids,
  computeDomainCentroids,
  communityDisplayName,
} from "./graphCommunityUtils";
import type { CommunityCentroid } from "./graphCommunityUtils";
import Sigma from "sigma";
import type { Attributes } from "graphology-types";
import type { Settings } from "sigma/settings";
import type { NodeDisplayData } from "sigma/types";
import { buildGraphologyGraph } from "../api/graphTransform";
import { computeGraphInsights } from "./graph/graphInsights";
import { fetchGraph, patchNodePosition, recomputeGraph } from "../api/graphClient";
import type { EdgeDetail } from "../api/graphClient";
import type { GraphEdge, GraphNode } from "../api/types";
import {
  selectCommunities,
  selectEdges,
  selectNodes,
  selectSetError,
  selectSetGraph,
  selectSetLoading,
  selectFilterNodeTypes,
  selectToggleFilterNodeType,
  selectClearFilterNodeTypes,
  selectTotalNodes,
  selectHideMetaTypes,
  selectHideIsolated,
  selectMinLinks,
  selectMaxLinks,
  selectNodeSizeScale,
  selectSpacingScale,
  selectSetHideMetaTypes,
  selectSetHideIsolated,
  selectSetMinLinks,
  selectSetMaxLinks,
  selectSetNodeSizeScale,
  selectSetSpacingScale,
  selectClearAllGraphFilters,
  useGraphStore,
} from "../store/graphStore";
import {
  selectSelectedNodeId,
  selectSetSelectedNodeId,
  selectVaultId,
  selectSelectPage,
  selectSetActiveSection,
  selectShowInsightsPanel,
  selectSetShowInsightsPanel,
  useAppStore,
} from "../store/appStore";
import { useStatusStore, selectStatusDataVersion } from "../store/statusStore";
import { GraphHeader } from "./graph/GraphHeader";
import { GraphLegend } from "./graph/GraphLegend";
import { CentroidOverlay } from "./graph/CentroidOverlay";
import { StatusBar } from "./graph/StatusBar";
import { CommunityPanel } from "./graph/CommunityPanel";
import { NodeTooltip } from "./graph/NodeTooltip";
import { EdgeBreakdownTooltip } from "./graph/EdgeBreakdownTooltip";
import {
  reducedMotion,
  readSigmaThemeColors,
  colorForType,
  deepenColor,
  DEFAULT_NODE_COLOR,
  makeDrawHaloNodeLabel,
  makeDrawHaloNodeHover,
  META_NODE_TYPES,
} from "./graph/graphViewerShared";
import type { SigmaThemeColors } from "./graph/graphViewerShared";

// ─── RT-3: minimum interval between version-driven graph re-fetches ───────────
// During a long ingest the data_version can bump on every poll tick (3s cadence).
// Re-fetching the full graph on every bump is jittery and wasteful; a 10s minimum
// interval means we update the graph at most 6×/minute. HomeDashboard stats are
// cheap JSON and are NOT throttled (still re-fetch on every bump).
const GRAPH_REFETCH_MIN_MS = 10_000;

// ─── Re-export community/domain palette + centroid utilities for test isolation ─
// These are all imported from pure modules (no sigma dependency) so they can be
// unit-tested in jsdom without WebGL2. See graphPalette.ts, graphCommunityUtils.ts.
export { COMMUNITY_PALETTE, LOW_COHESION_THRESHOLD, colorForCommunity, colorForDomain };
export type { ColorMode };
// computeCommunityCentroids / computeDomainCentroids / communityDisplayName re-exported
// from graphCommunityUtils for tests that import from GraphViewer directly (backward compat).
export { computeCommunityCentroids, computeDomainCentroids, communityDisplayName };

// ─── Hover-dim state (closed-over, NOT React state — no re-render on hover) ────

interface HoverState {
  hoveredNode: string | null;
  hoveredNeighbors: Set<string> | null;
  selectedNode: string | null;
}

// ─── Drag state (closed-over, NOT React state — no re-render during drag) ─────
//
// INVARIANT I2 note: drag moves ONLY the ONE dragged node by writing its x/y
// directly onto the graphology graph. No layout algorithm is invoked. No other
// node is moved. No rAF physics loop is started. The final position is persisted
// to the server via PATCH /pages/{id}/position so it survives the next GET /graph.

interface DragState {
  /** Node currently being dragged, or null */
  draggedNode: string | null;
  /** True once the pointer has moved enough to count as a drag (not a click) */
  hasMoved: boolean;
  /** Viewport-space pointer position at drag start (for the movement threshold) */
  downX: number;
  downY: number;
  /** Set when endDrag just handled a node CLICK. Sigma mis-classifies node clicks as stage clicks
   *  (clickNode never fires; clickStage fires right after upNode), so without this the clickStage
   *  handler would wipe the selection endDrag just made. clickStage consumes+resets this flag. */
  suppressStageClick: boolean;
}

/**
 * Minimum viewport-pixel movement before a pointer-down on a node is treated as a
 * drag (and thus persisted + pinned). Below this, it is a click/tap. This stops a
 * mobile tap with slight touch jitter from accidentally pinning a node at its current
 * (possibly runaway) coordinates — the root cause of the "graph collapsed to the
 * center" bug (the server-side outlier clamp is the safety net; this is prevention).
 */
const DRAG_THRESHOLD_PX = 5;

// ─── Main GraphViewer ──────────────────────────────────────────────────────────

interface TooltipState {
  nodeId: string;
  position: { x: number; y: number };
  neighborCount: number;
}

/**
 * GraphViewer — Obsidian-style sigma.js WebGL knowledge-graph viewer.
 *
 * DOM structure (I4 — <20 nodes in container):
 *   <div#graph-root role="application">  ← container
 *     <div#sigma-container>              ← sigma mounts its WebGL canvas here
 *       <canvas>                         ← ONE WebGL canvas (sigma owns this)
 *     </div>
 *     <div aria-live="polite">           ← accessible selection announcements
 *     <StatusBar />                      ← absolute overlay
 *     <GraphLegend />                    ← absolute overlay
 *     <NodeTooltip />                    ← conditional, single element
 *   </div>
 */

export const GraphViewer: React.FC = () => {
  const { t } = useTranslation();
  // I3: typed selectors — never subscribe to whole store
  const nodes = useGraphStore(selectNodes);
  const edges = useGraphStore(selectEdges);
  const communities = useGraphStore(selectCommunities);
  const vaultId = useAppStore(selectVaultId);
  const selectedNodeId = useAppStore(selectSelectedNodeId);
  const setGraph = useGraphStore(selectSetGraph);
  const setLoading = useGraphStore(selectSetLoading);
  const setError = useGraphStore(selectSetError);
  const setSelectedNodeId = useAppStore(selectSetSelectedNodeId);
  // GR3: node-type filter from store (I2-safe: visibility only, never re-layout)
  // Use a ref so the sigma reducers always read the latest filter without rebuilding sigma.
  const filterNodeTypes = useGraphStore(selectFilterNodeTypes);
  const filterNodeTypesRef = useRef<Set<string>>(filterNodeTypes);
  // GI-2 (v1.3.14): extended filter state from store — declared BEFORE the refs below
  // that capture them, to avoid a temporal-dead-zone (the refs' initial useRef(...) reads
  // these values). Live-preview caught this ordering bug in the initial GI-2 draft.
  const hideMetaTypes = useGraphStore(selectHideMetaTypes);
  const hideIsolated = useGraphStore(selectHideIsolated);
  const minLinks = useGraphStore(selectMinLinks);
  const maxLinks = useGraphStore(selectMaxLinks);
  const nodeSizeScale = useGraphStore(selectNodeSizeScale);
  const spacingScale = useGraphStore(selectSpacingScale);
  // GI-2: refs for extended filter state so sigma reducers always read latest values
  // without rebuilding sigma on every filter change (I3: no re-render per frame).
  const hideMetaTypesRef = useRef<boolean>(hideMetaTypes);
  const hideIsolatedRef = useRef<boolean>(hideIsolated);
  const minLinksRef = useRef<number | null>(minLinks);
  const maxLinksRef = useRef<number | null>(maxLinks);
  const nodeSizeScaleRef = useRef<number>(nodeSizeScale);
  // Persistent selection: nodeReducer reads this ref so the clicked node keeps a
  // ring + label at rest (not just on hover). Ref (not state) so the reducer sees
  // the latest value without rebuilding sigma — same pattern as filterNodeTypesRef.
  const selectedNodeIdRef = useRef<string | null>(null);
  const toggleFilterNodeType = useGraphStore(selectToggleFilterNodeType);
  const clearFilterNodeTypes = useGraphStore(selectClearFilterNodeTypes);
  const setHideMetaTypes = useGraphStore(selectSetHideMetaTypes);
  const setHideIsolated = useGraphStore(selectSetHideIsolated);
  const setMinLinks = useGraphStore(selectSetMinLinks);
  const setMaxLinks = useGraphStore(selectSetMaxLinks);
  const setNodeSizeScale = useGraphStore(selectSetNodeSizeScale);
  const setSpacingScale = useGraphStore(selectSetSpacingScale);
  const clearAllGraphFilters = useGraphStore(selectClearAllGraphFilters);
  // GR1: total vault pages from backend (null = old server)
  const totalNodes = useGraphStore(selectTotalNodes);
  // GR2: selectPage action for search-triggered navigation
  const selectPage = useAppStore(selectSelectPage);
  // Click-to-open: navigate to the wiki pages section for the clicked node (Obsidian-style).
  const setActiveSection = useAppStore(selectSetActiveSection);

  // WS-A [F4/F16]: subscribe to data_version from the ActivityBar's existing GET /status poll.
  // When the version bumps, we re-fetch GET /graph (precomputed coords from server — I2).
  // INVARIANT I3: no re-render on every poll tick; only triggers when the value changes.
  // INVARIANT I2: we NEVER run a layout algorithm — we only refetch server-computed coords.
  // INVARIANT AC-WS-A-4: no new poller; ActivityBar's STATUS_POLL_MS is the sole driver.
  const statusDataVersion = useStatusStore(selectStatusDataVersion);

  // Track which data_version the current graph data corresponds to so we only
  // refetch when the server version actually advances (AC-WS-A-3).
  const lastFetchedGraphVersionRef = useRef<number | null>(null);

  // RT-3: timestamp of the last version-driven graph re-fetch (milliseconds).
  // Prevents re-fetching on every version bump during a long ingest — throttled
  // to at most once per GRAPH_REFETCH_MIN_MS. The initial mount fetch is exempt.
  const lastGraphRefetchTimeRef = useRef<number>(0);

  // UX-1: true only while a version-bump-triggered graph re-fetch is in-flight
  // (NOT during the initial mount fetch which shows its own skeleton).
  const [isGraphRefetching, setIsGraphRefetching] = useState(false);

  // Graph container ref — used for fullscreen API (GR7)
  const graphRootRef = useRef<HTMLDivElement>(null);

  // sigma container ref — sigma mounts ONE WebGL <canvas> inside this div (I4)
  const containerRef = useRef<HTMLDivElement>(null);
  // sigma instance ref — kept outside React state to avoid re-render on mount
  const sigmaRef = useRef<Sigma<Attributes, Attributes, Attributes> | null>(null);

  // FE-RT-1: latest nodes/edges, synced on every render (NOT via useEffect) so the
  // mount/rebuild effect below can read fresh data without depending on [nodes, edges]
  // directly — that dependency was the root cause of the kill+rebuild-every-refetch bug.
  const latestNodesRef = useRef<GraphNode[]>(nodes);
  const latestEdgesRef = useRef<GraphEdge[]>(edges);
  latestNodesRef.current = nodes;
  latestEdgesRef.current = edges;
  // True once the sigma instance has been constructed at least once — used to gate
  // the initial-load camera animatedReset() (never on a background data diff/rebuild).
  const hasMountedSigmaRef = useRef(false);

  // Resolved sigma theme colors — updated on mount and on theme change (ADR-0048 §T1)
  // React state drives re-render so sigma can be re-instantiated with correct colors.
  const [sigmaThemeColors, setSigmaThemeColors] = React.useState<SigmaThemeColors>(() =>
    readSigmaThemeColors(),
  );

  // Color-mode toggle: "community" (default — colors by Louvain community id, one distinct
  // color per cluster from COMMUNITY_PALETTE) or "type" (colors by page type).
  // Community names in legend + centroid overlay use communityDisplayName(c) which forms
  // "{dominant_domain} · {top_page_subtopic}" — unique per cluster, no duplicate SAM rows.
  const [colorMode, setColorMode] = useState<ColorMode>("type");

  // Resolved app theme discriminant (W4 audit FE-GRAPH-1) — selects the light or dark
  // community/domain palette (graphPalette.ts) for the legend + centroid overlay so
  // colors stay legible on the dark canvas instead of washing out. Kept in sync with
  // sigmaThemeColors by the same MutationObserver below.
  const [graphTheme, setGraphTheme] = React.useState<GraphTheme>(() =>
    document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light",
  );

  // Tooltip state (React state — triggers re-render to show/hide tooltip)
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  // Aria-live announcement text
  const [announcement, setAnnouncement] = useState<string>("");

  // ── Community centroid map (I3 — memoized; recomputed only when nodes/communities change)
  // Active in "community" color mode. Groups nodes by Louvain community id (server-provided).
  // Labels come from communityDisplayName(c) — unique "{domain} · {subtopic}" names.
  // INVARIANT I2: only reads server-provided x/y and community field — never mutates coords.
  // INVARIANT I3: computed once per nodes/communities change, NOT per sigma frame.
  const communityCentroids = useMemo(() => {
    // Convert number-keyed Map<number, CommunityCentroid> → string-keyed Map<string, CommunityCentroid>
    // because CentroidOverlay uses string keys (supports both community and domain modes).
    const raw = computeCommunityCentroids(nodes, communities, graphTheme);
    const result = new Map<string, CommunityCentroid>();
    for (const [cid, centroid] of raw) {
      result.set(String(cid), centroid);
    }
    return result;
  }, [nodes, communities, graphTheme]);

  // ── R9-5: Community panel state ──────────────────────────────────────────
  const [communityPanel, setCommunityPanel] = useState<{ id: number; color: string } | null>(null);

  // ── R9-5: Edge breakdown tooltip state ──────────────────────────────────
  interface EdgeTooltipState {
    srcId: string;
    tgtId: string;
    position: { x: number; y: number };
  }
  const [edgeTooltip, setEdgeTooltip] = useState<EdgeTooltipState | null>(null);
  // Per-pair cache: key = "srcId__tgtId" or "tgtId__srcId"
  const edgeDetailCache = useRef<Map<string, EdgeDetail>>(new Map());

  const handleEdgeCached = useCallback((key: string, detail: EdgeDetail) => {
    edgeDetailCache.current.set(key, detail);
  }, []);

  // Regenerate-graph control state (reresolve links + recompute FA2, then refetch coords)
  const [regenerating, setRegenerating] = useState(false);
  const [regenMsg, setRegenMsg] = useState<string | null>(null);

  // ── Regenerate graph: reconnect cross-ingest links → server recomputes FA2 → refetch ──
  const handleRegenerate = useCallback(async () => {
    if (regenerating) return;
    setRegenerating(true);
    setRegenMsg(null);
    try {
      // 1. Reconnect cross-ingest links + FORCE a fresh server-side FA2 recompute (I2).
      //    Forcing (not just reresolve) guarantees the layout re-runs — so the outlier
      //    clamp takes effect and the graph stops collapsing to a dot.
      const result = await recomputeGraph();
      // 2. Refetch the freshly-computed precomputed coords (I2 — layout stays server-side).
      const { data, cacheStatus } = await fetchGraph(vaultId);
      setGraph(
        data.nodes,
        data.edges,
        data.data_version,
        cacheStatus,
        data.communities ?? [],
        data.total_nodes ?? null,
        data.total_edges ?? null,
      );
      setRegenMsg(
        result.reconnected > 0
          ? t("graph.regenerateDone", { count: result.reconnected })
          : t("graph.regenerateNone"),
      );
    } catch (err: unknown) {
      setRegenMsg(t("graph.regenerateError"));
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
      }
    } finally {
      setRegenerating(false);
    }
  }, [regenerating, vaultId, setGraph, setError, t]);

  // ── Fetch graph on mount / vaultId change ────────────────────────────────
  //
  // P2 — skip redundant fetch when the Zustand store already holds current data.
  //
  // On a REVISIT (navigate away → navigate back), the component unmounts/remounts
  // but the graphStore retains its nodes + dataVersion from the previous fetch.
  // If the store's dataVersion matches the latest statusDataVersion (from the
  // ActivityBar's existing /status poll), the data is already current and we can
  // rebuild sigma directly from the store without a network round-trip.
  //
  // Guards:
  //  - storeNodes.length > 0        : store actually has data (not first load)
  //  - storeDataVersion !== null     : store knows its version
  //  - currentStatusVersion !== null : status store has polled at least once
  //  - versions match                : data is confirmed current
  //
  // Read store state imperatively via .getState() — this avoids adding reactive
  // deps to the effect and keeps I3 clean (no extra subscriptions).
  //
  // INVARIANT I2: no layout algorithm invoked — sigma rebuilds from precomputed
  // server coords stored in the Zustand nodes array (unchanged).

  useEffect(() => {
    // P2: cache-hit check — skip fetch when store data is already at the current version.
    const { nodes: storeNodes, dataVersion: storeDataVersion } = useGraphStore.getState();
    const currentStatusVersion = useStatusStore.getState().dataVersion;

    if (
      storeNodes.length > 0 &&
      storeDataVersion !== null &&
      currentStatusVersion !== null &&
      storeDataVersion === currentStatusVersion
    ) {
      // Store data is current. Sigma will rebuild from the existing nodes array.
      // Initialise the WS-A ref so a same-version status tick doesn't trigger a
      // redundant re-fetch via the WS-A effect below (AC-WS-A-3).
      lastFetchedGraphVersionRef.current = storeDataVersion;
      return; // no AbortController cleanup needed
    }

    const ctrl = new AbortController();
    setLoading(true);

    fetchGraph(vaultId, ctrl.signal)
      .then(({ data, cacheStatus }) => {
        setGraph(
          data.nodes,
          data.edges,
          data.data_version,
          cacheStatus,
          data.communities ?? [],
          data.total_nodes ?? null,
          data.total_edges ?? null,
        );
        // WS-A: record the server version just fetched so we don't re-fetch on same-version ticks.
        lastFetchedGraphVersionRef.current = data.data_version;
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setError(err.message);
        }
      });

    return () => ctrl.abort();
  }, [vaultId, setGraph, setLoading, setError]);

  // ── WS-A [AC-WS-A-2, AC-WS-A-3]: re-fetch graph when data_version bumps ──
  // Polls via the existing ActivityBar /status cadence — no new interval (AC-WS-A-4).
  // Skips re-fetch if the version hasn't changed from last graph fetch (AC-WS-A-3).
  // INVARIANT I2: only calls fetchGraph; NEVER runs FA2 or any layout algorithm.
  // INVARIANT I3: effect deps are the version scalar; no per-tick re-render when unchanged.
  // RT-3: throttled to at most once per GRAPH_REFETCH_MIN_MS to prevent jitter during
  // long ingests (status poll cadence = 3s; many bumps in a row → skip intermediate ones).
  useEffect(() => {
    if (statusDataVersion === null) return;
    if (statusDataVersion === lastFetchedGraphVersionRef.current) return;
    // RT-3: enforce minimum interval between version-driven re-fetches (not initial mount).
    const now = Date.now();
    if (now - lastGraphRefetchTimeRef.current < GRAPH_REFETCH_MIN_MS) return;
    lastGraphRefetchTimeRef.current = now;
    // Version has advanced past the throttle window — refetch precomputed coords (AC-WS-A-2).
    const ctrl = new AbortController();
    setIsGraphRefetching(true); // UX-1: show "updating…" pill while in-flight
    fetchGraph(vaultId, ctrl.signal)
      .then(({ data, cacheStatus }) => {
        setGraph(
          data.nodes,
          data.edges,
          data.data_version,
          cacheStatus,
          data.communities ?? [],
          data.total_nodes ?? null,
          data.total_edges ?? null,
        );
        lastFetchedGraphVersionRef.current = data.data_version;
      })
      .catch((err: unknown) => {
        // Transient errors (network hiccup) — don't surface to the user, just log.
        if (err instanceof Error && err.name !== "AbortError") {
          console.warn("[WS-A] graph freshness re-fetch failed:", err.message);
        }
      })
      .finally(() => {
        setIsGraphRefetching(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusDataVersion]); // vaultId, setGraph intentionally omitted: mount effect owns initial fetch

  // ── GR3: sync filterNodeTypes ref and refresh sigma on filter change ─────
  // The ref lets the existing sigma reducers always see the latest filter value
  // without tearing down and rebuilding sigma on every toggle (I3: no heavy
  // work per frame; I2: no coords touched, only sigma's hidden flag is changed).
  useEffect(() => {
    filterNodeTypesRef.current = filterNodeTypes;
    // Trigger a visual refresh so sigma re-evaluates nodeReducer/edgeReducer
    // with the updated filter. skipIndexation: layout is not touched (I2).
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [filterNodeTypes]);

  // ── GI-2: sync filter refs and trigger visual refresh when visibility filters change ──
  // I2-safe: only sets hidden flags in reducers; never touches node coordinates.
  // I3-safe: updates refs (not state) so no re-render is triggered; sigma.refresh once.
  useEffect(() => {
    hideMetaTypesRef.current = hideMetaTypes;
    hideIsolatedRef.current = hideIsolated;
    minLinksRef.current = minLinks;
    maxLinksRef.current = maxLinks;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [hideMetaTypes, hideIsolated, minLinks, maxLinks]);

  // ── GI-2: node size scale — visual multiplier applied in nodeReducer via ref ───────
  useEffect(() => {
    nodeSizeScaleRef.current = nodeSizeScale;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [nodeSizeScale]);

  // ── GI-2: spacing scale — translate sigma node positions around the centroid ────────
  // Uses original `nodes` from store as source of truth for positions (I2: precomputed
  // by server; pure arithmetic scale around centroid — no FA2, no force iteration).
  // skipIndexation:false so sigma re-indexes the rescaled positions into camera space.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || nodes.length === 0) return;
    const sigmaGraph = sigma.getGraph();
    if (sigmaGraph.order === 0) return;

    // Build O(n) position lookup from server-side positions (never mutated by client)
    let sumX = 0;
    let sumY = 0;
    const origPos = new Map<string, { x: number; y: number }>();
    for (const n of nodes) {
      origPos.set(n.id, { x: n.x, y: n.y });
      sumX += n.x;
      sumY += n.y;
    }
    const cx = sumX / nodes.length;
    const cy = sumY / nodes.length;

    // Scale each node position around the centroid (pure arithmetic — I2)
    sigmaGraph.forEachNode((nodeKey) => {
      const orig = origPos.get(nodeKey);
      if (!orig) return;
      sigmaGraph.setNodeAttribute(nodeKey, "x", cx + (orig.x - cx) * spacingScale);
      sigmaGraph.setNodeAttribute(nodeKey, "y", cy + (orig.y - cy) * spacingScale);
    });

    sigma.refresh({ skipIndexation: false });
  }, [spacingScale, nodes]);

  // ── Watch resolved theme changes, re-read sigma render properties (ADR-0048 §T1) ──
  // Observes data-theme on <html>; on change, reads the new CSS vars and updates
  // sigmaThemeColors, which is in the sigma-mount effect deps → sigma rebuilds with
  // the new colors. This is a render-property update ONLY — no layout/coords touched (I2).

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setSigmaThemeColors(readSigmaThemeColors());
      setGraphTheme(
        document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light",
      );
    });
    observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  // ── FE-RT-1: build a sigma-ready graphology graph from CURRENT nodes/edges ──
  // Shared by both the mount/rebuild effect and the data-diff effect below so the
  // node/edge attribute derivation (colors, hub labels, GL1 hidden flags, …) is
  // computed identically whichever path constructs it. I2: buildGraphologyGraph
  // only copies server-provided x/y — no layout call, ever.
  const buildSigmaGraph = useCallback(
    (srcNodes: GraphNode[], srcEdges: GraphEdge[], mode: ColorMode) => {
      const isDarkTheme = document.documentElement.getAttribute("data-theme") === "dark";
      const rawGraph = buildGraphologyGraph(srcNodes, srcEdges, isDarkTheme ? "dark" : "light");

      // Build a plain Attributes graphology graph for sigma.
      // Copy all node/edge attributes (including server x/y) into the sigma graph.
      const SigmaGraphCtor = rawGraph.constructor as new (opts: {
        multi: boolean;
        type: string;
      }) => import("graphology").default;
      const sigmaGraph = new SigmaGraphCtor({ multi: false, type: "undirected" });

      rawGraph.forEachNode((nodeKey, rawAttrs) => {
        // Cast to Attributes (Record<string,unknown>) so dynamic key access is type-safe.
        const attrs = rawAttrs as Attributes;
        const nodeType = attrs["type"] as string | null | undefined;
        // Community id — -1 when unassigned or from older servers (non-breaking, set in graphTransform)
        const nodeCommunity = (attrs["community"] as number | undefined) ?? -1;
        // Domain name — null when untagged or from older servers (non-breaking).
        // I2: value comes from the server (GraphNode.domain); never computed client-side.
        const nodeDomain = (attrs["domain"] as string | null | undefined) ?? null;
        // Color is determined by active color-mode (I2: all values come from server).
        // "community" mode colors by Louvain community id — one distinct color per cluster
        // from COMMUNITY_PALETTE (cycles for >12 communities; -1 = unassigned → gray).
        // "type" mode colors by page type (concept, entity, source, …).
        const nodeColor =
          mode === "community"
            ? colorForCommunity(nodeCommunity, isDarkTheme ? "dark" : "light")
            : colorForType(nodeType ?? null);
        sigmaGraph.addNode(nodeKey, {
          x: attrs["x"] as number,
          y: attrs["y"] as number,
          label: attrs["label"] as string,
          // GL2: pre-truncated hub label; nodeReducer swaps this in for hub nodes at rest
          hubLabel: (attrs["hubLabel"] as string | undefined) ?? (attrs["label"] as string),
          size: attrs["size"] as number,
          color: nodeColor,
          // Store degree for reducers
          degree: attrs["degree"] as number,
          // Store type for reducers
          nodeType: nodeType ?? null,
          // Store community for reducers / tooltip
          nodeCommunity,
          // Store domain for reducers (I2: from server)
          nodeDomain,
          // GL2: hub flag — nodeReducer uses this to force permanent truncated label on top-K hubs
          isHub: (attrs["forceLabel"] as boolean | undefined) ?? false,
        });
      });

      rawGraph.forEachEdge((_edgeKey, attrs, source, target) => {
        if (!sigmaGraph.hasEdge(source, target)) {
          sigmaGraph.addEdge(source, target, {
            weight: attrs["weight"] as number,
            color: attrs["color"] as string,
            size: attrs["size"] as number,
            // GL1: pass through normalizedWeight so edgeReducer can check it on hover
            normalizedWeight: attrs["normalizedWeight"] as number,
            // GL1: resting hidden flag (weak edges culled at rest, revealed on hover)
            hidden: attrs["hidden"] as boolean,
          });
        }
      });

      return { rawGraph, sigmaGraph };
    },
    [],
  );

  // ── FE-RT-1: mount / rebuild sigma instance ───────────────────────────────
  // Deps are ONLY [colorMode, sigmaThemeColors, hasGraphData] — deliberately
  // NOT [nodes, edges]. Reading current data via latestNodesRef/latestEdgesRef
  // (synced during render above) means a background data refresh (graphStore's
  // periodic /graph refetch during a long ingest) never re-runs this effect, so
  // it never kills+rebuilds the WebGL instance, never re-registers every event
  // handler, and never resets the user's camera. The instance IS rebuilt when:
  //  - hasGraphData flips false→true (first data arrival — real initial mount)
  //  - colorMode changes (Type/Community toggle needs new node colors)
  //  - sigmaThemeColors changes (light/dark theme needs a new render context)
  // Ordinary node/edge UPDATES (same colorMode/theme) are handled by the
  // diff effect further below, which patches the EXISTING graphology graph
  // in place and calls sigma.refresh() — no kill(), no new Sigma().
  const hasGraphData = nodes.length > 0;

  useEffect(() => {
    if (!containerRef.current) return;
    if (!hasGraphData) return;

    const { sigmaGraph } = buildSigmaGraph(
      latestNodesRef.current,
      latestEdgesRef.current,
      colorMode,
    );

    // Destroy previous sigma instance before creating a new one
    if (sigmaRef.current) {
      sigmaRef.current.kill();
      sigmaRef.current = null;
    }

    // ── Hover-dim closure state ───────────────────────────────────────────
    // Stored in a plain object (NOT React state) so mutations do NOT cause re-renders.
    // Reducers read from this object; we call renderer.refresh({skipIndexation:true}) manually.
    const hoverState: HoverState = {
      hoveredNode: null,
      hoveredNeighbors: null,
      selectedNode: null,
    };

    // ── sigma v3 settings ────────────────────────────────────────────────
    // nodeReducer / edgeReducer implement Obsidian-style hover-dim.
    // They are set ONCE at construction; we mutate hoverState and call refresh().
    // Theme colors (bg, labelColor, halo) are read from resolved CSS vars (ADR-0048 §T1).
    // This is render-property only — no layout/coords touched (I2).
    const sigmaSettings: Partial<Settings<Attributes, Attributes, Attributes>> = {
      // Label rendering — uses resolved --syn-text for label color
      labelFont: "Inter, system-ui, sans-serif",
      labelSize: 13,
      labelWeight: "600",
      labelColor: { color: sigmaThemeColors.labelColor },
      // Obsidian-style labels: hidden when zoomed out (just dots), fade in as you zoom.
      // Node rendered size scales with camera zoom; a label shows only once a node's
      // on-screen size passes labelRenderedSizeThreshold. With the small node range
      // (2.5–11px) this threshold keeps the fit/zoomed-out view label-free, and labels
      // appear progressively (hubs first) as the user zooms in. labelDensity raised so
      // more labels reveal when eligible.
      // GL2 (B3-LOOK): threshold was lowered from 13→8 for more labels on zoom-in.
      // Declutter pass 2026-07: raised back from 8→11 so fewer non-hub labels crowd
      // the fit-view. Hub nodes still appear at rest via forceLabel regardless.
      labelDensity: 0.7,
      labelGridCellSize: 70,
      labelRenderedSizeThreshold: 11,

      // Parity with llm_wiki 0.6.0: suppress labels/edges while the camera moves so
      // panning/zooming stays legible and light on large graphs (I3). Static hover is
      // unaffected — only the hovered node forces a label (see nodeReducer).
      hideLabelsOnMove: true,
      hideEdgesOnMove: true,

      // Custom halo drawers — built with resolved theme colors (ADR-0048 §T1)
      defaultDrawNodeLabel: makeDrawHaloNodeLabel(sigmaThemeColors),
      defaultDrawNodeHover: makeDrawHaloNodeHover(sigmaThemeColors),

      // Edge events required for edgeReducer hover detection
      enableEdgeEvents: true,

      // The container can legitimately measure 0px for one frame while the
      // flex layout settles (theme-driven re-instantiation, section switch).
      // Without this sigma THROWS at construction ("Container has no width")
      // and — before SectionErrorBoundary existed — unmounted the whole app.
      // The existing ResizeObserver picks up the real size immediately after.
      allowInvalidContainer: true,

      // zIndex enables per-node z ordering in reducers
      zIndex: true,

      // Camera bounds
      minCameraRatio: 0.1,
      maxCameraRatio: 4,

      // ── nodeReducer: GR3 filter + Obsidian hover-dim + GL2 hub labels ───
      nodeReducer(node: string, data: Attributes): Partial<NodeDisplayData> {
        const res: Partial<NodeDisplayData> & Attributes = { ...data };

        // GR3: node-type filter — hide nodes whose type is not in the active set.
        // I2-safe: only sets hidden:true, never touches x/y or re-layout.
        // I3-safe: reads from filterNodeTypesRef (updated via useEffect above)
        //   so no re-render per frame — the ref is mutated and sigma.refresh() called once.
        const activeFilter = filterNodeTypesRef.current;
        if (activeFilter.size > 0) {
          const nodeType = (data["nodeType"] as string | null | undefined) ?? null;
          const typeKey = nodeType ?? "other";
          if (!activeFilter.has(typeKey)) {
            res["hidden"] = true;
            return res as Partial<NodeDisplayData>;
          }
        }

        // GI-2: hide meta-type nodes (index, log, overview) — I2-safe (visibility only).
        if (hideMetaTypesRef.current) {
          const nodeType = (data["nodeType"] as string | null | undefined) ?? "";
          if (META_NODE_TYPES.has(nodeType)) {
            res["hidden"] = true;
            return res as Partial<NodeDisplayData>;
          }
        }

        // GI-2: hide isolated nodes (degree 0) — I2-safe.
        if (hideIsolatedRef.current && ((data["degree"] as number | undefined) ?? 0) === 0) {
          res["hidden"] = true;
          return res as Partial<NodeDisplayData>;
        }

        // GI-2: min/max links filter — I2-safe.
        {
          const nodeDegree = (data["degree"] as number | undefined) ?? 0;
          const minL = minLinksRef.current;
          const maxL = maxLinksRef.current;
          if (minL !== null && nodeDegree < minL) {
            res["hidden"] = true;
            return res as Partial<NodeDisplayData>;
          }
          if (maxL !== null && nodeDegree > maxL) {
            res["hidden"] = true;
            return res as Partial<NodeDisplayData>;
          }
        }

        // GI-2: node size scale (visual multiplier — I2: only changes render size, no coords).
        const sizeScale = nodeSizeScaleRef.current;
        if (sizeScale !== 1.0) {
          res["size"] = ((data["size"] as number | undefined) ?? 8) * sizeScale;
        }

        // GL2: hub nodes always show their truncated label at rest (top-K by degree).
        // hubLabel is pre-computed at build time (truncateHubLabel) — no per-frame work (I3).
        if ((data["isHub"] as boolean | undefined) === true) {
          res["forceLabel"] = true;
          // Replace the full label with the truncated hub label so sigma draws
          // the short version at rest. Full title stays in the tooltip (nodeId → fetchPageDetail).
          res["label"] = (data["hubLabel"] as string | undefined) ?? (data["label"] as string);
        }

        if (hoverState.hoveredNeighbors !== null) {
          const isHovered = node === hoverState.hoveredNode;
          const isNeighbor = hoverState.hoveredNeighbors.has(node);

          if (isHovered) {
            // Hovered node: highlighted, forced label (full title restored), top z, slight size bump
            res["highlighted"] = true;
            res["forceLabel"] = true;
            // Restore the full label on the hovered node so the tooltip confirms the exact title
            res["label"] = data["label"] as string;
            res["zIndex"] = 2;
            res["size"] = ((data["size"] as number | undefined) ?? 8) * 1.15;
          } else if (isNeighbor) {
            // Neighbor: raised z + deepened color so the cluster pops — but do NOT
            // force its label. Matches llm_wiki 0.6.0 (only the hovered node forces a
            // label; neighbours are highlighted, not labelled), so hovering a hub no
            // longer floods the view with every neighbour's title. A neighbour that is
            // itself a hub still shows its truncated label via the isHub branch above.
            res["zIndex"] = 1;
            // Mix toward black to deepen while preserving hue (light-theme pop)
            res["color"] = deepenColor((data["color"] as string | undefined) ?? DEFAULT_NODE_COLOR);
          } else {
            // All other nodes: dim (washed-out, hide label) — use --syn-border resolved value
            res["label"] = "";
            res["color"] =
              sigmaThemeColors.labelColor === "#1f2328"
                ? "#c7ccd4" // light mode: close to --syn-border
                : "#30363d"; // dark mode: close to --syn-border dark
            res["zIndex"] = 0;
          }
        } else if (selectedNodeIdRef.current !== null && node === selectedNodeIdRef.current) {
          // Persistent selection ring at REST (no hover active). Reuses sigma's
          // hover renderer (makeDrawHaloNodeHover) via highlighted:true, so the
          // clicked node keeps a ring + full label until another node is selected.
          // Hover always wins: this branch only runs when hoveredNeighbors === null.
          res["highlighted"] = true;
          res["forceLabel"] = true;
          res["label"] = data["label"] as string;
          res["zIndex"] = 2;
        }

        return res as Partial<NodeDisplayData>;
      },

      // ── edgeReducer: GR3 filter + GI-2 filters + GL1 resting cull + Obsidian hover reveal ─
      // At rest: edges with hidden:true (weak edges per GL1) are not rendered.
      // GR3: edges whose source or target is filtered out are also hidden.
      // GI-2: additional visibility checks for meta-type, isolated, min/max links.
      // On hover: incident edges are ALWAYS revealed regardless of GL1 threshold
      //   so the user can explore the full neighborhood even on large graphs.
      // Non-incident edges during hover: hidden (Obsidian dim).
      // I2-safe: visibility only, no coord mutation.
      edgeReducer(edge: string, data: Attributes) {
        const res: Attributes = { ...data };

        // Extract endpoints once for all filter checks (avoids repeated extremities() calls).
        const [src, tgt] = sigmaGraph.extremities(edge);

        // GR3: hide edge if either endpoint type is filtered out.
        const activeFilter = filterNodeTypesRef.current;
        if (activeFilter.size > 0) {
          const srcType =
            (sigmaGraph.getNodeAttribute(src, "nodeType") as string | null | undefined) ?? null;
          const tgtType =
            (sigmaGraph.getNodeAttribute(tgt, "nodeType") as string | null | undefined) ?? null;
          if (!activeFilter.has(srcType ?? "other") || !activeFilter.has(tgtType ?? "other")) {
            res["hidden"] = true;
            return res;
          }
        }

        // GI-2: hide edge if either endpoint is a meta-type (index/log/overview).
        if (hideMetaTypesRef.current) {
          const srcType =
            (sigmaGraph.getNodeAttribute(src, "nodeType") as string | null | undefined) ?? "";
          const tgtType =
            (sigmaGraph.getNodeAttribute(tgt, "nodeType") as string | null | undefined) ?? "";
          if (META_NODE_TYPES.has(srcType) || META_NODE_TYPES.has(tgtType)) {
            res["hidden"] = true;
            return res;
          }
        }

        // GI-2: hide edge if either endpoint is isolated (degree 0) and hideIsolated is on.
        if (hideIsolatedRef.current) {
          const srcDeg = (sigmaGraph.getNodeAttribute(src, "degree") as number | undefined) ?? 0;
          const tgtDeg = (sigmaGraph.getNodeAttribute(tgt, "degree") as number | undefined) ?? 0;
          if (srcDeg === 0 || tgtDeg === 0) {
            res["hidden"] = true;
            return res;
          }
        }

        // GI-2: hide edge if either endpoint doesn't pass min/max links filter.
        {
          const minL = minLinksRef.current;
          const maxL = maxLinksRef.current;
          if (minL !== null || maxL !== null) {
            const srcDeg = (sigmaGraph.getNodeAttribute(src, "degree") as number | undefined) ?? 0;
            const tgtDeg = (sigmaGraph.getNodeAttribute(tgt, "degree") as number | undefined) ?? 0;
            if (minL !== null && (srcDeg < minL || tgtDeg < minL)) {
              res["hidden"] = true;
              return res;
            }
            if (maxL !== null && (srcDeg > maxL || tgtDeg > maxL)) {
              res["hidden"] = true;
              return res;
            }
          }
        }

        if (hoverState.hoveredNode !== null) {
          const srcRelevant =
            src === hoverState.hoveredNode || (hoverState.hoveredNeighbors?.has(src) ?? false);
          const tgtRelevant =
            tgt === hoverState.hoveredNode || (hoverState.hoveredNeighbors?.has(tgt) ?? false);

          if (!srcRelevant || !tgtRelevant) {
            // Non-incident: hide entirely (Obsidian dim; overrides GL1 reveal)
            res["hidden"] = true;
          } else {
            // GL1 REVEAL: incident edges always shown on hover, even if below threshold.
            res["hidden"] = false;
            // Emphasis color follows the theme — darker on a light canvas,
            // LIGHTER on a dark canvas (a dark emphasis would vanish).
            const dark = document.documentElement.getAttribute("data-theme") === "dark";
            // Active-edge highlight — cyan on dark, slate-800 on light (llm_wiki 0.6.0 parity)
            res["color"] = dark ? "#38bdf8" : "#1e293b";
            res["size"] = ((data["size"] as number | undefined) ?? 1) * 2;
          }
        }
        // At rest (no hover): hidden flag is whatever was set in buildGraphologyGraph (GL1).
        // sigma respects hidden:true by skipping the edge in WebGL draw — no data mutation.

        return res;
      },
    };

    // Instantiate sigma — creates ONE WebGL <canvas> inside containerRef (I4)
    // I2: sigma renders FIXED precomputed positions. No layout algorithm. No rAF physics.
    const sigma = new Sigma(sigmaGraph, containerRef.current, sigmaSettings);
    sigmaRef.current = sigma;

    // ── Drag state (closed-over plain object — no React re-render during drag) ─
    const dragState: DragState = {
      draggedNode: null,
      hasMoved: false,
      downX: 0,
      downY: 0,
      suppressStageClick: false,
    };

    // ── Event handlers ────────────────────────────────────────────────────
    // Mutate hoverState / dragState in-place, then call refresh.
    // Do NOT call setSetting inside handlers (spec requirement).

    sigma.on("enterNode", ({ node }) => {
      // Suppress hover-dim while a drag is in progress
      if (dragState.draggedNode !== null) return;
      hoverState.hoveredNode = node;
      hoverState.hoveredNeighbors = new Set(sigmaGraph.neighbors(node));
      sigma.refresh({ skipIndexation: true });
    });

    sigma.on("leaveNode", () => {
      if (dragState.draggedNode !== null) return;
      hoverState.hoveredNode = null;
      hoverState.hoveredNeighbors = null;
      sigma.refresh({ skipIndexation: true });
    });

    // ── Drag: downNode ────────────────────────────────────────────────────
    // I2: we record WHICH node is being dragged; no layout computed here.
    sigma.on("downNode", ({ node, event }) => {
      dragState.draggedNode = node;
      dragState.hasMoved = false;
      dragState.downX = event.x;
      dragState.downY = event.y;
      // Highlight the node visually during drag
      sigmaGraph.setNodeAttribute(node, "highlighted", true);
      // NB: do NOT disable the mouse captor here. The captor is what dispatches sigma's
      // upNode / upStage / clickNode events; disabling it suppressed them, so endDrag never ran
      // and click-to-open was silently dead (the node highlighted but the page never opened).
      // Stage-pan during a node drag is prevented instead by event.preventSigmaDefault() in the
      // moveBody handler below (the official sigma v3 node-drag pattern).
      sigma.refresh({ skipIndexation: true });
    });

    // ── Drag: moveBody ────────────────────────────────────────────────────
    // I2: ONLY the dragged node's x/y are updated — direct manipulation, not layout.
    // No other node is touched. No physics loop. No rAF.
    sigma.on("moveBody", ({ event }) => {
      if (dragState.draggedNode === null) return;

      // Prevent sigma's default stage-pan behaviour
      event.preventSigmaDefault();

      // Convert screen coords → graph-space coords (sigma v3 API)
      const pos = sigma.viewportToGraph({ x: event.x, y: event.y });

      // Ignore sub-threshold jitter so a tap doesn't register as a drag (mobile pin bug).
      const dx = event.x - dragState.downX;
      const dy = event.y - dragState.downY;
      if (!dragState.hasMoved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;

      // Write ONLY the dragged node's position — I2: no other node touched
      sigmaGraph.setNodeAttribute(dragState.draggedNode, "x", pos.x);
      sigmaGraph.setNodeAttribute(dragState.draggedNode, "y", pos.y);

      dragState.hasMoved = true;
      // scheduleRefresh redraws without full re-indexation — fast path
      sigma.scheduleRefresh();
    });

    // ── Drag: upNode / upStage — end of drag ─────────────────────────────
    const endDrag = () => {
      if (dragState.draggedNode === null) return;

      const node = dragState.draggedNode;
      const moved = dragState.hasMoved;

      // Clear highlighted attribute
      sigmaGraph.setNodeAttribute(node, "highlighted", false);

      if (moved) {
        // Persist the new position to the backend (fire-and-forget).
        // I2: we are writing a USER-CHOSEN position back to the server, not
        // computing layout. The server stores it and returns it on the next
        // GET /graph so FA2 can re-incorporate it as a fixed seed.
        const finalX = sigmaGraph.getNodeAttribute(node, "x") as number;
        const finalY = sigmaGraph.getNodeAttribute(node, "y") as number;

        patchNodePosition(node, finalX, finalY).catch((err: unknown) => {
          // Non-critical — position is already updated locally in sigma.
          // Log quietly; do not surface to the user.
          if (err instanceof Error) {
            console.warn("[GraphViewer] patchNodePosition failed:", err.message);
          }
        });
      } else {
        // No pointer movement between downNode and upNode → a genuine CLICK, not a drag.
        // Open the clicked node's wiki page here (Obsidian-style). This MUST live in endDrag,
        // not in sigma's `clickNode`: downNode disables the mouse captor so the stage doesn't pan
        // while dragging, and a disabled captor suppresses `clickNode` emission — so relying on it
        // left click-to-open silently broken (the node highlighted but the page never opened).
        // endDrag always fires on upNode/upStage, so this is the reliable click seam.
        // Sigma dispatches a clickStage right after this (it treats node clicks as stage clicks);
        // flag it so that handler does NOT immediately wipe the selection we just set.
        dragState.suppressStageClick = true;
        hoverState.selectedNode = node;
        setSelectedNodeId(node);
        selectPage(node, "graph");
        setActiveSection("pages");
      }

      dragState.draggedNode = null;
      dragState.hasMoved = false;
      sigma.refresh({ skipIndexation: true });
    };

    sigma.on("upNode", endDrag);
    sigma.on("upStage", endDrag);

    // ── Click (no drag) — FALLBACK ────────────────────────────────────────
    // In this sigma setup clickNode does NOT fire for node clicks (sigma classifies them as stage
    // clicks, so clickStage fires instead) — the real click-to-open seam is endDrag's !moved branch
    // above. This handler stays as a harmless, idempotent fallback for any environment where
    // clickNode DOES fire: same action, and it flags suppressStageClick so the follow-up clickStage
    // does not wipe the just-opened selection.
    sigma.on("clickNode", ({ node }) => {
      dragState.suppressStageClick = true;
      hoverState.selectedNode = node;
      setSelectedNodeId(node);
      selectPage(node, "graph");
      setActiveSection("pages");
    });

    sigma.on("clickStage", () => {
      // Consume the flag set by a node click in endDrag: sigma dispatches clickStage right after a
      // node click, which would otherwise wipe the just-opened page. One suppressed clear per node
      // click; genuine empty-stage clicks (no preceding node endDrag) still deselect normally.
      if (dragState.suppressStageClick) {
        dragState.suppressStageClick = false;
        return;
      }
      hoverState.selectedNode = null;
      setSelectedNodeId(null);
      setTooltip(null);
      setEdgeTooltip(null);
    });

    // ── R9-5: Edge click → show breakdown tooltip ─────────────────────────
    // sigma fires clickEdge when enableEdgeEvents:true (already set above).
    sigma.on("clickEdge", ({ edge, event }) => {
      const [src, tgt] = sigmaGraph.extremities(edge);
      setEdgeTooltip({ srcId: src, tgtId: tgt, position: { x: event.x, y: event.y } });
      // Close node tooltip if open
      setTooltip(null);
    });

    // ── Camera fit ────────────────────────────────────────────────────────
    // sigma/utils in v3.0.3 does not export fitViewportToNodes.
    // animatedReset() resets camera to the normalized graph center — equivalent fit.
    // FE-RT-1: ONLY on the true first mount of this component instance — never on a
    // colorMode/theme-driven rebuild, and never on a background data refresh (that
    // path doesn't even reach this effect — see the diff effect below). Explicit
    // re-centering after that is exclusively the user's "Fit" button (handleFit).
    if (!hasMountedSigmaRef.current) {
      hasMountedSigmaRef.current = true;
      sigma.getCamera().animatedReset({
        duration: reducedMotion ? 0 : 500,
      });
    }

    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
    // Rebuild sigma ONLY on color-mode or theme change, or when data first arrives
    // (hasGraphData flips false→true). Ordinary node/edge content updates are handled
    // by the diff effect below WITHOUT tearing down this sigma instance (FE-RT-1).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasGraphData, colorMode, sigmaThemeColors, buildSigmaGraph]);

  // ── FE-RT-1: diff incoming nodes/edges into the EXISTING sigma graph ──────
  // Runs whenever the graphStore's nodes/edges arrays change reference (every
  // GET /graph success — including the throttled background refetch during a
  // long ingest, RT-3). Instead of killing sigma and building a new WebGL
  // context (the old behavior — see 1.9.3 W2 finding FE-RT-1), this patches the
  // ALREADY-MOUNTED graphology graph in place:
  //   - nodes/edges present in the new payload but missing from the live graph → added
  //   - nodes/edges present in both → attributes merged (x/y/color/label/…) in place
  //   - nodes/edges no longer present in the new payload → removed
  // Then a single sigma.refresh() repaints — no new Sigma instance, no camera reset,
  // no event-handler re-registration, no hover/drag state loss.
  // INVARIANT I2: buildSigmaGraph (shared helper above) only ever copies x/y that the
  // SERVER already computed (FA2 offline) — this effect never runs a layout algorithm,
  // it only reconciles which nodes/edges are attached to the sigma graph and what their
  // server-provided attributes are.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return; // Not mounted yet — the mount effect above owns the first build.
    // NOTE: do NOT early-return for nodes.length === 0 here. When a cascade-delete
    // empties the vault and /graph returns zero nodes, we must run the remove loops
    // below so all stale sigma nodes/edges are dropped and the canvas clears.

    // Diff against sigmaGraph (the transformed copy — color/hubLabel/nodeType/
    // nodeCommunity/nodeDomain/isHub already computed), NOT rawGraph (plain
    // type/community/domain, no color): liveGraph's node/edge attrs are already
    // in the sigmaGraph shape (built by the mount effect above), so merging
    // rawGraph's raw attrs here would strip color/hubLabel/isHub on the very
    // next diff after mount and break sigma's node/edge reducers.
    const { sigmaGraph: nextGraph } = buildSigmaGraph(nodes, edges, colorMode);
    const liveGraph = sigma.getGraph();

    // ── Nodes: add / update ──────────────────────────────────────────────
    nextGraph.forEachNode((nodeKey, attrs) => {
      if (liveGraph.hasNode(nodeKey)) {
        liveGraph.mergeNodeAttributes(nodeKey, attrs);
      } else {
        liveGraph.addNode(nodeKey, attrs);
      }
    });
    // ── Nodes: remove any no longer present in the latest payload ────────
    liveGraph.forEachNode((nodeKey) => {
      if (!nextGraph.hasNode(nodeKey)) liveGraph.dropNode(nodeKey);
    });

    // ── Edges: add / update ──────────────────────────────────────────────
    nextGraph.forEachEdge((_edgeKey, attrs, source, target) => {
      if (!liveGraph.hasNode(source) || !liveGraph.hasNode(target)) return;
      if (liveGraph.hasEdge(source, target)) {
        liveGraph.mergeEdgeAttributes(source, target, attrs);
      } else {
        liveGraph.addEdge(source, target, attrs);
      }
    });
    // ── Edges: remove any no longer present in the latest payload ────────
    liveGraph.forEachEdge((edgeKeyLive, _attrs, source, target) => {
      if (!nextGraph.hasEdge(source, target)) liveGraph.dropEdge(edgeKeyLive);
    });

    // Positions/colors may have shifted (server FA2 recompute) — full refresh,
    // but NOT skipIndexation:true-only since coordinates may have moved and
    // sigma's spatial index needs to catch up. Still zero WebGL context churn.
    sigma.refresh({ skipIndexation: false });
    // colorMode intentionally omitted: when it changes, hasGraphData/colorMode
    // deps on the mount effect above already trigger a full rebuild that
    // supersedes this diff for that render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, buildSigmaGraph]);

  // ── Sync selectedNodeId from store → announcement (aria-live) ────────────

  useEffect(() => {
    // Keep the reducer's ref in sync so the persistent selection ring follows the store.
    selectedNodeIdRef.current = selectedNodeId;
    if (!selectedNodeId) {
      setAnnouncement("");
      // Clear the ring: re-run reducers now that nothing is selected.
      sigmaRef.current?.refresh({ skipIndexation: true });
      return;
    }
    if (!sigmaRef.current) return;

    const graph = sigmaRef.current.getGraph();
    const attrs = graph.getNodeAttributes(selectedNodeId) as Attributes & {
      label?: string;
      nodeType?: string | null;
      degree?: number;
    };

    const title = attrs["label"] ?? selectedNodeId;
    const type = attrs["nodeType"] ?? "unknown type";
    const neighborCount = graph.neighbors(selectedNodeId).length;

    // F8: use i18n for screen-reader announcement (was hardcoded English)
    setAnnouncement(t("graph.nodeSelected", { title, type, count: neighborCount }));

    // Trigger a refresh so sigma re-applies reducers with updated selectedNode
    sigmaRef.current.refresh({ skipIndexation: true });
  }, [selectedNodeId, t]);

  const handleTooltipClose = useCallback(() => {
    setSelectedNodeId(null);
    setTooltip(null);
    setAnnouncement("");
  }, [setSelectedNodeId]);

  // ── Camera controls — zoom in / out / fit ─────────────────────────────────
  // These are simple camera calls; I2 is preserved (no layout algorithm invoked).
  // reducedMotion is read from the module-level const declared above the component.

  const handleZoomIn = useCallback(() => {
    sigmaRef.current?.getCamera().animatedZoom({ duration: reducedMotion ? 0 : 200 });
  }, []);

  const handleZoomOut = useCallback(() => {
    sigmaRef.current?.getCamera().animatedUnzoom({ duration: reducedMotion ? 0 : 200 });
  }, []);

  const handleFit = useCallback(() => {
    sigmaRef.current?.getCamera().animatedReset({ duration: reducedMotion ? 0 : 300 });
  }, []);

  // ── GR2: In-graph search — find node by title substring, select + camera center ──
  // Client-side only; nodes are already in the store (I3: computed on change, not per frame).
  const handleSearch = useCallback(
    (query: string) => {
      if (!query.trim() || !sigmaRef.current) return;
      const q = query.toLowerCase();
      // Find first matching node in the sigma graph
      const sigma = sigmaRef.current;
      const graph = sigma.getGraph();
      let matchKey: string | null = null;
      graph.forEachNode((key, attrs) => {
        if (matchKey !== null) return;
        const label = ((attrs["label"] as string | undefined) ?? "").toLowerCase();
        if (label.includes(q)) matchKey = key;
      });
      if (matchKey === null) return;
      // Select the node (triggers aria announcement + tree sync)
      selectPage(matchKey, "graph");
      setSelectedNodeId(matchKey);
      // Animate camera to center on the found node's precomputed coords (I2-safe: read-only)
      const attrs = graph.getNodeAttributes(matchKey);
      const x = attrs["x"] as number;
      const y = attrs["y"] as number;
      sigma.getCamera().animate({ x, y, ratio: 0.3 }, { duration: reducedMotion ? 0 : 400 });
    },
    [selectPage, setSelectedNodeId],
  );

  // ── GR4: Reset — clear ALL filters (type + GI-2) + fit camera ───────────────
  const handleReset = useCallback(() => {
    clearAllGraphFilters(); // clears filterNodeTypes + hideMetaTypes/hideIsolated/minLinks/maxLinks/nodeSizeScale/spacingScale
    handleFit();
  }, [clearAllGraphFilters, handleFit]);

  // ── GR7: Fullscreen — Fullscreen API on the graph root container ───────────
  const handleFullscreen = useCallback(() => {
    const el = graphRootRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen().catch((err: unknown) => {
        if (err instanceof Error) console.warn("[GraphViewer] fullscreen failed:", err.message);
      });
    } else {
      document.exitFullscreen().catch(() => {
        /* ignore */
      });
    }
  }, []);

  // ── Insights panel toggle — shared via graphStore (sibling GraphInsightsPanel reads it)
  const showInsightsPanel = useAppStore(selectShowInsightsPanel);
  const setShowInsightsPanel = useAppStore(selectSetShowInsightsPanel);

  const handleToggleInsights = useCallback(() => {
    setShowInsightsPanel(!showInsightsPanel);
  }, [showInsightsPanel, setShowInsightsPanel]);

  // ── insightCount — computed from current nodes/edges/communities (I3: memoized, not per-frame)
  // I3 / AC-F4-6: computeGraphInsights scans the whole graph (surprising connections +
  // knowledge gaps) — a >50ms main-thread task on large graphs. It MUST NOT run on the
  // load/render critical path (it would trip the long-task budget). Compute the toolbar
  // badge count lazily — only once the user opens the Insights panel — and even then at
  // idle time. Before first open the badge shows no number; the initial graph render
  // stays long-task-free.
  const [insightCount, setInsightCount] = useState(0);
  useEffect(() => {
    if (!showInsightsPanel || nodes.length === 0) {
      return;
    }
    let cancelled = false;
    const compute = () => {
      if (!cancelled) setInsightCount(computeGraphInsights(nodes, edges, communities).total);
    };
    const w = window as typeof window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (h: number) => void;
    };
    let handle: number;
    if (typeof w.requestIdleCallback === "function") {
      handle = w.requestIdleCallback(compute, { timeout: 1000 });
    } else {
      handle = window.setTimeout(compute, 300);
    }
    return () => {
      cancelled = true;
      if (typeof w.cancelIdleCallback === "function") w.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
    };
  }, [showInsightsPanel, nodes, edges, communities]);

  return (
    // I4: this container holds sigma's single <canvas> + a handful of overlay divs.
    // Total DOM nodes inside: <div#sigma-container> + <canvas> + aria-live + overlays = ~10 → well under 20.
    <div
      id="graph-root"
      ref={graphRootRef}
      role="application"
      aria-label={t("graph.title")}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        background: "var(--syn-bg)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* UXA-28: @keyframes syn-spin is declared globally in theme.css — no inline <style> needed */}

      {/* GR1–GR5, GR7, GI-2: Graph header with stats, search, filter, reset, fullscreen, color-mode, insights, refresh */}
      <GraphHeader
        nodes={nodes}
        edges={edges}
        totalNodes={totalNodes}
        filterNodeTypes={filterNodeTypes}
        toggleFilterNodeType={toggleFilterNodeType}
        clearFilterNodeTypes={clearFilterNodeTypes}
        onSearch={handleSearch}
        onReset={handleReset}
        onFullscreen={handleFullscreen}
        graphContainerRef={graphRootRef}
        colorMode={colorMode}
        onSetColorMode={setColorMode}
        showInsights={showInsightsPanel}
        onToggleInsights={handleToggleInsights}
        insightCount={insightCount}
        onRefresh={handleRegenerate}
        regenerating={regenerating}
        regenMsg={regenMsg}
        hideMetaTypes={hideMetaTypes}
        onSetHideMetaTypes={setHideMetaTypes}
        hideIsolated={hideIsolated}
        onSetHideIsolated={setHideIsolated}
        minLinks={minLinks}
        onSetMinLinks={setMinLinks}
        maxLinks={maxLinks}
        onSetMaxLinks={setMaxLinks}
        nodeSizeScale={nodeSizeScale}
        onSetNodeSizeScale={setNodeSizeScale}
        spacingScale={spacingScale}
        onSetSpacingScale={setSpacingScale}
        onClearAllFilters={clearAllGraphFilters}
      />
      {/* Canvas area wrapper: flex:1, position:relative so all absolute overlays
          (regenerate, zoom, legend, tooltips) are positioned relative to the canvas
          area — NOT the full graph-root that now includes the header above it. */}
      <div style={{ flex: 1, minHeight: 0, position: "relative", overflow: "hidden" }}>
        {/* sigma mounts ONE WebGL <canvas> here — I4.
          Background is set from the resolved --syn-bg token (ADR-0048 §T1).
          sigma inherits the container background for its WebGL clear color.
          Both inline style and CSS var are set so sigma and the DOM agree.
          Mobile/PWA (R10-5, AC-R10-5-3): touch-action: none is applied to
          #sigma-container via theme.css @media (max-width: 767px). This hands
          all pointer events exclusively to sigma's touch handler, enabling
          pinch-zoom without triggering the browser's native page-scroll/zoom.
          I2 is preserved: sigma's camera.animatedZoom/animatedUnzoom scale the
          precomputed coordinates — no FA2 re-invocation, no main-thread layout. */}
        <div
          id="sigma-container"
          ref={containerRef}
          style={{ width: "100%", height: "100%", background: sigmaThemeColors.bg }}
        />

        {/* Aria-live region — announces node selection for screen readers */}
        <div
          aria-live="polite"
          aria-atomic="true"
          style={{
            position: "absolute",
            width: 1,
            height: 1,
            overflow: "hidden",
            clip: "rect(0 0 0 0)",
            whiteSpace: "nowrap",
            border: 0,
            top: 0,
            left: 0,
          }}
        >
          {announcement}
        </div>

        {/* Zoom / fit control cluster — top-right of canvas area (reference layout) */}
        <div
          className="syn-card"
          style={{
            position: "absolute",
            top: 12,
            right: 12,
            padding: "4px",
            zIndex: 5,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 2,
            userSelect: "none",
          }}
          aria-label={t("graph.zoomControlsLabel")}
          data-testid="graph-zoom-controls"
        >
          <button
            type="button"
            onClick={handleZoomIn}
            data-testid="graph-zoom-in"
            aria-label={t("graph.zoomIn")}
            title={t("graph.zoomIn")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 28,
              height: 28,
              border: "1px solid var(--syn-border)",
              borderRadius: 3,
              background: "var(--syn-surface)",
              color: "var(--syn-text-muted)",
              cursor: "pointer",
              padding: 0,
            }}
          >
            <ZoomIn size={14} strokeWidth={1.8} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={handleZoomOut}
            data-testid="graph-zoom-out"
            aria-label={t("graph.zoomOut")}
            title={t("graph.zoomOut")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 28,
              height: 28,
              border: "1px solid var(--syn-border)",
              borderRadius: 3,
              background: "var(--syn-surface)",
              color: "var(--syn-text-muted)",
              cursor: "pointer",
              padding: 0,
            }}
          >
            <ZoomOut size={14} strokeWidth={1.8} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={handleFit}
            data-testid="graph-fit"
            aria-label={t("graph.fit")}
            title={t("graph.fit")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 28,
              height: 28,
              border: "1px solid var(--syn-border)",
              borderRadius: 3,
              background: "var(--syn-surface)",
              color: "var(--syn-text-muted)",
              cursor: "pointer",
              padding: 0,
            }}
          >
            <Maximize2 size={14} strokeWidth={1.8} aria-hidden="true" />
          </button>
        </div>

        {/* Status bar overlay — bottom-center so it doesn't conflict with zoom controls top-right */}
        <StatusBar isRefetching={isGraphRefetching} />

        {/* Legend overlay — CVD-safe: name + color swatch; switches on colorMode.
          "community" mode shows ONE row per Louvain community, labeled with
          communityDisplayName(c) — unique "{domain} · {subtopic}" per cluster.
          G3 (v1.3.14 fix): onCommunityClick wired only in "community" mode so clicking
          a community legend row opens CommunityPanel (GET /graph/communities/{id}). */}
        <GraphLegend
          colorMode={colorMode}
          communities={communities}
          nodes={nodes}
          theme={graphTheme}
          {...(colorMode === "community"
            ? {
                onCommunityClick: (id: number) =>
                  setCommunityPanel({ id, color: colorForCommunity(id, graphTheme) }),
              }
            : {})}
        />

        {/* Community centroid labels overlay (community mode).
          "Comunità" toggle = per-Louvain-community grouping: one label per cluster at its
          nodes' centroid. Label = communityDisplayName(c) truncated to OVERLAY_LABEL_MAX_CHARS.
          Uses CentroidOverlay (string-keyed Map) with communityCentroids (memoized, I3).
          INVARIANT I2: reads server-provided x/y only; never mutates node positions or runs layout.
          INVARIANT I3: communityCentroids memoized via useMemo; only viewport projection per frame. */}
        <CentroidOverlay
          centroids={communityCentroids}
          sigmaRef={sigmaRef}
          active={colorMode === "community"}
          testId="community-overlay"
        />

        {/* R9-5: Community drill-down panel */}
        {communityPanel !== null && (
          <CommunityPanel
            communityId={communityPanel.id}
            communityColor={communityPanel.color}
            onClose={() => setCommunityPanel(null)}
            onNavigate={(pageId) => {
              setSelectedNodeId(pageId);
              setCommunityPanel(null);
            }}
          />
        )}

        {/* Tooltip — conditional, at most 1 visible at a time */}
        {tooltip !== null && (
          <NodeTooltip
            nodeId={tooltip.nodeId}
            position={tooltip.position}
            neighborCount={tooltip.neighborCount}
            onClose={handleTooltipClose}
          />
        )}

        {/* R9-5: Edge weight breakdown tooltip */}
        {edgeTooltip !== null && (
          <EdgeBreakdownTooltip
            srcId={edgeTooltip.srcId}
            tgtId={edgeTooltip.tgtId}
            position={edgeTooltip.position}
            cache={edgeDetailCache.current}
            onCached={handleEdgeCached}
            onClose={() => setEdgeTooltip(null)}
          />
        )}

        {/* Close canvas area wrapper */}
      </div>
    </div>
  );
};

export default GraphViewer;
