/**
 * PanelGroup.tsx — react-resizable-panels@4.12.0 three-panel layout with collapse.
 *
 * CRITICAL API NOTE for v4.12.0:
 *   - String sizes ("22%") = percentages; plain numbers = pixels.
 *   - Group.defaultLayout uses plain numbers as percentages.
 *   - Use usePanelRef() + collapsible + collapse()/expand() for imperative collapse.
 *
 * Layout: left 22% | separator | center 56% | separator | right 22%
 * Collapse: chevron buttons on the outer edges of each side panel.
 *
 * Light design: var(--syn-bg-soft) side panels, var(--syn-bg) center,
 * var(--syn-border) separators and collapse button borders.
 */

import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { Group, Panel, Separator, usePanelRef } from "react-resizable-panels";
import type { Layout } from "react-resizable-panels";
import { NavTree } from "../nav/NavTree";
import { NoteView } from "../wiki/NoteView";
import { PreviewPanel } from "../preview/PreviewPanel";
import { ScenarioTemplates } from "../common/ScenarioTemplates";
import { useGraphStore, selectVaultId } from "../../store/graphStore";
import { fetchGraph } from "../../api/graphClient";

// ─── Panel IDs ────────────────────────────────────────────────────────────────

const PANEL_LEFT = "panel-left";
const PANEL_CENTER = "panel-center";
const PANEL_RIGHT = "panel-right";
const LS_KEY = "synapse-panel-layout-v2";

// ─── Default layout ───────────────────────────────────────────────────────────

const DEFAULT_LAYOUT: Layout = {
  [PANEL_LEFT]: 22,
  [PANEL_CENTER]: 56,
  [PANEL_RIGHT]: 22,
};

function loadLayout(): Layout {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Layout;
      const l = parsed[PANEL_LEFT];
      const c = parsed[PANEL_CENTER];
      const r = parsed[PANEL_RIGHT];
      if (
        typeof l === "number" && l > 0 &&
        typeof c === "number" && c > 0 &&
        typeof r === "number" && r > 0
      ) {
        return parsed;
      }
    }
  } catch {
    // ignore
  }
  return DEFAULT_LAYOUT;
}

// ─── Separator style ──────────────────────────────────────────────────────────

const SEPARATOR_STYLE: CSSProperties = {
  width: 4,
  flexShrink: 0,
  background: "var(--syn-border)",
  cursor: "col-resize",
  transition: "background 0.12s ease",
};

// ─── Collapse button ──────────────────────────────────────────────────────────

