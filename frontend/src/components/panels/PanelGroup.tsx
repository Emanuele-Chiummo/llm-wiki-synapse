/**
 * PanelGroup.tsx — react-resizable-panels@4.12.0 wrapper providing the 3-panel layout.
 *
 * CRITICAL API NOTE for v4.12.0:
 *   - Plain number props on Panel (defaultSize, minSize, maxSize) are interpreted as PIXELS,
 *     not percentages. Sizes must be passed as strings: "22%", "15%", etc.
 *   - defaultLayout on Group uses plain numbers that ARE percentages (0–100). These go
 *     through W() which sums them and normalises to 100 — no unit suffix needed there.
 *   - Separator (not PanelResizeHandle) is the resize handle component.
 *   - Group renders height:100% internally; the parent must have minHeight:0 in a flex
 *     column so that percentage resolves correctly (AppShell sets this).
 *
 * Layout: left 22% (min 15%) | separator | center 56% (min 40%) | separator | right 22% (min 15%)
 * At 1440px innerWidth the separators are ~4px each → usable widths ≈ left 316px / center 806px / right 316px.
 */

import { useCallback } from "react";
import type { CSSProperties } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import type { Layout } from "react-resizable-panels";
import { NavTree } from "../nav/NavTree";
import { GraphPanel } from "../center/GraphPanel";
import { PreviewPanel } from "../preview/PreviewPanel";
import { ScenarioTemplates } from "../common/ScenarioTemplates";
import { useGraphStore } from "../../store/graphStore";
import { selectVaultId } from "../../store/graphStore";

// ─── Panel IDs (must match id prop on each <Panel> AND defaultLayout keys) ───

const PANEL_LEFT = "panel-left";
const PANEL_CENTER = "panel-center";
const PANEL_RIGHT = "panel-right";
const LS_KEY = "synapse-panel-layout-v2"; // v2 suffix avoids stale pixel-based entries

// ─── Default layout — numbers are percentages for Group.defaultLayout ─────────

const DEFAULT_LAYOUT: Layout = {
  [PANEL_LEFT]: 22,
  [PANEL_CENTER]: 56,
  [PANEL_RIGHT]: 22,
};

// ─── Persist layout to localStorage ──────────────────────────────────────────

function loadLayout(): Layout {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Layout;
      // Validate all three keys are present with reasonable numeric values (0–100)
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
    // ignore parse / storage errors
  }
  return DEFAULT_LAYOUT;
}

// ─── Separator style ──────────────────────────────────────────────────────────

const SEPARATOR_STYLE: CSSProperties = {
  width: 4,
  flexShrink: 0,
  background: "#21262d",
  cursor: "col-resize",
  transition: "background 0.12s ease",
};

// ─── Component ────────────────────────────────────────────────────────────────

export function PanelGroup() {
  const vaultId = useGraphStore(selectVaultId);
  const initialLayout = loadLayout();

  const handleLayoutChanged = useCallback((layout: Layout) => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(layout));
    } catch {
      // storage full or private-mode — ignore
    }
  }, []);

  return (
    <Group
      id="synapse-panel-group"
      orientation="horizontal"
      // defaultLayout values are percentages (0–100); processed by the
      // Group's W() normaliser which treats them as percentages.
      defaultLayout={initialLayout}
      onLayoutChanged={handleLayoutChanged}
      style={{ width: "100%", height: "100%" }}
    >
      {/* Left panel — NavTree
          minSize/maxSize/defaultSize are STRINGS with "%" so they are parsed
          as percentages, not pixels (bt() in the library: number → px, string → %). */}
      <Panel
        id={PANEL_LEFT}
        defaultSize="22%"
        minSize="15%"
        maxSize="40%"
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "#161b22",
          minWidth: 0,
        }}
      >
        <ScenarioTemplates />
        <div style={{ flex: 1, overflow: "hidden" }}>
          <NavTree vaultId={vaultId} />
        </div>
      </Panel>

      <Separator
        id="separator-left"
        aria-label="Resize left panel"
        style={SEPARATOR_STYLE}
      />

      {/* Center panel — GraphPanel (ADR-0018 §1: one canonical graph location) */}
      <Panel
        id={PANEL_CENTER}
        defaultSize="56%"
        minSize="40%"
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "#0d1117",
          minWidth: 0,
        }}
      >
        <GraphPanel />
      </Panel>

      <Separator
        id="separator-right"
        aria-label="Resize right panel"
        style={SEPARATOR_STYLE}
      />

      {/* Right panel — PreviewPanel */}
      <Panel
        id={PANEL_RIGHT}
        defaultSize="22%"
        minSize="15%"
        maxSize="40%"
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "#161b22",
          minWidth: 0,
        }}
      >
        <PreviewPanel />
      </Panel>
    </Group>
  );
}
