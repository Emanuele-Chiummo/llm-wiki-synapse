/**
 * SectionRouter.tsx — reads activeSection from graphStore and renders the matching layout.
 *
 * ADR-0018 §1 section → panel mapping:
 *   pages    → <PanelGroup/>  (NavTree | GraphPanel | PreviewPanel — ADR-0017, verbatim)
 *   graph    → <GraphPanel/>  full-bleed
 *   ingest   → <IngestView/>  (center) + <IngestRunDetail/> (right)
 *   settings → <SettingsPanel/> (single column)
 *
 * INVARIANT I2: GraphPanel is reused verbatim — no layout/force code added here.
 * INVARIANT I3: reads only activeSection (scalar) — no unrelated store keys subscribed.
 */

import { useGraphStore, selectActiveSection } from "../store/graphStore";
import { PanelGroup } from "./panels/PanelGroup";
import { GraphPanel } from "./center/GraphPanel";
import { IngestView } from "./ingest/IngestView";
import { IngestRunDetail } from "./ingest/IngestRunDetail";
import { SettingsPanel } from "./settings/SettingsPanel";

export function SectionRouter() {
  const activeSection = useGraphStore(selectActiveSection);

  if (activeSection === "pages") {
    return <PanelGroup />;
  }

  if (activeSection === "graph") {
    return (
      <div
        style={{ flex: 1, overflow: "hidden", width: "100%", height: "100%" }}
        data-testid="section-graph"
      >
        <GraphPanel />
      </div>
    );
  }

  if (activeSection === "ingest") {
    return (
      <div
        style={{ display: "flex", flex: 1, overflow: "hidden", width: "100%", height: "100%" }}
        data-testid="section-ingest"
      >
        {/* Center: IngestView (run list + trigger) */}
        <div style={{ flex: 1, overflow: "hidden", minWidth: 0, background: "#0d1117" }}>
          <IngestView />
        </div>
        {/* Right: IngestRunDetail */}
        <div
          style={{
            width: 320,
            flexShrink: 0,
            overflow: "hidden",
            background: "#161b22",
            borderLeft: "1px solid #21262d",
          }}
        >
          <IngestRunDetail />
        </div>
      </div>
    );
  }

  if (activeSection === "settings") {
    return (
      <div
        style={{ flex: 1, overflow: "auto", width: "100%", height: "100%", background: "#0d1117" }}
        data-testid="section-settings"
      >
        <SettingsPanel />
      </div>
    );
  }

  // Fallback — should never happen with exhaustive Section type
  return null;
}