function CollapseButton({
  direction,
  collapsed,
  onClick,
}: {
  direction: "left" | "right";
  collapsed: boolean;
  onClick: () => void;
}) {
  const pointsRight = direction === "left" ? !collapsed : collapsed;
  return (
    <button
      onClick={onClick}
      data-testid={`collapse-${direction}-btn`}
      aria-label={collapsed ? "Expand panel" : "Collapse panel"}
      title={collapsed ? "Expand" : "Collapse"}
      style={{
        position: "absolute",
        top: "50%",
        transform: "translateY(-50%)",
        [direction === "left" ? "right" : "left"]: 0,
        zIndex: 10,
        width: 16,
        height: 32,
        border: "1px solid var(--syn-border)",
        borderRadius: direction === "left" ? "0 4px 4px 0" : "4px 0 0 4px",
        background: "var(--syn-bg)",
        color: "var(--syn-text-dim)",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
        fontSize: 10,
        lineHeight: 1,
        transition: "color 0.1s ease, background 0.1s ease",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-muted)";
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-surface-hover)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-dim)";
        (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-bg)";
      }}
    >
      {pointsRight ? "›" : "‹"}
    </button>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function PanelGroup() {
  const vaultId = useGraphStore(selectVaultId);
  const initialLayout = loadLayout();

  // E2E defect fix (R9-6 finding): PreviewPanel reads node metadata from
  // graphStore.nodes, which historically was populated only when GraphViewer
  // mounted (graph section). Visiting "pages" cold left the panel stuck on
  // its empty state. Lazily hydrate the store here when empty — same
  // fetchGraph→setGraph path GraphViewer uses; server-precomputed coords
  // only, no client layout (I2). Silent on failure: the tree still works.
  // IMPORTANT: read the store imperatively (getState) — subscribing to `nodes`
  // here would re-render PanelGroup when the graph loads, and the per-render
  // loadLayout() would feed react-resizable-panels an unstable initialLayout
  // (observed: nav-tree scroll container collapsing to 0 height).
  useEffect(() => {
    if (useGraphStore.getState().nodes.length > 0) return;
    const ctrl = new AbortController();
    fetchGraph(vaultId, ctrl.signal)
      .then(({ data, cacheStatus }) => {
        useGraphStore
          .getState()
          .setGraph(data.nodes, data.edges, data.data_version, cacheStatus, data.communities ?? []);
      })
      .catch(() => {
        // non-blocking: tree/wiki reading unaffected
      });
    return () => ctrl.abort();
  }, [vaultId]);

  const leftRef = usePanelRef();
  const rightRef = usePanelRef();
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);

  const handleLayoutChanged = useCallback((layout: Layout) => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(layout));
    } catch {
      // ignore
    }
  }, []);

  const toggleLeft = useCallback(() => {
    if (leftCollapsed) {
      leftRef.current?.expand();
      setLeftCollapsed(false);
    } else {
      leftRef.current?.collapse();
      setLeftCollapsed(true);
    }
  }, [leftCollapsed, leftRef]);

  const toggleRight = useCallback(() => {
    if (rightCollapsed) {
      rightRef.current?.expand();
      setRightCollapsed(false);
    } else {
      rightRef.current?.collapse();
      setRightCollapsed(true);
    }
  }, [rightCollapsed, rightRef]);

  return (
    <Group
      id="synapse-panel-group"
      orientation="horizontal"
      defaultLayout={initialLayout}
      onLayoutChanged={handleLayoutChanged}
      style={{ width: "100%", height: "100%" }}
    >
      {/* Left panel — NavTree */}
      <Panel
        id={PANEL_LEFT}
        panelRef={leftRef}
        defaultSize="22%"
        minSize="15%"
        maxSize="40%"
        collapsible
        collapsedSize="3%"
        style={{
          position: "relative",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "var(--syn-bg-soft)",
          minWidth: 0,
        }}
      >
        {!leftCollapsed && (
          <>
            <ScenarioTemplates />
            <div style={{ flex: 1, overflow: "hidden" }}>
              <NavTree vaultId={vaultId} />
            </div>
          </>
        )}
        <CollapseButton direction="left" collapsed={leftCollapsed} onClick={toggleLeft} />
      </Panel>

      <Separator
        id="separator-left"
        aria-label="Resize left panel"
        style={SEPARATOR_STYLE}
      />

      {/* Center panel — NoteView (wiki pages section; graph section is in SectionRouter) */}
      <Panel
        id={PANEL_CENTER}
        defaultSize="56%"
        minSize="40%"
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "var(--syn-bg)",
          minWidth: 0,
        }}
      >
        <NoteView />
      </Panel>

      <Separator
        id="separator-right"
        aria-label="Resize right panel"
        style={SEPARATOR_STYLE}
      />

      {/* Right panel — PreviewPanel */}
      <Panel
        id={PANEL_RIGHT}
        panelRef={rightRef}
        defaultSize="22%"
        minSize="15%"
        maxSize="40%"
        collapsible
        collapsedSize="3%"
        style={{
          position: "relative",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "var(--syn-bg-soft)",
          minWidth: 0,
        }}
      >
        {!rightCollapsed && <PreviewPanel />}
        <CollapseButton direction="right" collapsed={rightCollapsed} onClick={toggleRight} />
      </Panel>
    </Group>
  );
}
