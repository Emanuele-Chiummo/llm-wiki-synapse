/**
 * SettingsPanel.tsx — 2-level nav shell (ADR-0055).
 *
 * 5 groups × 18 pages. Group headers are non-clickable labels with a short
 * description for novice orientation (v1.3.9 IA redesign).
 * Page items are buttons with data-testid="settings-nav-<pageId>",
 * data-settings-section={pageId}, and aria-current on active.
 * Advanced pages show a subtle badge.
 * Default active: "providers".
 *
 * Deep-link: listens to CustomEvent "synapse:settingsSection" with detail.section = pageId.
 * Arrow keys traverse only the 18 page items (skip group headers).
 *
 * INVARIANT I3: subscribes via typed selectors only.
 * INVARIANT I6: no hardcoded model/provider IDs.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../ui/Button";
import { OpsScheduleCard } from "./OpsScheduleCard";
import { SettingsSaveFooter } from "./SettingsSaveFooter";
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
import { SectionChangelog } from "./sections/SectionChangelog";
import { SectionAbout } from "./sections/SectionAbout";
import { SectionScenarios } from "./sections/SectionScenarios";
import { SectionCosts } from "./sections/SectionCosts";
import { SectionSecurity } from "./sections/SectionSecurity";
import { SectionRuntimeConfig } from "./sections/SectionRuntimeConfig";
import { SectionHeader, GroupDivider } from "./ui";
import {
  SlidersHorizontal,
  Cpu,
  Folder,
  Wrench,
  Book,
  Link2,
  Shield,
  Palette,
  Wand2,
  Zap,
  Network,
  Globe,
  Clock,
  Scissors,
  FileText,
  Image as ImageIcon,
  Lock,
  DollarSign,
  Info,
  History,
} from "lucide-react";

// ─── Page type ────────────────────────────────────────────────────────────────
// 20 stable page IDs — one per leaf page in the 2-level nav.

type SettingsPage =
  // Group: essentials
  | "providers"
  | "appearance"
  | "setup"
  // Group: content
  | "sourceWatch"
  | "clipper"
  | "pdf"
  | "imageCaptioning"
  | "generation"
  | "scenarios"
  // Group: aiBehavior (advanced)
  | "context"
  | "embeddings"
  | "webSearch"
  | "automation"
  | "limits"
  // Group: access
  | "security"
  | "apiMcp"
  // Group: system
  | "costs"
  | "maintenance"
  | "changelog"
  | "about";

// ─── Nav structure ─────────────────────────────────────────────────────────────

interface NavGroup {
  id: string;
  labelKey: string;
  descKey: string;
  pages: NavPage[];
}

interface NavPage {
  id: SettingsPage;
  labelKey: string;
  icon: ReactNode;
  advanced?: boolean;
}

const NAV_GROUPS: NavGroup[] = [
  {
    id: "essentials",
    labelKey: "settings.nav.groupEssentials",
    descKey: "settings.nav.groupEssentialsDesc",
    pages: [
      { id: "providers", labelKey: "settings.nav.providers", icon: <Cpu size={15} aria-hidden="true" /> },
      { id: "appearance", labelKey: "settings.nav.appearance", icon: <Palette size={15} aria-hidden="true" /> },
      { id: "setup", labelKey: "settings.nav.setup", icon: <Wand2 size={15} aria-hidden="true" /> },
    ],
  },
  {
    id: "content",
    labelKey: "settings.nav.groupContent",
    descKey: "settings.nav.groupContentDesc",
    pages: [
      { id: "sourceWatch", labelKey: "settings.nav.sourceWatch2", icon: <Folder size={15} aria-hidden="true" /> },
      { id: "clipper", labelKey: "settings.nav.clipper", icon: <Scissors size={15} aria-hidden="true" /> },
      { id: "pdf", labelKey: "settings.nav.pdf", icon: <FileText size={15} aria-hidden="true" /> },
      { id: "imageCaptioning", labelKey: "settings.nav.imageCaptioning", icon: <ImageIcon size={15} aria-hidden="true" /> },
      { id: "generation", labelKey: "settings.nav.generation", icon: <Book size={15} aria-hidden="true" /> },
      { id: "scenarios", labelKey: "settings.nav.scenarios", icon: <Zap size={15} aria-hidden="true" /> },
    ],
  },
  {
    id: "aiBehavior",
    labelKey: "settings.nav.groupAiBehavior",
    descKey: "settings.nav.groupAiBehaviorDesc",
    pages: [
      { id: "context", labelKey: "settings.nav.context", icon: <SlidersHorizontal size={15} aria-hidden="true" />, advanced: true },
      {
        id: "embeddings",
        labelKey: "settings.nav.embeddings2",
        icon: <Network size={15} aria-hidden="true" />,
        advanced: true,
      },
      { id: "webSearch", labelKey: "settings.nav.webSearch2", icon: <Globe size={15} aria-hidden="true" />, advanced: true },
      {
        id: "automation",
        labelKey: "settings.nav.automation",
        icon: <Clock size={15} aria-hidden="true" />,
        advanced: true,
      },
      { id: "limits", labelKey: "settings.nav.limits", icon: <Shield size={15} aria-hidden="true" />, advanced: true },
    ],
  },
  {
    id: "access",
    labelKey: "settings.nav.groupAccess",
    descKey: "settings.nav.groupAccessDesc",
    pages: [
      { id: "security", labelKey: "settings.nav.security2", icon: <Lock size={15} aria-hidden="true" /> },
      { id: "apiMcp", labelKey: "settings.nav.apiMcp2", icon: <Link2 size={15} aria-hidden="true" /> },
    ],
  },
  {
    id: "system",
    labelKey: "settings.nav.groupSystem",
    descKey: "settings.nav.groupSystemDesc",
    pages: [
      { id: "costs", labelKey: "settings.nav.costs2", icon: <DollarSign size={15} aria-hidden="true" /> },
      { id: "maintenance", labelKey: "settings.nav.maintenance2", icon: <Wrench size={15} aria-hidden="true" /> },
      { id: "changelog", labelKey: "settings.nav.changelog2", icon: <History size={15} aria-hidden="true" /> },
      { id: "about", labelKey: "settings.nav.about2", icon: <Info size={15} aria-hidden="true" /> },
    ],
  },
];

// Flat ordered list of all 18 page IDs (for arrow-key traversal)
const ALL_PAGES: SettingsPage[] = NAV_GROUPS.flatMap((g) => g.pages.map((p) => p.id));

// ─── SettingsPanel ─────────────────────────────────────────────────────────────

export function SettingsPanel() {
  const [activePage, setActivePage] = useState<SettingsPage>("providers");
  const [query, setQuery] = useState("");
  const { t } = useTranslation();
  const pageRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Client-side filter over the ~18 pages by their translated label. With this many
  // settings a "jump to" search is faster than scanning five groups.
  const q = query.trim().toLowerCase();
  const groupsToRender = NAV_GROUPS.map((group) => ({
    group,
    pages: q ? group.pages.filter((p) => t(p.labelKey).toLowerCase().includes(q)) : group.pages,
  })).filter((g) => g.pages.length > 0);

  // Deep-link: listen to synapse:settingsSection CustomEvent (same pattern as synapse:openWizard)
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ section: string }>).detail;
      if (detail?.section && ALL_PAGES.includes(detail.section as SettingsPage)) {
        setActivePage(detail.section as SettingsPage);
      }
    };
    window.addEventListener("synapse:settingsSection", handler);
    return () => {
      window.removeEventListener("synapse:settingsSection", handler);
    };
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
      className="settings-panel"
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
        className="settings-panel__nav"
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
        <p
          style={{
            margin: "0 12px 12px",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            color: "var(--syn-text-dim)",
          }}
        >
          {t("settings.title")}
        </p>

        {/* Quick filter over all settings pages */}
        <div style={{ margin: "0 12px 8px" }}>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("settings.nav.searchPlaceholder")}
            aria-label={t("settings.nav.searchPlaceholder")}
            data-testid="settings-nav-search"
            style={{
              width: "100%",
              boxSizing: "border-box",
              padding: "5px 8px",
              fontSize: 12,
              color: "var(--syn-text)",
              background: "var(--syn-surface)",
              border: "1px solid var(--syn-border)",
              borderRadius: "var(--syn-radius-sm)",
              outline: "none",
            }}
          />
        </div>

        {groupsToRender.length === 0 && (
          <p style={{ margin: "8px 12px", fontSize: 11, color: "var(--syn-text-dim)" }}>
            {t("settings.nav.noResults")}
          </p>
        )}

        {/* Grouped page list — group headers aid scannability (Synapse has more settings
            than LLM Wiki, so the 5 groups let the eye jump to the right area at a glance) */}
        <div style={{ marginTop: 6 }}>
          {groupsToRender.map(({ group, pages }) => {
            if (pages.length === 0) return null;
            return (
              <div key={group.id} style={{ marginBottom: 8 }}>
                <p
                  data-testid={`settings-nav-group-${group.id}`}
                  style={{
                    margin: "12px 12px 4px",
                    fontSize: 10.5,
                    fontWeight: 600,
                    letterSpacing: "0.06em",
                    textTransform: "uppercase",
                    color: "var(--syn-text-dim)",
                  }}
                >
                  {t(group.labelKey)}
                </p>
                {pages.map((page) => {
                  const globalIdx = ALL_PAGES.indexOf(page.id);
                  const isActive = activePage === page.id;
                  return (
                    <button
                      key={page.id}
                      ref={(el) => {
                        pageRefs.current[globalIdx] = el;
                      }}
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
                        padding: "7px 12px",
                        border: "none",
                        background: isActive ? "var(--syn-accent-soft)" : "transparent",
                        color: isActive ? "var(--syn-text)" : "var(--syn-text-dim)",
                        fontSize: 12.5,
                        cursor: "pointer",
                        textAlign: "left",
                        borderRadius: 6,
                        transition: "background 0.1s ease, color 0.1s ease",
                      }}
                      onMouseEnter={(e) => {
                        if (!isActive) {
                          (e.currentTarget as HTMLButtonElement).style.background =
                            "var(--syn-surface-hover)";
                          (e.currentTarget as HTMLButtonElement).style.color =
                            "var(--syn-text-muted)";
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!isActive) {
                          (e.currentTarget as HTMLButtonElement).style.background = "transparent";
                          (e.currentTarget as HTMLButtonElement).style.color =
                            "var(--syn-text-dim)";
                        }
                      }}
                    >
                      <span
                        style={{
                          display: "inline-flex",
                          flexShrink: 0,
                          opacity: isActive ? 1 : 0.6,
                        }}
                        aria-hidden="true"
                      >
                        {page.icon}
                      </span>
                      {t(page.labelKey)}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      </aside>

      {/* ── Content area (flex column: scroll region + sticky Save footer) ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div
          className="settings-panel__content"
          style={{ flex: 1, overflowY: "auto", padding: "32px 40px", maxWidth: 720 }}
        >
          {activePage === "appearance" && <PageAppearance />}
          {activePage === "setup" && <PageSetup />}
          {activePage === "providers" && <SectionLlmModels />}
          {activePage === "scenarios" && <SectionScenarios />}
          {activePage === "context" && <SectionGeneral />}
          {activePage === "embeddings" && <PageEmbeddings />}
          {activePage === "webSearch" && <SectionWebSearch />}
          {activePage === "generation" && <PageGeneration />}
          {activePage === "automation" && <PageAutomation />}
          {activePage === "limits" && <PageLimits />}
          {activePage === "sourceWatch" && <SectionSourceWatch />}
          {activePage === "clipper" && <SectionWebClipper />}
          {activePage === "pdf" && <PagePdf />}
          {activePage === "imageCaptioning" && <PageImageCaptioning />}
          {activePage === "apiMcp" && <SectionApiMcp />}
          {activePage === "security" && <SectionSecurity />}
          {activePage === "costs" && <SectionCosts />}
          {activePage === "maintenance" && <SectionMaintenance />}
          {activePage === "changelog" && <SectionChangelog />}
          {activePage === "about" && <SectionAbout />}
        </div>
        {/* Sticky Save bar — visible only when client-preference drafts are dirty */}
        <SettingsSaveFooter />
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
      <Button variant="accent-ghost" data-testid="wizard-reopen-btn" onClick={handleOpenWizard}>
        {t("config.gettingStarted.wizardReopen")}
      </Button>
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
      <SectionRuntimeConfig
        keys={["overview_language", "wikilink_enrich_enabled", "domain_vocabulary"]}
      />
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
      <SectionRuntimeConfig
        keys={[
          "deep_research_max_iter",
          "deep_research_token_budget",
          "deep_research_max_queries",
          "lint_max_iter",
          "lint_token_budget",
        ]}
      />
    </div>
  );
}

/** pdf: pdf_extractor + marker_* (local) + mineru_* (cloud, opt-in — P3-d) runtime keys */
function PagePdf() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader
        title={t("config.pdfExtractorSection.title")}
        desc={t("config.pdfExtractorSection.desc")}
      />
      <SectionRuntimeConfig
        keys={[
          "pdf_extractor",
          "marker_service_url",
          "marker_timeout_seconds",
          "mineru_api_url",
          "mineru_timeout_seconds",
        ]}
      />
    </div>
  );
}

/** imageCaptioning: vision caption master toggle + per-run cap (v1.5 P3-a, llm_wiki parity) */
function PageImageCaptioning() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader
        title={t("config.imageCaptioningSection.title")}
        desc={t("config.imageCaptioningSection.desc")}
      />
      <SectionRuntimeConfig keys={["vision_captions_enabled", "vision_max_images_per_run"]} />
    </div>
  );
}
