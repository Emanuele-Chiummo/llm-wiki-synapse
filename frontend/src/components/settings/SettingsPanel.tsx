/**
 * SettingsPanel.tsx — 2-level nav shell (ADR-0055).
 *
 * 6 groups × 18 pages. Group headers are non-clickable labels.
 * Page items are buttons with data-testid="settings-nav-<pageId>",
 * data-settings-section={pageId}, and aria-current on active.
 * Default active: "appearance".
 *
 * Deep-link: listens to CustomEvent "synapse:settingsSection" with detail.section = pageId.
 * Arrow keys traverse only the 18 page items (skip group headers).
 *
 * INVARIANT I3: subscribes via typed selectors only.
 * INVARIANT I6: no hardcoded model/provider IDs.
 */

import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { OpsScheduleCard } from "./OpsScheduleCard";
import { SectionGeneral } from "./sections/SectionGeneral";
import { SectionLlmModels } from "./sections/SectionLlmModels";
import { SectionEmbeddings } from "./sections/SectionEmbeddings";
import { SectionSourceWatch } from "./sections/SectionSourceWatch";
import { SectionWebSearch } from "./sections/SectionWebSearch";
import { SectionApiMcp } from "./sections/SectionApiMcp";
import { SectionWebClipper } from "./sections/SectionWebClipper";
import { SectionOutput } from "./sections/SectionOutput";
import { SectionInterface } from "./sections/SectionInterface";
import { SectionMaintenance } from "./sections/SectionMaintenance";
import { SectionAbout } from "./sections/SectionAbout";
import { SectionScenarios } from "./sections/SectionScenarios";
import { SectionCosts } from "./sections/SectionCosts";
import { SectionSecurity } from "./sections/SectionSecurity";
import { SectionRuntimeConfig } from "./sections/SectionRuntimeConfig";
import {
  SectionHeader, GroupDivider, BTN_PRIMARY,
  IconSliders, IconCpu, IconFolder, IconWrench, IconBook, IconLink, IconShield,
  IconPalette, IconWand, IconBolt, IconVectors, IconGlobe, IconClock,
  IconScissors, IconFileText, IconLock, IconDollar, IconInfo,
} from "./ui";

// ─── Page type ────────────────────────────────────────────────────────────────
// 17 stable page IDs — one per leaf page in the 2-level nav.

type SettingsPage =
  // Group: Generale
  | "appearance"
  | "setup"
  // Group: AI e modelli
  | "providers"
  | "scenarios"
  | "context"
  | "embeddings"
  | "webSearch"
  // Group: Contenuti wiki
  | "generation"
  | "automation"
  | "limits"
  // Group: Sorgenti e import
  | "sourceWatch"
  | "clipper"
  | "pdf"
  // Group: Connessioni
  | "apiMcp"
  | "security"
  // Group: Sistema
  | "costs"
  | "maintenance"
  | "about";

// ─── Nav structure ─────────────────────────────────────────────────────────────

interface NavGroup {
  id: string;
  labelKey: string;
  pages: NavPage[];
}

interface NavPage {
  id: SettingsPage;
  labelKey: string;
  icon: ReactNode;
}

const NAV_GROUPS: NavGroup[] = [
  {
    id: "generale",
    labelKey: "settings.nav.groupGeneral",
    pages: [
      { id: "appearance", labelKey: "settings.nav.appearance", icon: <IconPalette /> },
      { id: "setup",      labelKey: "settings.nav.setup",      icon: <IconWand /> },
    ],
  },
  {
    id: "aiModelli",
    labelKey: "settings.nav.groupAiModelsNew",
    pages: [
      { id: "providers",  labelKey: "settings.nav.providers",  icon: <IconCpu /> },
      { id: "scenarios",  labelKey: "settings.nav.scenarios",  icon: <IconBolt /> },
      { id: "context",    labelKey: "settings.nav.context",    icon: <IconSliders /> },
      { id: "embeddings", labelKey: "settings.nav.embeddings2", icon: <IconVectors /> },
      { id: "webSearch",  labelKey: "settings.nav.webSearch2", icon: <IconGlobe /> },
    ],
  },
  {
    id: "wikiContent",
    labelKey: "settings.nav.groupWikiContent",
    pages: [
      { id: "generation", labelKey: "settings.nav.generation", icon: <IconBook /> },
      { id: "automation", labelKey: "settings.nav.automation", icon: <IconClock /> },
      { id: "limits",     labelKey: "settings.nav.limits",     icon: <IconShield /> },
    ],
  },
  {
    id: "sourcesImport",
    labelKey: "settings.nav.groupSourcesImport",
    pages: [
      { id: "sourceWatch", labelKey: "settings.nav.sourceWatch2", icon: <IconFolder /> },
      { id: "clipper",     labelKey: "settings.nav.clipper",      icon: <IconScissors /> },
      { id: "pdf",         labelKey: "settings.nav.pdf",          icon: <IconFileText /> },
    ],
  },
  {
    id: "connections",
    labelKey: "settings.nav.groupConnections",
    pages: [
      { id: "apiMcp",   labelKey: "settings.nav.apiMcp2",   icon: <IconLink /> },
      { id: "security", labelKey: "settings.nav.security2", icon: <IconLock /> },
    ],
  },
  {
    id: "system",
    labelKey: "settings.nav.groupSystem",
    pages: [
      { id: "costs",       labelKey: "settings.nav.costs2",       icon: <IconDollar /> },
      { id: "maintenance", labelKey: "settings.nav.maintenance2", icon: <IconWrench /> },
      { id: "about",       labelKey: "settings.nav.about2",       icon: <IconInfo /> },
    ],
  },
];

