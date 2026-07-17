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
 *
 * Mobile/PWA (R10-5, AC-R10-5-1): At <768px, theme.css targets the Panel classNames
 * below to hide the left/right panels and let the center fill full width. The
 * horizontal orientation is kept (no orientation switch needed); CSS hides side panels.
 * This is the simplest robust approach — no new props, no layout library changes.
 *
 * ADR-0057: Desktop collapse state migrated from local useState to uiStore.
 * Mobile/tablet: PanelDrawer overlays provide access to tree + preview panels.
 * Tree drawer closes automatically when a page is selected (selectedNodeId change).
 */

import { useCallback, useEffect, useRef, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Group, Panel, Separator, usePanelRef } from "react-resizable-panels";
import type { Layout } from "react-resizable-panels";
import { NavTree } from "../nav/NavTree";
import { NoteView } from "../wiki/NoteView";
import { PreviewPanel } from "../preview/PreviewPanel";
import { ScenarioTemplates } from "../common/ScenarioTemplates";
import { PanelDrawer } from "./PanelDrawer";
import { useGraphStore } from "../../store/graphStore";
import { selectVaultId, selectSelectedNodeId, useAppStore } from "../../store/appStore";
import {
  useUiStore,
  selectLeftPanelOpen,
  selectRightPanelOpen,
  selectTreeDrawerOpen,
  selectPreviewDrawerOpen,
  selectSetLeftPanelOpen,
  selectSetRightPanelOpen,
  selectOpenTreeDrawer,
  selectCloseTreeDrawer,
  selectOpenPreviewDrawer,
  selectClosePreviewDrawer,
} from "../../store/uiStore";
import { useViewport } from "../../hooks/useViewport";
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
        typeof l === "number" &&
        l > 0 &&
        typeof c === "number" &&
        c > 0 &&
        typeof r === "number" &&
        r > 0
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
  const { t } = useTranslation();
  const pointsRight = direction === "left" ? !collapsed : collapsed;
  return (
    <button
      onClick={onClick}
      data-testid={`collapse-${direction}-btn`}
      aria-label={collapsed ? t("panels.expandPanel") : t("panels.collapsePanel")}
      title={collapsed ? t("panels.expand") : t("panels.collapse")}
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

// ─── Mobile toolbar ───────────────────────────────────────────────────────────

function MobileToolbar({
  showTreeButton,
  onOpenTree,
  onOpenPreview,
}: {
  showTreeButton: boolean;
  onOpenTree: () => void;
  onOpenPreview: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="panel-group__mobile-toolbar"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "4px 8px",
        background: "var(--syn-bg-soft)",
        borderBottom: "1px solid var(--syn-border)",
        flexShrink: 0,
        gap: 8,
      }}
    >
      {showTreeButton ? (
        <button
          data-testid="open-tree-drawer-btn"
          aria-label={t("panels.openTree")}
          title={t("panels.openTree")}
          onClick={onOpenTree}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 12px",
            minHeight: 44,
            border: "1px solid var(--syn-border)",
            borderRadius: "var(--syn-radius-sm)",
            background: "var(--syn-surface)",
            color: "var(--syn-text-muted)",
            cursor: "pointer",
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          {/* Simple icon approximation */}
          <span aria-hidden="true">☰</span>
          <span>{t("panels.openTree")}</span>
        </button>
      ) : (
        <div />
      )}

      <button
        data-testid="open-preview-drawer-btn"
        aria-label={t("panels.openPreview")}
        title={t("panels.openPreview")}
        onClick={onOpenPreview}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "8px 12px",
          minHeight: 44,
          border: "1px solid var(--syn-border)",
          borderRadius: "var(--syn-radius-sm)",
          background: "var(--syn-surface)",
          color: "var(--syn-text-muted)",
          cursor: "pointer",
          fontSize: 12,
          fontWeight: 600,
        }}
      >
        <span>{t("panels.openPreview")}</span>
        <span aria-hidden="true">›</span>
      </button>
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function PanelGroup() {
  const { t } = useTranslation();
  const vaultId = useAppStore(selectVaultId);
  const selectedNodeId = useAppStore(selectSelectedNodeId);
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

  // ── uiStore — desktop collapse state (ADR-0057 §3) ───────────────────────
  // Replaces PanelGroup's former local useState for leftCollapsed/rightCollapsed.
  // Desktop collapse behavior is identical — only the state source changes.
  const leftPanelOpen = useUiStore(selectLeftPanelOpen);
  const rightPanelOpen = useUiStore(selectRightPanelOpen);
  const setLeftPanelOpen = useUiStore(selectSetLeftPanelOpen);
  const setRightPanelOpen = useUiStore(selectSetRightPanelOpen);
  const leftCollapsed = !leftPanelOpen;
  const rightCollapsed = !rightPanelOpen;

  // Drawer state
  const treeDrawerOpen = useUiStore(selectTreeDrawerOpen);
  const previewDrawerOpen = useUiStore(selectPreviewDrawerOpen);
  const openTreeDrawer = useUiStore(selectOpenTreeDrawer);
  const closeTreeDrawer = useUiStore(selectCloseTreeDrawer);
  const openPreviewDrawer = useUiStore(selectOpenPreviewDrawer);
  const closePreviewDrawer = useUiStore(selectClosePreviewDrawer);

  // Close tree drawer when a page is selected (ADR-0057 §4 — "selecting a page closes the drawer").
  const prevSelectedNodeId = useRef(selectedNodeId);
  useEffect(() => {
    if (treeDrawerOpen && selectedNodeId !== prevSelectedNodeId.current) {
      closeTreeDrawer();
    }
    prevSelectedNodeId.current = selectedNodeId;
  }, [selectedNodeId, treeDrawerOpen, closeTreeDrawer]);

  // Close drawers when unmounting (navigating away from pages section).
  useEffect(() => {
    return () => {
      closeTreeDrawer();
      closePreviewDrawer();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Viewport tier for mobile toolbar visibility (ADR-0057 §1).
  const viewport = useViewport();
  const isMobile = viewport === "mobile";
  const isTablet = viewport === "tablet";
  const showMobileToolbar = isMobile || isTablet;

  const leftRef = usePanelRef();
  const rightRef = usePanelRef();

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
      setLeftPanelOpen(true);
    } else {
      leftRef.current?.collapse();
      setLeftPanelOpen(false);
    }
  }, [leftCollapsed, leftRef, setLeftPanelOpen]);

  const toggleRight = useCallback(() => {
    if (rightCollapsed) {
      rightRef.current?.expand();
      setRightPanelOpen(true);
    } else {
      rightRef.current?.collapse();
      setRightPanelOpen(false);
    }
  }, [rightCollapsed, rightRef, setRightPanelOpen]);

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%" }}>
      {/* Mobile/tablet toolbar: tree + preview drawer buttons (ADR-0057 §5) */}
      {showMobileToolbar && (
        <MobileToolbar
          showTreeButton={isMobile}
          onOpenTree={openTreeDrawer}
          onOpenPreview={openPreviewDrawer}
        />
      )}

      <Group
        id="synapse-panel-group"
        orientation="horizontal"
        defaultLayout={initialLayout}
        onLayoutChanged={handleLayoutChanged}
        style={{ flex: 1, minHeight: 0 }}
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
          className="panel-group__panel--left"
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
          aria-label={t("panels.resizeLeft")}
          className="panel-group__separator--left"
          style={SEPARATOR_STYLE}
        />

        {/* Center panel — NoteView (wiki pages section; graph section is in SectionRouter) */}
        <Panel
          id={PANEL_CENTER}
          defaultSize="56%"
          minSize="40%"
          className="panel-group__panel--center"
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
          aria-label={t("panels.resizeRight")}
          className="panel-group__separator--right"
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
          className="panel-group__panel--right"
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

      {/* Mobile tree drawer — slides in from left (ADR-0057 §3, §4) */}
      <PanelDrawer
        open={treeDrawerOpen}
        side="left"
        onClose={closeTreeDrawer}
        label={t("panels.openTree")}
      >
        <ScenarioTemplates />
        <div style={{ flex: 1, overflow: "hidden" }}>
          <NavTree vaultId={vaultId} />
        </div>
      </PanelDrawer>

      {/* Mobile/tablet preview drawer — slides in from right (ADR-0057 §3, §4) */}
      <PanelDrawer
        open={previewDrawerOpen}
        side="right"
        onClose={closePreviewDrawer}
        label={t("panels.openPreview")}
      >
        <PreviewPanel />
      </PanelDrawer>
    </div>
  );
}
