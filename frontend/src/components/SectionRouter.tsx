/**
 * SectionRouter.tsx — reads activeSection from graphStore and renders the matching layout.
 *
 * Section → layout mapping:
 *   chat        → <ChatSection/>
 *   pages       → <PanelGroup/>  (NavTree | GraphPanel | PreviewPanel)
 *   ingest      → <IngestView/> + <IngestRunDetail/>
 *   graph       → <GraphPanel/> full-bleed
 *   search      → placeholder (M5)
 *   lint        → placeholder (M5)
 *   review      → placeholder (M5)
 *   deep-search → placeholder (M5)
 *   settings    → <SettingsPanel/> (single column)
 *
 * INVARIANT I2: GraphPanel is reused verbatim — no layout/force code added here.
 * INVARIANT I3: reads only activeSection (scalar) — no unrelated store keys subscribed.
 */

import { useTranslation } from "react-i18next";
import { useGraphStore, selectActiveSection } from "../store/graphStore";
import { PanelGroup } from "./panels/PanelGroup";
import { GraphPanel } from "./center/GraphPanel";
import { IngestView } from "./ingest/IngestView";
import { IngestRunDetail } from "./ingest/IngestRunDetail";
import { SettingsPanel } from "./settings/SettingsPanel";
import { ChatSection } from "./chat/ChatSection";
import { DeepSearchView } from "./research/DeepSearchView";

// ─── M5 placeholder ───────────────────────────────────────────────────────────

function ComingSoonPlaceholder({ titleKey, descKey }: { titleKey: string; descKey: string }) {
  const { t } = useTranslation();
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        color: "#484f58",
        fontSize: 13,
        userSelect: "none",
      }}
    >
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#30363d" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48 2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48 2.83-2.83"/>
      </svg>
      <span style={{ fontSize: 14, fontWeight: 600, color: "#30363d" }}>{t(titleKey)}</span>
      <span style={{ fontSize: 12, color: "#30363d", maxWidth: 280, textAlign: "center" }}>{t(descKey)}</span>
    </div>
  );
}

// ─── SectionRouter ────────────────────────────────────────────────────────────

export function SectionRouter() {
  const activeSection = useGraphStore(selectActiveSection);

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

  if (activeSection === "ingest") {
    return (
      <div
        style={{ display: "flex", flex: 1, overflow: "hidden", width: "100%", height: "100%" }}
        data-testid="section-ingest"
      >
        <div style={{ flex: 1, overflow: "hidden", minWidth: 0, background: "#0d1117" }}>
          <IngestView />
        </div>
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

  // M5 placeholder sections
  if (activeSection === "search") {
    return (
      <div style={{ flex: 1, display: "flex", background: "#0d1117" }} data-testid="section-search">
        <ComingSoonPlaceholder titleKey="nav.search" descKey="nav.comingSoon" />
      </div>
    );
  }

  if (activeSection === "lint") {
    return (
      <div style={{ flex: 1, display: "flex", background: "#0d1117" }} data-testid="section-lint">
        <ComingSoonPlaceholder titleKey="nav.lint" descKey="nav.comingSoon" />
      </div>
    );
  }

  if (activeSection === "review") {
    return (
      <div style={{ flex: 1, display: "flex", background: "#0d1117" }} data-testid="section-review">
        <ComingSoonPlaceholder titleKey="nav.review" descKey="nav.comingSoon" />
      </div>
    );
  }

  if (activeSection === "deep-search") {
    return (
      <div style={{ flex: 1, display: "flex", overflow: "hidden", background: "#0d1117" }} data-testid="section-deep-search">
        <DeepSearchView />
      </div>
    );
  }

  return null;
}