// Flat ordered list of all 18 page IDs (for arrow-key traversal)
const ALL_PAGES: SettingsPage[] = NAV_GROUPS.flatMap((g) => g.pages.map((p) => p.id));

// ─── SettingsPanel ─────────────────────────────────────────────────────────────

export function SettingsPanel() {
  const [activePage, setActivePage] = useState<SettingsPage>("appearance");
  const { t } = useTranslation();
  const pageRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Deep-link: listen to synapse:settingsSection CustomEvent (same pattern as synapse:openWizard)
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ section: string }>).detail;
      if (detail?.section && ALL_PAGES.includes(detail.section as SettingsPage)) {
        setActivePage(detail.section as SettingsPage);
      }
    };
    window.addEventListener("synapse:settingsSection", handler);
    return () => { window.removeEventListener("synapse:settingsSection", handler); };
  }, []);

  // Arrow-key navigation over the 18 page items only (skip group headers)
  const handleNavKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      const currentIdx = ALL_PAGES.indexOf(activePage);
      let nextIdx = currentIdx;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        nextIdx = (currentIdx + 1) % ALL_PAGES.length;
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        nextIdx = (currentIdx - 1 + ALL_PAGES.length) % ALL_PAGES.length;
      } else if (e.key === "Home") {
        e.preventDefault();
        nextIdx = 0;
      } else if (e.key === "End") {
        e.preventDefault();
        nextIdx = ALL_PAGES.length - 1;
      } else {
        return;
      }

      const nextPage = ALL_PAGES[nextIdx];
      if (nextPage) {
        setActivePage(nextPage);
        pageRefs.current[nextIdx]?.focus();
      }
    },
    [activePage],
  );

  return (
    <div
      data-testid="settings-panel"
      style={{
        display: "flex",
        width: "100%",
        height: "100%",
        color: "var(--syn-text)",
        fontSize: 13,
        overflow: "hidden",
      }}
    >
      {/* ── 2-level left nav ── */}
      <aside
        role="navigation"
        aria-label={t("settings.title")}
        onKeyDown={handleNavKeyDown}
        style={{
          width: 200,
          flexShrink: 0,
          background: "var(--syn-bg-soft)",
          borderRight: "1px solid var(--syn-border)",
          display: "flex",
          flexDirection: "column",
          padding: "16px 0",
          overflowY: "auto",
        }}
      >
        <p style={{ margin: "0 12px 12px", fontSize: 11, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--syn-text-dim)" }}>
          {t("settings.title")}
        </p>

        {NAV_GROUPS.map((group) => (
          <div key={group.id}>
            {/* Group header — non-clickable label */}
            <p
              style={{
                margin: "12px 12px 4px",
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                color: "var(--syn-text-dim)",
                opacity: 0.7,
              }}
            >
              {t(group.labelKey)}
            </p>

            {/* Page items */}
            {group.pages.map((page) => {
              const globalIdx = ALL_PAGES.indexOf(page.id);
              const isActive = activePage === page.id;
              return (
                <button
                  key={page.id}
                  ref={(el) => { pageRefs.current[globalIdx] = el; }}
                  data-settings-section={page.id}
                  data-testid={`settings-nav-${page.id}`}
                  aria-current={isActive ? "true" : undefined}
                  tabIndex={isActive ? 0 : -1}
                  onClick={() => setActivePage(page.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    width: "100%",
                    padding: "6px 12px 6px 18px",
                    border: "none",
                    background: isActive ? "var(--syn-accent-soft)" : "transparent",
                    color: isActive ? "var(--syn-text)" : "var(--syn-text-dim)",
                    fontSize: 12,
                    cursor: "pointer",
                    textAlign: "left",
                    borderRadius: 0,
                    borderLeft: isActive ? "2px solid var(--syn-accent)" : "2px solid transparent",
                    transition: "background 0.1s ease, color 0.1s ease",
                  }}
                  onMouseEnter={(e) => {
                    if (!isActive) {
                      (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-surface-hover)";
                      (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-muted)";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isActive) {
                      (e.currentTarget as HTMLButtonElement).style.background = "transparent";
                      (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-dim)";
                    }
                  }}
                >
                  <span style={{ display: "inline-flex", flexShrink: 0, opacity: isActive ? 1 : 0.6 }} aria-hidden="true">
                    {page.icon}
                  </span>
                  {t(page.labelKey)}
                </button>
              );
            })}
          </div>
        ))}
      </aside>

      {/* ── Content area ── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "32px 40px", maxWidth: 720 }}>
        {activePage === "appearance" && <PageAppearance />}
        {activePage === "setup"      && <PageSetup />}
        {activePage === "providers"  && <SectionLlmModels />}
        {activePage === "scenarios"  && <SectionScenarios />}
        {activePage === "context"    && <SectionGeneral />}
        {activePage === "embeddings" && <PageEmbeddings />}
        {activePage === "webSearch"  && <SectionWebSearch />}
        {activePage === "generation" && <PageGeneration />}
        {activePage === "automation" && <PageAutomation />}
        {activePage === "limits"     && <PageLimits />}
        {activePage === "sourceWatch" && <SectionSourceWatch />}
        {activePage === "clipper"    && <SectionWebClipper />}
        {activePage === "pdf"        && <PagePdf />}
        {activePage === "apiMcp"     && <SectionApiMcp />}
        {activePage === "security"   && <SectionSecurity />}
        {activePage === "costs"      && <SectionCosts />}
        {activePage === "maintenance" && <SectionMaintenance />}
        {activePage === "about"      && <SectionAbout />}
      </div>
    </div>
  );
}

// ─── Composite page wrappers ────────────────────────────────────────────────────

/** appearance: SectionInterface + SectionOutput */
function PageAppearance() {
  return (
    <div>
      <SectionInterface />
      <GroupDivider />
      <SectionOutput />
    </div>
  );
}

/** setup: wizard re-open slot */
function PageSetup() {
  const { t } = useTranslation();
  function handleOpenWizard() {
    window.dispatchEvent(new Event("synapse:openWizard"));
  }
  return (
    <div data-testid="wizard-placeholder-slot">
      <SectionHeader
        title={t("config.gettingStarted.wizardSlot")}
        desc={t("config.gettingStarted.wizardSlotDesc")}
      />
      <button
        data-testid="wizard-reopen-btn"
        onClick={handleOpenWizard}
        style={BTN_PRIMARY}
      >
        {t("config.gettingStarted.wizardReopen")}
      </button>
    </div>
  );
}

/** embeddings: SectionEmbeddings + runtime keys embeddings_enabled + embedding_format */
function PageEmbeddings() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionEmbeddings />
      <GroupDivider />
      <SectionHeader
        title={t("config.runtimeOverridesSection.title")}
        desc={t("config.runtimeOverridesSection.desc")}
      />
      <SectionRuntimeConfig keys={["embeddings_enabled", "embedding_format"]} />
    </div>
  );
}

/** generation: runtime keys overview_language + wikilink_enrich_enabled + domain_vocabulary */
function PageGeneration() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader
        title={t("settings.nav.generation")}
        desc={t("config.runtimeOverridesSection.desc")}
      />
      <SectionRuntimeConfig keys={["overview_language", "wikilink_enrich_enabled", "domain_vocabulary"]} />
    </div>
  );
}

/** automation: OpsScheduleCard */
function PageAutomation() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader
        title={t("settings.opsSchedule.title")}
        desc={t("settings.opsSchedule.sectionDesc")}
      />
      <OpsScheduleCard />
    </div>
  );
}

/** limits: 5 loop-cap runtime keys (S14–S18, I7) */
function PageLimits() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader
        title={t("config.limitsSection.title")}
        desc={t("config.limitsSection.desc")}
      />
      <SectionRuntimeConfig keys={[
        "deep_research_max_iter",
        "deep_research_token_budget",
        "deep_research_max_queries",
        "lint_max_iter",
        "lint_token_budget",
      ]} />
    </div>
  );
}

/** pdf: runtime keys pdf_extractor + marker_service_url + marker_timeout_seconds */
function PagePdf() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader
        title={t("config.pdfExtractorSection.title")}
        desc={t("config.pdfExtractorSection.desc")}
      />
      <SectionRuntimeConfig keys={["pdf_extractor", "marker_service_url", "marker_timeout_seconds"]} />
    </div>
  );
}
