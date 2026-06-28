/**
 * GraphPanel.tsx — thin wrapper that mounts GraphViewer in the center panel.
 *
 * Keeps MainTabs clean: it only decides which panel to show; the heavy
 * sigma.js setup stays entirely inside GraphViewer.
 */

import { GraphViewer } from "../GraphViewer";

export function GraphPanel() {
  return (
    <div
      className="graph-panel"
      style={{ position: "relative", width: "100%", height: "100%" }}
      data-testid="graph-panel"
    >
      <GraphViewer />
    </div>
  );
}
