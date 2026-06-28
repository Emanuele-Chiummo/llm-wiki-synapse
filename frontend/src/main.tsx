/**
 * main.tsx — Synapse frontend entry point.
 *
 * React 19 + Vite. Single-page app: one route "/" renders the GraphViewer.
 * No router library needed for v0.3 (single route per ADR-0015 §2).
 */

import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

const rootEl = document.getElementById("root");

if (!rootEl) {
  throw new Error("[Synapse] Root element #root not found. Check index.html.");
}

// React 19 concurrent root
createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
