/**
 * SectionRouter.tsx — reads activeSection from graphStore and renders the matching layout.
 *
 * Section → layout mapping:
 *   chat        → <ChatSection/>
 *   pages       → <PanelGroup/>  (NavTree | GraphPanel | PreviewPanel)
 *   sources     → <SourcesView/> (v0.6 [F11] — raw-source file browser)
 *   ingest      → <IngestView/> + <IngestRunDetail/> (ingest run history / cost ledger)
 *   graph       → <GraphPanel/> full-bleed
 *   search      → <SearchView/>   (v0.6, GET /search — F5/llm_wiki parity)
 *   lint        → <LintView/>
 *   review      → <ReviewQueueView/>
 *   deep-search → <DeepSearchView/>
 *   settings    → <SettingsPanel/> (single column)
 *   convert     → <ConvertPanel/> (v1.1 [F12/R11-1 A1] — dedicated Marker PDF conversion)
 *
 * Light design: var(--syn-bg) content areas, var(--syn-bg-soft) side detail panels,
 * var(--syn-border) dividers, var(--syn-text-dim) placeholder text.
 *
 * INVARIANT I2: GraphPanel is reused verbatim — no layout/force code added here.
 * INVARIANT I3: reads only activeSection (scalar) — no unrelated store keys subscribed.
 */

import { useGraphStore, selectActiveSection } from "../store/graphStore";
import { SectionErrorBoundary } from "./common/SectionErrorBoundary";
import { PanelGroup } from "./panels/PanelGroup";
import { GraphPanel } from "./center/GraphPanel";
import { IngestView } from "./ingest/IngestView";
import { IngestRunDetail } from "./ingest/IngestRunDetail";
import { SettingsPanel } from "./settings/SettingsPanel";
import { ChatSection } from "./chat/ChatSection";
import { DeepSearchView } from "./research/DeepSearchView";
import { ReviewQueueView } from "./review/ReviewQueueView";
import { LintView } from "./lint/LintView";
import { SearchView } from "./search/SearchView";
import { SourcesView } from "./sources/SourcesView";
import { ConvertPanel } from "./convert/ConvertPanel";

// ─── SectionRouter ────────────────────────────────────────────────────────────

export function SectionRouter() {
  const activeSection = useGraphStore(selectActiveSection);
  return (
    <SectionErrorBoundary sectionId={activeSection}>
      <SectionContent activeSection={activeSection} />
    </SectionErrorBoundary>
  );
}

function SectionContent({ activeSection }: { activeSection: ReturnType<typeof selectActiveSection> }) {

  if (activeSection === "chat") {
    return <ChatSection />;
  }

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

  if (activeSection === "sources") {
    return (
      <div
        style={{ flex: 1, display: "flex", overflow: "hidden", width: "100%", height: "100%" }}
        data-testid="section-sources"
      >
        <SourcesView />
      </div>
    );
  }

  if (activeSection === "ingest") {
    return (
      <div
        className="ingest-section"
        style={{ display: "flex", flex: 1, overflow: "hidden", width: "100%", height: "100%" }}
        data-testid="section-ingest"
      >
        <div style={{ flex: 1, overflow: "hidden", minWidth: 0, background: "var(--syn-bg)" }}>
          <IngestView />
        </div>
        <div
          className="ingest-section__detail"
          style={{
            width: 320,
            flexShrink: 0,
            overflow: "hidden",
            background: "var(--syn-bg-soft)",
            borderLeft: "1px solid var(--syn-border)",
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
        style={{ flex: 1, overflow: "auto", width: "100%", height: "100%", background: "var(--syn-bg)" }}
        data-testid="section-settings"
      >
        <SettingsPanel />
      </div>
    );
  }

  if (activeSection === "search") {
    return (
      <div style={{ flex: 1, display: "flex", overflow: "hidden", background: "var(--syn-bg)" }} data-testid="section-search">
        <SearchView />
      </div>
    );
  }

  if (activeSection === "lint") {
    return (
      <div style={{ flex: 1, display: "flex", overflow: "hidden", background: "var(--syn-bg)" }} data-testid="section-lint">
        <LintView />
      </div>
    );
  }

  if (activeSection === "review") {
    return (
      <div style={{ flex: 1, display: "flex", overflow: "hidden", background: "var(--syn-bg)" }} data-testid="section-review">
        <ReviewQueueView />
      </div>
    );
  }

  if (activeSection === "deep-search") {
    return (
      <div style={{ flex: 1, display: "flex", overflow: "hidden", background: "var(--syn-bg)" }} data-testid="section-deep-search">
        <DeepSearchView />
      </div>
    );
  }

  if (activeSection === "convert") {
    return (
      <div
        style={{ flex: 1, overflow: "auto", width: "100%", height: "100%", background: "var(--syn-bg)" }}
        data-testid="section-convert"
      >
        <ConvertPanel />
      </div>
    );
  }

  return null;
}
