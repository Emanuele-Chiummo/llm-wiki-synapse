/**
 * App.tsx — Synapse v0.3 root component.
 *
 * Scope: thin sigma.js graph viewer (single route, read-only).
 * The 3-panel shell (F1), chat (F6), provider selector (F17), and
 * CodeMirror editor (F3/K5) are all v0.4 scope.
 *
 * DOM structure:
 *   <div#app>                  ← full-viewport flex column
 *     <header>                 ← minimal branding (2 DOM nodes)
 *     <main>                   ← fills remaining height
 *       <GraphViewer />        ← sigma canvas + overlays (<20 DOM nodes total)
 *
 * Total DOM count in the graph area: < 20 (I4 / G4).
 */

import React from "react";
import { GraphViewer } from "./components/GraphViewer";

const App: React.FC = () => {
  return (
    <div
      id="app"
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100vw",
        height: "100vh",
        background: "#0d1117",
        color: "#e6edf3",
      }}
    >
      {/* Minimal header — 2 DOM nodes */}
      <header
        style={{
          flexShrink: 0,
          height: 40,
          display: "flex",
          alignItems: "center",
          padding: "0 16px",
          borderBottom: "1px solid #21262d",
          background: "#161b22",
          gap: 8,
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 600, color: "#e6edf3" }}>Synapse</span>
        <span style={{ fontSize: 12, color: "#8b949e" }}>Knowledge Graph — v0.3</span>
      </header>

      {/* Main area — GraphViewer fills all remaining height */}
      <main style={{ flex: 1, overflow: "hidden", position: "relative" }}>
        <GraphViewer />
      </main>
    </div>
  );
};

export default App;
