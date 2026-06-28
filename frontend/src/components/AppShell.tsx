/**
 * AppShell.tsx — top-level layout combining Header, PanelGroup, and ActivityBar.
 *
 * Architecture (ADR-0017 §2):
 *
 *   ┌──────────────────────────────────────────────────┐
 *   │  Header (48px)                                   │
 *   ├────────────┬───────────────────┬─────────────────┤
 *   │  NavTree   │   MainTabs        │  PreviewPanel   │
 *   │  (left)    │   (center)        │  (right)        │
 *   │            │   [Graph / Chat]  │                 │
 *   ├────────────┴───────────────────┴─────────────────┤
 *   │  ActivityBar (28px)                              │
 *   └──────────────────────────────────────────────────┘
 *
 * Uses CSS flex-column; PanelGroup uses react-resizable-panels with
 * `autoSaveId` for localStorage persistence.
 */

import { Header } from "./Header";
import { PanelGroup } from "./panels/PanelGroup";
import { ActivityBar } from "./activity/ActivityBar";

export function AppShell() {
  return (
    <div
      className="app-shell"
      data-testid="app-shell"
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100vw",
        height: "100vh",
        overflow: "hidden",
        background: "#0d1117",
        color: "#e6edf3",
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
      }}
    >
      <Header />
      {/* PanelGroup grows to fill remaining vertical space.
          minHeight:0 is required so height:100% inside the Group resolves
          correctly in a flex-column context (without it the flex child has
          no definite height and Group collapses panels to content size). */}
      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PanelGroup />
      </div>
      <ActivityBar />
    </div>
  );
}
