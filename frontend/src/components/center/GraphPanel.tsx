/**
 * GraphPanel.tsx — thin wrapper that mounts GraphViewer in the center panel.
 *
 * Keeps MainTabs clean: it only decides which panel to show; the heavy
 * sigma.js setup stays entirely inside GraphViewer.
 *
 * GraphInsightsPanel is rendered as an absolutely-positioned overlay sibling
 * of GraphViewer inside this relative container (F4, G-P1-5).
 * GraphViewer.tsx is NOT modified — the overlay is added here only.
 */

import { GraphViewer } from "../GraphViewer";
import { GraphInsightsPanel } from "../graph/GraphInsightsPanel";

export function GraphPanel() {
  return (
    <div
      className="graph-panel"
      style={{ position: "relative", width: "100%", height: "100%" }}
      data-testid="graph-panel"
    >
      <GraphViewer />
      <GraphInsightsPanel />
    </div>
  );
}
