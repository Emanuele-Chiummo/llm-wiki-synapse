/**
 * SectionRouter.tsx — reads activeSection from graphStore and renders the matching layout.
 *
 * Section → layout mapping:
 *   home        → <HomeDashboard/> (v1.2 [F18/R12-1] — landing dashboard, default section)
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
 * P1 — code-split: all heavy views are loaded with React.lazy() so the initial
 * bundle only ships the Home dashboard and the shell. Each section chunk is fetched
 * on first navigation to that section and then cached by the browser.
 * HomeDashboard stays eager (it is the default landing view — lazy would add a
 * visible flash on first load).
 *
 * Light design: var(--syn-bg) content areas, var(--syn-bg-soft) side detail panels,
 * var(--syn-border) dividers, var(--syn-text-dim) placeholder text.
 *
 * INVARIANT I2: GraphPanel is reused verbatim — no layout/force code added here.
 * INVARIANT I3: reads only activeSection (scalar) — no unrelated store keys subscribed.
 */

import React, { Suspense } from "react";
import { useGraphStore, selectActiveSection } from "../store/graphStore";
import { SectionErrorBoundary } from "./common/SectionErrorBoundary";
// HomeDashboard is EAGER — it is the default landing view; lazy would add a flash.
import { HomeDashboard } from "./home/HomeDashboard";

// ─── Lazy section imports (P1 — code-split) ───────────────────────────────────
// Each section is its own async chunk. The browser fetches and caches it on first
// navigation; subsequent visits use the local cache (no re-download).
// Named-export wrapper: React.lazy requires a module with a `default` export.

const GraphPanel = React.lazy(() =>
  import("./center/GraphPanel").then((m) => ({ default: m.GraphPanel })),
);

const PanelGroup = React.lazy(() =>
  import("./panels/PanelGroup").then((m) => ({ default: m.PanelGroup })),
);

const ChatSection = React.lazy(() =>
  import("./chat/ChatSection").then((m) => ({ default: m.ChatSection })),
);

const IngestView = React.lazy(() =>
  import("./ingest/IngestView").then((m) => ({ default: m.IngestView })),
);

const IngestRunDetail = React.lazy(() =>
  import("./ingest/IngestRunDetail").then((m) => ({ default: m.IngestRunDetail })),
);

const SettingsPanel = React.lazy(() =>
  import("./settings/SettingsPanel").then((m) => ({ default: m.SettingsPanel })),
);

const DeepSearchView = React.lazy(() =>
  import("./research/DeepSearchView").then((m) => ({ default: m.DeepSearchView })),
);

const ReviewQueueView = React.lazy(() =>
  import("./review/ReviewQueueView").then((m) => ({ default: m.ReviewQueueView })),
);

const LintView = React.lazy(() =>
  import("./lint/LintView").then((m) => ({ default: m.LintView })),
);

const SearchView = React.lazy(() =>
  import("./search/SearchView").then((m) => ({ default: m.SearchView })),
);

const SourcesView = React.lazy(() =>
  import("./sources/SourcesView").then((m) => ({ default: m.SourcesView })),
);

const ConvertPanel = React.lazy(() =>
  import("./convert/ConvertPanel").then((m) => ({ default: m.ConvertPanel })),
);

const ProjectLauncher = React.lazy(() =>
  import("./projects/ProjectLauncher").then((m) => ({ default: m.ProjectLauncher })),
);

// ─── Loading skeleton (P1 — Suspense fallback) ────────────────────────────────
// Tiny, theme-aware placeholder shown while a lazy chunk downloads.
// Uses .syn-skeleton shimmer animation from theme.css (prefers-reduced-motion safe).
// Kept in this file to avoid an extra async chunk for the fallback itself.

function SectionSkeleton(): React.JSX.Element {
  return (
    <div
      aria-busy="true"
      aria-label="Loading section"
      style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        height: "100%",
        background: "var(--syn-bg)",
      }}
    >
      <span
        className="syn-skeleton"
        style={{
          display: "inline-block",
          width: 120,
          height: 6,
          borderRadius: 3,
        }}
      />
    </div>
  );
}

// ─── SectionRouter ────────────────────────────────────────────────────────────

export function SectionRouter() {
  const activeSection = useGraphStore(selectActiveSection);
  return (
    <SectionErrorBoundary sectionId={activeSection}>
      {/* Suspense catches async chunk loading (P1). SectionErrorBoundary above
          catches both lazy-load failures (network error on chunk fetch) and
          runtime render errors inside the loaded section. */}
      <Suspense fallback={<SectionSkeleton />}>
        <SectionContent activeSection={activeSection} />
      </Suspense>
    </SectionErrorBoundary>
  );
}

function SectionContent({ activeSection }: { activeSection: ReturnType<typeof selectActiveSection> }) {

  if (activeSection === "home") {
    return (
      <div
        style={{ flex: 1, overflow: "auto", width: "100%", height: "100%", background: "var(--syn-bg)" }}
        data-testid="section-home"
      >
        <HomeDashboard />
      </div>
    );
  }

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

  if (activeSection === "projects") {
    return (
      <div
        style={{ flex: 1, overflow: "auto", width: "100%", height: "100%", background: "var(--syn-bg)" }}
        data-testid="section-projects"
      >
        <ProjectLauncher />
      </div>
    );
  }

  return null;
}
