/**
 * SettingsPanel.tsx — settings surface with left-nav layout (ADR-0018 §5).
 *
 * Sections:
 *   General      — context window + budget split (F14)
 *   LLM Models   — provider CRUD (F17)
 *   Embeddings   — vector embeddings placeholder (M5)
 *   Source Watch — scheduled folder import (ADR-0020)
 *   API + MCP    — HTTP API + MCP server placeholder (M5)
 *   Output       — language + conversation history (F16)
 *   Interface    — display preferences placeholder (M5)
 *   Maintenance  — duplicate detection + reset
 *   About        — version + links
 *
 * INVARIANT I3: subscribes via typed selectors only.
 * INVARIANT I6: no hardcoded model/provider IDs.
 */

import React, { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ImportScheduleCard } from "./ImportScheduleCard";
import { useTranslation } from "react-i18next";
import { getAppConfig, putAppConfig, resetAppConfig, type AppConfigEntry, type AppConfigKey } from "../../api/appConfigClient";
import { useShallow } from "zustand/react/shallow";
import { getAuthToken, setAuthToken, clearAuthToken, apiBase } from "../../api/base";
import {
  useSettingsStore,
  selectContextWindow,
  selectLanguage,
  selectConversationHistoryLength,
  selectSetContextWindow,
  selectSetLanguage,
  selectSetConversationHistoryLength,
  selectResetSettings,
  selectTheme,
  selectSetTheme,
  CONTEXT_WINDOW_OPTIONS,
  CONV_HISTORY_OPTIONS,
  type ConvHistoryLength,
  type Theme,
  computeBudgetSplit,
  formatTokenCount,
} from "../../store/settingsStore";
import {
  useProviderStore,
  selectProviderList,
  selectProviderLoading,
  selectProviderError,
  selectFetchProviderList,
  selectAddProvider,
  selectDeleteProvider,
} from "../../store/providerStore";
import { useGraphStore, selectVaultId } from "../../store/graphStore";
import type { CreateProviderConfigBody } from "../../api/types";
import {
  fetchEmbeddingConfig,
  fetchMcpInfo,
  setRemoteMcpEnabled,
  setMcpAuth,
  fetchClipConfig,
  setClipConfig,
  fetchWebSearchConfig,
  setWebSearchConfig,
  getCliAuthConfig,
  setCliAuthConfig,
  type EmbeddingConfig,
  type McpInfoResponse,
  type McpRemoteStateResponse,
  type McpAuthResponse,
} from "../../api/providerClient";
import type { ClipConfigResponse, ClipConfigStateResponse, WebSearchConfigResponse, CliAuthConfig } from "../../api/types";
import { fetchScenarios, applyScenario, type ScenarioItem } from "../../api/scenariosClient";
import { fetchCostsSummary, type CostsSummary } from "../../api/costsClient";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { showToast } from "../common/Toast";

// ─── Settings section type ────────────────────────────────────────────────────
// A2.1: 5 plain-language groups replace the original 14 flat sections.

type SettingsSection =
  | "gettingStarted"
  | "aiModels"
  | "sources"
  | "output"
  | "advanced";

// ─── Left nav item ────────────────────────────────────────────────────────────

interface NavItem {
  id: SettingsSection;
  labelKey: string;
  icon: ReactNode;
}

function IconSliders() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="4" x2="4" y1="21" y2="14"/><line x1="4" x2="4" y1="6" y2="3"/>
      <line x1="12" x2="12" y1="21" y2="12"/><line x1="12" x2="12" y1="4" y2="3"/>
      <line x1="20" x2="20" y1="21" y2="16"/><line x1="20" x2="20" y1="8" y2="3"/>
      <line x1="1" x2="7" y1="14" y2="14"/><line x1="9" x2="15" y1="12" y2="12"/>
      <line x1="17" x2="23" y1="16" y2="16"/>
    </svg>
  );
}

function IconCpu() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect width="16" height="16" x="4" y="4" rx="2"/>
      <rect width="6" height="6" x="9" y="9" rx="1"/>
      <path d="M15 2v2M15 20v2M9 2v2M9 20v2M2 15h2M2 9h2M20 15h2M20 9h2"/>
    </svg>
  );
}

function IconFolder() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"/>
    </svg>
  );
}

function IconType() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="4 7 4 4 20 4 20 7"/>
      <line x1="9" x2="15" y1="20" y2="20"/>
      <line x1="12" x2="12" y1="4" y2="20"/>
    </svg>
  );
}

function IconWrench() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
    </svg>
  );
}

// A2.1: 5 top-level groups. All original section content is preserved inside each group.
const NAV_ITEMS: NavItem[] = [
  { id: "gettingStarted", labelKey: "settings.nav.groupGettingStarted", icon: <IconSliders /> },
  { id: "aiModels",       labelKey: "settings.nav.groupAiModels",       icon: <IconCpu /> },
  { id: "sources",        labelKey: "settings.nav.groupSources",        icon: <IconFolder /> },
  { id: "output",         labelKey: "settings.nav.groupOutput",         icon: <IconType /> },
  { id: "advanced",       labelKey: "settings.nav.groupAdvanced",       icon: <IconWrench /> },
];

// ─── Component ────────────────────────────────────────────────────────────────

export function SettingsPanel() {
  const [activeSection, setActiveSection] = useState<SettingsSection>("gettingStarted");
  const { t } = useTranslation();
  const navBtnRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Arrow-key navigation for the settings left sub-nav (AC-HARD-SET-5 / DEFECT-M4H-005).
  // Mirrors the NavRail.handleKeyDown pattern (ADR-0021).
  const handleNavKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      const currentIdx = NAV_ITEMS.findIndex((item) => item.id === activeSection);
      let nextIdx = currentIdx;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        nextIdx = (currentIdx + 1) % NAV_ITEMS.length;
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        nextIdx = (currentIdx - 1 + NAV_ITEMS.length) % NAV_ITEMS.length;
      } else if (e.key === "Home") {
        e.preventDefault();
        nextIdx = 0;
      } else if (e.key === "End") {
        e.preventDefault();
        nextIdx = NAV_ITEMS.length - 1;
      } else {
        return;
      }

      const nextItem = NAV_ITEMS[nextIdx];
      if (nextItem) {
        setActiveSection(nextItem.id);
        navBtnRefs.current[nextIdx]?.focus();
      }
    },
    [activeSection],
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
      {/* Left nav */}
      <aside
        role="navigation"
        aria-label={t("settings.title")}
        onKeyDown={handleNavKeyDown}
        style={{
          width: 180,
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
        {NAV_ITEMS.map((item, idx) => (
          <button
            key={item.id}
            ref={(el) => { navBtnRefs.current[idx] = el; }}
            data-settings-section={item.id}
            data-testid={`settings-nav-${item.id}`}
            aria-current={activeSection === item.id ? "true" : undefined}
            tabIndex={activeSection === item.id ? 0 : -1}
            onClick={() => setActiveSection(item.id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              width: "100%",
              padding: "7px 12px",
              border: "none",
              background: activeSection === item.id ? "var(--syn-accent-soft)" : "transparent",
              color: activeSection === item.id ? "var(--syn-text)" : "var(--syn-text-dim)",
              fontSize: 12,
              cursor: "pointer",
              textAlign: "left",
              borderRadius: 0,
              borderLeft: activeSection === item.id ? "2px solid var(--syn-accent)" : "2px solid transparent",
              transition: "background 0.1s ease, color 0.1s ease",
            }}
            onMouseEnter={(e) => {
              if (activeSection !== item.id) {
                (e.currentTarget as HTMLButtonElement).style.background = "var(--syn-surface-hover)";
                (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-muted)";
              }
            }}
            onMouseLeave={(e) => {
              if (activeSection !== item.id) {
                (e.currentTarget as HTMLButtonElement).style.background = "transparent";
                (e.currentTarget as HTMLButtonElement).style.color = "var(--syn-text-dim)";
              }
            }}
          >
            <span style={{ opacity: activeSection === item.id ? 1 : 0.6 }}>{item.icon}</span>
            {t(item.labelKey)}
          </button>
        ))}
      </aside>

      {/* Content area — A2.1: 5-group IA */}
      <div style={{ flex: 1, overflowY: "auto", padding: "32px 40px", maxWidth: 720 }}>
        {activeSection === "gettingStarted" && <GroupGettingStarted />}
        {activeSection === "aiModels"       && <GroupAiModels />}
        {activeSection === "sources"        && <GroupSources />}
        {activeSection === "output"         && <GroupOutput />}
        {activeSection === "advanced"       && <GroupAdvanced />}
      </div>
    </div>
  );
}

// ─── A2.1 Group wrappers ──────────────────────────────────────────────────────
// Each group renders its constituent sections with a shared group heading.
// All existing section components (SectionGeneral, SectionLlmModels, …) are
// unchanged — they are composed here, not modified.

function GroupDivider() {
  return (
    <div style={{ borderTop: "1px solid var(--syn-border)", margin: "32px 0" }} />
  );
}

/** Group 1: Getting started — context window + wizard re-open button (A2.2). */
function GroupGettingStarted() {
  const { t } = useTranslation();

  /** Fire the custom event that AppShell listens to (avoids prop-drilling). */
  function handleOpenWizard() {
    window.dispatchEvent(new Event("synapse:openWizard"));
  }

  return (
    <div>
      <GroupHeader title={t("settings.nav.groupGettingStarted")} />
      <SectionGeneral />
      <GroupDivider />
      {/* A2.2: wizard re-open slot — previously a "coming soon" placeholder. */}
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
    </div>
  );
}

/** Group 2: AI & Models — providers, embeddings, web search, API+MCP. */
function GroupAiModels() {
  const { t } = useTranslation();
  return (
    <div>
      <GroupHeader title={t("settings.nav.groupAiModels")} />
      <SectionLlmModels />
      <GroupDivider />
      <SectionEmbeddings />
      <GroupDivider />
      <SectionWebSearch />
      <GroupDivider />
      <SectionApiMcp />
    </div>
  );
}

/** Group 3: Sources & PDF — source watch, web clipper, PDF extractor runtime config. */
function GroupSources() {
  const { t } = useTranslation();
  return (
    <div>
      <GroupHeader title={t("settings.nav.groupSources")} />
      <SectionSourceWatch />
      <GroupDivider />
      <SectionWebClipper />
      <GroupDivider />
      {/* PDF extraction runtime overrides (S1, S2, S3) — R11-2 migrated settings */}
      <SectionHeader
        title={t("config.pdfExtractorSection.title")}
        desc={t("config.pdfExtractorSection.desc")}
      />
      <SectionRuntimeConfig keys={["pdf_extractor", "marker_service_url", "marker_timeout_seconds"]} />
    </div>
  );
}

/** Group 4: Output & Appearance — output, interface, scenarios. */
function GroupOutput() {
  const { t } = useTranslation();
  return (
    <div>
      <GroupHeader title={t("settings.nav.groupOutput")} />
      <SectionOutput />
      <GroupDivider />
      <SectionInterface />
      <GroupDivider />
      <SectionScenarios />
    </div>
  );
}

/** Group 5: Advanced — costs, security, maintenance, about + runtime overrides (S4-S8). */
function GroupAdvanced() {
  const { t } = useTranslation();
  return (
    <div>
      <GroupHeader title={t("settings.nav.groupAdvanced")} />
      <SectionCosts />
      <GroupDivider />
      <SectionSecurity />
      <GroupDivider />
      <SectionMaintenance />
      <GroupDivider />
      {/* Runtime overrides (S4-S8): cost alert, embeddings, format, language, wikilinks */}
      <SectionHeader
        title={t("config.runtimeOverridesSection.title")}
        desc={t("config.runtimeOverridesSection.desc")}
      />
      <SectionRuntimeConfig
        keys={[
          "cost_alert_threshold_usd",
          "embeddings_enabled",
          "embedding_format",
          "overview_language",
          "wikilink_enrich_enabled",
          "domain_vocabulary",
        ]}
      />
      <GroupDivider />
      <SectionAbout />
    </div>
  );
}

function GroupHeader({ title }: { title: string }) {
  return (
    <h2 style={{ fontSize: 15, fontWeight: 700, margin: "0 0 24px", color: "var(--syn-text)" }}>
      {title}
    </h2>
  );
}

// ─── SectionRuntimeConfig — R11-2 migrated settings (ADR-0053) ───────────────
// Renders a subset of the 8 runtime config keys, determined by the `keys` prop.
// Each field: effective value + source badge (Default / Custom) + Save + Reset.
// PUT /config/app/{key} on save; DELETE /config/app/{key} on reset (ADR-0053 §3.3).
// I3: local state only, no Zustand store. I6: sends strings, no embedding logic.
// AC-R11-2-12: primary labels are plain language (never equal to env-var names).

type RcEntry = AppConfigEntry & { localValue: string; saving: boolean; saved: boolean };

const EMPTY_ENTRY: Omit<RcEntry, "key"> = {
  value: "",
  source: "env",
  localValue: "",
  saving: false,
  saved: false,
};

function SectionRuntimeConfig({ keys }: { keys: AppConfigKey[] }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<Map<AppConfigKey, RcEntry>>(new Map());
  const [loading, setLoading] = useState(true);
  const [fetchErr, setFetchErr] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setFetchErr(null);
    getAppConfig(ac.signal)
      .then((resp) => {
        const map = new Map<AppConfigKey, RcEntry>();
        for (const key of keys) {
          const found = resp.settings.find((s) => s.key === key);
          map.set(key, {
            key,
            value: found?.value ?? "",
            source: found?.source ?? "env",
            localValue: found?.value ?? "",
            saving: false,
            saved: false,
          });
        }
        setEntries(map);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "AbortError") return;
        setFetchErr(t("config.error"));
        setLoading(false);
      });
    return () => { ac.abort(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys.join(",")]);

  const setLocal = (key: AppConfigKey, localValue: string) => {
    setEntries((prev) => {
      const next = new Map(prev);
      const entry = next.get(key) ?? { ...EMPTY_ENTRY, key };
      next.set(key, { ...entry, localValue });
      return next;
    });
  };

  const handleSave = async (key: AppConfigKey) => {
    const entry = entries.get(key);
    if (!entry) return;
    setEntries((prev) => {
      const next = new Map(prev);
      next.set(key, { ...entry, saving: true, saved: false });
      return next;
    });
    try {
      await putAppConfig(key, entry.localValue);
      setEntries((prev) => {
        const next = new Map(prev);
        const e = next.get(key);
        if (e) next.set(key, { ...e, value: entry.localValue, source: "override", saving: false, saved: true });
        return next;
      });
      setTimeout(() => {
        setEntries((prev) => {
          const next = new Map(prev);
          const e = next.get(key);
          if (e) next.set(key, { ...e, saved: false });
          return next;
        });
      }, 2500);
    } catch {
      setEntries((prev) => {
        const next = new Map(prev);
        const e = next.get(key);
        if (e) next.set(key, { ...e, saving: false });
        return next;
      });
    }
  };

  const handleReset = async (key: AppConfigKey) => {
    const entry = entries.get(key);
    if (!entry) return;
    setEntries((prev) => {
      const next = new Map(prev);
      next.set(key, { ...entry, saving: true, saved: false });
      return next;
    });
    try {
      await resetAppConfig(key);
      // Refetch to get the env-default value after reset
      const resp = await getAppConfig();
      const found = resp.settings.find((s) => s.key === key);
      setEntries((prev) => {
        const next = new Map(prev);
        next.set(key, {
          key,
          value: found?.value ?? "",
          source: found?.source ?? "env",
          localValue: found?.value ?? "",
          saving: false,
          saved: true,
        });
        return next;
      });
      setTimeout(() => {
        setEntries((prev) => {
          const next = new Map(prev);
          const e = next.get(key);
          if (e) next.set(key, { ...e, saved: false });
          return next;
        });
      }, 2500);
    } catch {
      setEntries((prev) => {
        const next = new Map(prev);
        const e = next.get(key);
        if (e) next.set(key, { ...e, saving: false });
        return next;
      });
    }
  };

  if (loading) {
    return <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("config.loading")}</p>;
  }
  if (fetchErr) {
    return <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{fetchErr}</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24, marginTop: 8 }}>
      {keys.map((key) => {
        const entry = entries.get(key) ?? { ...EMPTY_ENTRY, key };
        return <RuntimeConfigField key={key} configKey={key} entry={entry} onLocalChange={setLocal} onSave={handleSave} onReset={handleReset} />;
      })}
    </div>
  );
}

// ─── RuntimeConfigField — one row for each of the 8 keys ─────────────────────
// Renders label (plain language, AC-R11-2-12) + help text + control + source badge + actions.

function RuntimeConfigField({
  configKey,
  entry,
  onLocalChange,
  onSave,
  onReset,
}: {
  configKey: AppConfigKey;
  entry: RcEntry;
  onLocalChange: (key: AppConfigKey, value: string) => void;
  onSave: (key: AppConfigKey) => Promise<void>;
  onReset: (key: AppConfigKey) => Promise<void>;
}) {
  const { t } = useTranslation();

  const i18nBase = `config.${configKeyToI18nSuffix(configKey)}`;
  const label   = t(`${i18nBase}.label`);
  const help    = t(`${i18nBase}.help`);
  const isOverride = entry.source === "override";

  return (
    <div data-testid={`rc-field-${configKey}`}>
      {/* Label row */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <label style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)" }}>
          {label}
        </label>
        <span
          data-testid={`rc-source-badge-${configKey}`}
          style={{
            padding: "1px 7px",
            borderRadius: 4,
            fontSize: 10,
            fontWeight: 600,
            background: isOverride
              ? "color-mix(in srgb, var(--syn-accent) 12%, var(--syn-mix-base) 88%)"
              : "var(--syn-surface-hover)",
            color: isOverride ? "var(--syn-accent)" : "var(--syn-text-dim)",
            border: isOverride
              ? "1px solid color-mix(in srgb, var(--syn-accent) 30%, transparent 70%)"
              : "1px solid var(--syn-border)",
          }}
        >
          {isOverride ? t("config.sourceBadge.override") : t("config.sourceBadge.env")}
        </span>
      </div>

      {/* Help text */}
      <p style={{ fontSize: 11, color: "var(--syn-text-dim)", margin: "0 0 6px", lineHeight: 1.5 }}>{help}</p>

      {/* Control */}
      <RcControl configKey={configKey} entry={entry} onLocalChange={onLocalChange} />

      {/* Hint: underlying env-var key name (secondary, smaller — AC-R11-2-12 compliant) */}
      <p style={{ fontSize: 10, color: "var(--syn-text-dim)", margin: "4px 0 0", fontFamily: "monospace" }}>
        {t("config.keyHint", { key: configKey.toUpperCase() })}
      </p>

      {/* Action row */}
      <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
        <button
          data-testid={`rc-save-${configKey}`}
          onClick={() => { void onSave(configKey); }}
          disabled={entry.saving}
          style={{ ...BTN_PRIMARY, opacity: entry.saving ? 0.4 : 1, cursor: entry.saving ? "not-allowed" : "pointer" }}
        >
          {entry.saving ? t("config.saving") : t("config.save")}
        </button>
        {isOverride && (
          <button
            data-testid={`rc-reset-${configKey}`}
            onClick={() => { void onReset(configKey); }}
            disabled={entry.saving}
            style={{ ...BTN_SECONDARY, opacity: entry.saving ? 0.4 : 1, cursor: entry.saving ? "not-allowed" : "pointer" }}
          >
            {t("config.resetToDefault")}
          </button>
        )}
        {entry.saved && (
          <span style={{ fontSize: 11, color: "var(--syn-green)" }}>
            {isOverride ? t("config.saved") : t("config.resetDone")}
          </span>
        )}
      </div>
    </div>
  );
}

/** Map a config key to its i18n sub-key (camelCase). */
function configKeyToI18nSuffix(key: AppConfigKey): string {
  const map: Record<AppConfigKey, string> = {
    pdf_extractor:              "pdfExtractor",
    marker_service_url:         "markerServiceUrl",
    marker_timeout_seconds:     "markerTimeoutSeconds",
    cost_alert_threshold_usd:   "costAlertThresholdUsd",
    embeddings_enabled:         "embeddingsEnabled",
    embedding_format:           "embeddingFormat",
    overview_language:          "overviewLanguage",
    wikilink_enrich_enabled:    "wikilinkEnrichEnabled",
    // S9 — ADR-0054 §2.1, F18
    domain_vocabulary:          "domainVocabulary",
  };
  return map[key];
}

/** Per-key control: select for enum keys, text input for free-form, toggle for booleans. */
function RcControl({
  configKey,
  entry,
  onLocalChange,
}: {
  configKey: AppConfigKey;
  entry: RcEntry;
  onLocalChange: (key: AppConfigKey, value: string) => void;
}) {
  const { t } = useTranslation();

  if (configKey === "pdf_extractor") {
    return (
      <select
        data-testid="rc-control-pdf_extractor"
        value={entry.localValue}
        onChange={(e) => onLocalChange(configKey, e.target.value)}
        style={INPUT_STYLE}
      >
        <option value="pypdf">{t("config.pdfExtractor.optionPypdf")}</option>
        <option value="marker">{t("config.pdfExtractor.optionMarker")}</option>
      </select>
    );
  }

  if (configKey === "embedding_format") {
    return (
      <select
        data-testid="rc-control-embedding_format"
        value={entry.localValue}
        onChange={(e) => onLocalChange(configKey, e.target.value)}
        style={INPUT_STYLE}
      >
        <option value="ollama">{t("config.embeddingFormat.optionOllama")}</option>
        <option value="openai">{t("config.embeddingFormat.optionOpenai")}</option>
      </select>
    );
  }

  if (configKey === "embeddings_enabled" || configKey === "wikilink_enrich_enabled") {
    const i18nBase = `config.${configKeyToI18nSuffix(configKey)}`;
    const isOn = entry.localValue === "true" || entry.localValue === "1";
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button
          data-testid={`rc-control-${configKey}`}
          role="switch"
          aria-checked={isOn}
          onClick={() => onLocalChange(configKey, isOn ? "false" : "true")}
          style={{
            width: 40,
            height: 22,
            borderRadius: 11,
            border: "none",
            cursor: "pointer",
            position: "relative",
            background: isOn ? "var(--syn-accent)" : "var(--syn-border)",
            transition: "background 0.15s",
            flexShrink: 0,
            padding: 0,
          }}
        >
          <span
            style={{
              position: "absolute",
              top: 3,
              left: isOn ? 21 : 3,
              width: 16,
              height: 16,
              borderRadius: "50%",
              background: "white",
              transition: "left 0.15s",
            }}
          />
        </button>
        <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
          {isOn ? t(`${i18nBase}.on`) : t(`${i18nBase}.off`)}
        </span>
      </div>
    );
  }

  if (configKey === "overview_language") {
    return (
      <input
        type="text"
        data-testid="rc-control-overview_language"
        value={entry.localValue}
        onChange={(e) => onLocalChange(configKey, e.target.value)}
        placeholder={t("config.overviewLanguage.placeholder")}
        style={INPUT_STYLE}
      />
    );
  }

  if (configKey === "marker_service_url") {
    return (
      <input
        type="text"
        data-testid="rc-control-marker_service_url"
        value={entry.localValue}
        onChange={(e) => onLocalChange(configKey, e.target.value)}
        placeholder={t("config.markerServiceUrl.placeholder")}
        style={INPUT_STYLE}
      />
    );
  }

  // S9: domain_vocabulary — tag-chip textarea (JSON array wire format, ADR-0054 §2.1).
  // The UI shows a plain comma-separated text input; the user types domain names
  // WITHOUT the "domain/" prefix (that prefix is an implementation detail hidden here).
  // On save the parent serialises the current localValue as a JSON array string.
  // We store comma-separated text in localValue; the parent's onSave sends it as-is
  // (the backend validates & normalises the JSON array format).
  if (configKey === "domain_vocabulary") {
    // Parse the stored JSON array into a comma-separated display value.
    // localValue may be "" (not yet set), a JSON array string, or comma-separated text
    // (after first edit). We normalise the display on first render only.
    let displayValue = entry.localValue;
    if (displayValue.trim().startsWith("[")) {
      try {
        const parsed = JSON.parse(displayValue) as string[];
        displayValue = parsed.join(", ");
      } catch {
        // leave as-is if malformed
      }
    }
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <input
          type="text"
          data-testid="rc-control-domain_vocabulary"
          value={displayValue}
          onChange={(e) => {
            // Serialise the comma-separated user input as a JSON array string so the
            // backend receives the canonical wire format (ADR-0054 §2.1).
            const raw = e.target.value;
            const names = raw.split(",").map((s) => s.trim()).filter(Boolean);
            const jsonValue = JSON.stringify(names);
            onLocalChange(configKey, jsonValue);
          }}
          placeholder={t("config.domainVocabulary.placeholder")}
          style={INPUT_STYLE}
        />
        <p style={{ margin: 0, fontSize: 10, color: "var(--syn-text-dim)", lineHeight: 1.4 }}>
          {t("config.domainVocabulary.chipHint")}
        </p>
      </div>
    );
  }

  // marker_timeout_seconds, cost_alert_threshold_usd — numeric text inputs
  return (
    <input
      type="text"
      inputMode="decimal"
      data-testid={`rc-control-${configKey}`}
      value={entry.localValue}
      onChange={(e) => onLocalChange(configKey, e.target.value)}
      placeholder={
        configKey === "marker_timeout_seconds"
          ? t("config.markerTimeoutSeconds.placeholder")
          : t("config.costAlertThresholdUsd.placeholder")
      }
      style={INPUT_STYLE}
    />
  );
}

// ─── Section: General ─────────────────────────────────────────────────────────

function SectionGeneral() {
  const { t } = useTranslation();
  const contextWindow = useSettingsStore(selectContextWindow);
  const setContextWindow = useSettingsStore(selectSetContextWindow);
  const budget = computeBudgetSplit(contextWindow);

  return (
    <div>
      <SectionHeader title={t("settings.nav.general")} desc={t("settings.contextWindowHelp")} />

      <Field label={t("settings.contextWindow")}>
        <select
          id="ctx-select"
          value={contextWindow}
          onChange={(e) => setContextWindow(Number(e.target.value) as typeof contextWindow)}
          style={INPUT_STYLE}
        >
          {CONTEXT_WINDOW_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>{formatTokenCount(opt)}</option>
          ))}
        </select>
      </Field>

      <div style={{ marginTop: 24 }}>
        <p style={{ margin: "0 0 10px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)" }}>
          {t("settings.budgetSplit")}
        </p>
        <BudgetRow label={t("settings.budgetHistory")}    pct={60} tokens={budget.history} />
        <BudgetRow label={t("settings.budgetRetrieved")}  pct={20} tokens={budget.retrieved} />
        <BudgetRow label={t("settings.budgetSystem")}     pct={5}  tokens={budget.system} />
        <BudgetRow label={t("settings.budgetGeneration")} pct={15} tokens={budget.generation} />
      </div>
    </div>
  );
}

// ─── Section: LLM Models ─────────────────────────────────────────────────────

function SectionLlmModels() {
  const { t } = useTranslation();
  const providerList = useProviderStore(useShallow(selectProviderList));
  const providerLoading = useProviderStore(selectProviderLoading);
  const providerError = useProviderStore(selectProviderError);
  const fetchProviders = useProviderStore(selectFetchProviderList);
  const addProvider = useProviderStore(selectAddProvider);
  const deleteProvider = useProviderStore(selectDeleteProvider);
  const vaultId = useGraphStore(selectVaultId);

  const [showForm, setShowForm] = useState(false);
  const [formType, setFormType] = useState<"local" | "api" | "cli">("api");
  const [formModelId, setFormModelId] = useState("");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [formScope, setFormScope] = useState<"global" | "vault">("global");
  const [formLoading, setFormLoading] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    if (providerList.length === 0 && !providerLoading) {
      void fetchProviders();
    }
  }, [providerList.length, providerLoading, fetchProviders]);

  const handleAdd = async () => {
    setFormLoading(true);
    const body: CreateProviderConfigBody = {
      scope: formScope,
      vault_id: formScope === "vault" ? vaultId : null,
      provider_type: formType,
      model_id: formModelId.trim() || null,
      base_url: formBaseUrl.trim() || null,
    };
    await addProvider(body, vaultId ?? "");
    setFormLoading(false);
    setShowForm(false);
    setFormModelId("");
    setFormBaseUrl("");
    setSuccessMsg(t("settings.llmModels.added"));
    setTimeout(() => setSuccessMsg(null), 2500);
  };

  const handleDelete = async (id: string) => {
    // Non-blocking warning when deleting the last remaining provider (AC-HARD-PROV-6).
    const isLast = providerList.length === 1;
    const confirmMsg = isLast
      ? `${t("settings.llmModels.lastProviderWarning")}\n\n${t("settings.llmModels.confirmDelete")}`
      : t("settings.llmModels.confirmDelete");
    if (!window.confirm(confirmMsg)) return;
    await deleteProvider(id, vaultId ?? "");
    setSuccessMsg(t("settings.llmModels.deleted"));
    setTimeout(() => setSuccessMsg(null), 2500);
  };

  return (
    <div>
      <SectionHeader title={t("settings.nav.llmModels")} desc={t("settings.llmModels.desc")} />

      {successMsg && (
        <div style={{ marginBottom: 12, padding: "6px 12px", background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)", border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)", borderRadius: 6, fontSize: 12, color: "var(--syn-green)" }}>
          {successMsg}
        </div>
      )}
      {providerError && (
        <div style={{ marginBottom: 12, padding: "6px 12px", background: "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)", border: "1px solid color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)", borderRadius: 6, fontSize: 12, color: "var(--syn-red)" }}>
          {providerError}
        </div>
      )}

      {providerLoading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("common.loading")}</p>
      )}

      {!providerLoading && providerList.length === 0 && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("provider.noProviders")}</p>
      )}

      {/* Provider list */}
      <div style={{ marginBottom: 16 }}>
        {providerList.map((item) => (
          <div
            key={item.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 12px",
              border: "1px solid var(--syn-border)",
              borderRadius: 6,
              marginBottom: 6,
              background: "var(--syn-surface)",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text)" }}>
                  {t(`provider.type.${item.provider_type}` as string) || item.provider_type}
                </span>
                <span style={{ padding: "1px 6px", borderRadius: 4, background: "var(--syn-surface-hover)", color: "var(--syn-text-muted)", fontSize: 10 }}>
                  {t(`provider.scope.${item.scope}`)}
                </span>
                {item.is_fallback && (
                  <span style={{ padding: "1px 6px", borderRadius: 4, background: "var(--syn-surface-hover)", color: "var(--syn-text-dim)", fontSize: 10 }}>
                    {t("settings.llmModels.fallback")}
                  </span>
                )}
              </div>
              {item.model_id && (
                <p style={{ margin: "3px 0 0", fontSize: 11, color: "var(--syn-text-muted)", fontFamily: "monospace" }}>
                  {item.model_id}
                </p>
              )}
              {item.base_url && (
                <p style={{ margin: "2px 0 0", fontSize: 10, color: "var(--syn-text-dim)", fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {item.base_url}
                </p>
              )}
            </div>
            <button
              onClick={() => void handleDelete(item.id)}
              title={t("settings.llmModels.delete")}
              style={{
                padding: "4px 8px",
                border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                borderRadius: 4,
                background: "transparent",
                color: "var(--syn-red)",
                fontSize: 11,
                cursor: "pointer",
                flexShrink: 0,
              }}
            >
              {t("settings.llmModels.delete")}
            </button>
          </div>
        ))}
      </div>

      {/* Add form */}
      {showForm ? (
        <div style={{ padding: 16, border: "1px solid var(--syn-border)", borderRadius: 8, background: "var(--syn-bg-soft)", marginBottom: 16 }}>
          <p style={{ margin: "0 0 12px", fontSize: 12, fontWeight: 600, color: "var(--syn-text)" }}>
            {t("settings.llmModels.addProvider")}
          </p>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
            <Field label={t("settings.llmModels.providerType")} compact>
              <select value={formType} onChange={(e) => setFormType(e.target.value as typeof formType)} style={INPUT_STYLE}>
                <option value="api">API (Anthropic / OpenAI-compat)</option>
                <option value="local">Local (Ollama)</option>
                <option value="cli">CLI (claude-agent-sdk)</option>
              </select>
            </Field>
            <Field label={t("settings.llmModels.scope")} compact>
              <select value={formScope} onChange={(e) => setFormScope(e.target.value as typeof formScope)} style={INPUT_STYLE}>
                <option value="global">Global</option>
                <option value="vault">Vault</option>
              </select>
            </Field>
          </div>

          <Field label={t("settings.llmModels.modelId")} compact>
            <input
              type="text"
              value={formModelId}
              onChange={(e) => setFormModelId(e.target.value)}
              placeholder={
                formType === "local"
                  ? t("settings.llmModels.modelIdPlaceholderLocal")
                  : formType === "cli"
                  ? t("settings.llmModels.modelIdPlaceholderCli")
                  : t("settings.llmModels.modelIdPlaceholder")
              }
              style={INPUT_STYLE}
            />
          </Field>

          {(formType === "api" || formType === "local") && (
            <Field label={t("settings.llmModels.baseUrl")} compact>
              <input
                type="text"
                value={formBaseUrl}
                onChange={(e) => setFormBaseUrl(e.target.value)}
                placeholder={
                  formType === "local"
                    ? t("settings.llmModels.baseUrlPlaceholderLocal")
                    : t("settings.llmModels.baseUrlPlaceholder")
                }
                style={INPUT_STYLE}
              />
            </Field>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button
              onClick={() => void handleAdd()}
              disabled={formLoading || formModelId.trim() === ""}
              title={formModelId.trim() === "" ? t("settings.llmModels.modelIdRequired") : undefined}
              style={{
                ...BTN_PRIMARY,
                opacity: formLoading || formModelId.trim() === "" ? 0.4 : 1,
                cursor: formLoading || formModelId.trim() === "" ? "not-allowed" : "pointer",
              }}
            >
              {formLoading ? "…" : t("settings.llmModels.add")}
            </button>
            <button onClick={() => setShowForm(false)} style={BTN_SECONDARY}>
              {t("settings.llmModels.cancel")}
            </button>
          </div>
        </div>
      ) : (
        <button onClick={() => setShowForm(true)} style={BTN_PRIMARY}>
          + {t("settings.llmModels.addProvider")}
        </button>
      )}
    </div>
  );
}

// ─── Section: Embeddings ──────────────────────────────────────────────────────
// ADR-0030: embeddings_enabled is a read-only ENV flag — NOT an interactive toggle.
// When off, semantic (Qdrant) search degrades to lexical-only (Postgres keyword).

function SectionEmbeddings() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<EmbeddingConfig | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    fetchEmbeddingConfig(ac.signal)
      .then((data) => { setCfg(data); setErr(false); })
      .catch((e: unknown) => { if (!(e instanceof Error) || e.name !== "AbortError") setErr(true); });
    return () => { ac.abort(); };
  }, []);

  return (
    <div>
      <SectionHeader title={t("settings.nav.embeddings")} desc={t("settings.embeddings.desc")} />
      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{t("settings.embeddings.error")}</p>
      ) : cfg === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("settings.embeddings.loading")}</p>
      ) : cfg.embeddings_enabled ? (
        /* ── ENABLED: semantic search active ── */
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div
            data-testid="embeddings-status-active"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 10px",
              background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)",
              border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
              borderRadius: 6,
              fontSize: 12,
              color: "var(--syn-green)",
              fontWeight: 600,
            }}
          >
            <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--syn-green)", flexShrink: 0, display: "inline-block" }} />
            {t("settings.embeddings.semanticActive")}
          </div>
          <EmbedRow label={t("settings.embeddings.urlLabel")} value={cfg.embedding_url} mono />
          <EmbedRow label={t("settings.embeddings.modelLabel")} value={cfg.embedding_model} mono />
          <EmbedRow label={t("settings.embeddings.dimLabel")} value={String(cfg.embedding_dim)} />
          <p style={{ fontSize: 11, color: "var(--syn-text-dim)", margin: "4px 0 0", lineHeight: 1.5 }}>
            {t("settings.embeddings.envNote")}
          </p>
        </div>
      ) : (
        /* ── DISABLED: lexical-only degrade (ADR-0030 §2.3) ── */
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div
            data-testid="embeddings-status-lexical"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 10px",
              background: "color-mix(in srgb, var(--syn-amber) 8%, var(--syn-mix-base) 92%)",
              border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, var(--syn-mix-base) 70%)",
              borderRadius: 6,
              fontSize: 12,
              color: "var(--syn-amber)",
              fontWeight: 600,
            }}
          >
            <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--syn-amber)", flexShrink: 0, display: "inline-block" }} />
            {t("settings.embeddings.lexicalOnly")}
          </div>
          <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: 0, lineHeight: 1.6 }}>
            {t("settings.embeddings.lexicalOnlyNote")}
          </p>
          {/* URL / model / dim shown dimmed — informational only in lexical mode */}
          <div style={{ opacity: 0.45 }}>
            <EmbedRow label={t("settings.embeddings.urlLabel")} value={cfg.embedding_url} mono />
            <div style={{ marginTop: 10 }}>
              <EmbedRow label={t("settings.embeddings.modelLabel")} value={cfg.embedding_model} mono />
            </div>
            <div style={{ marginTop: 10 }}>
              <EmbedRow label={t("settings.embeddings.dimLabel")} value={String(cfg.embedding_dim)} />
            </div>
          </div>
          <p style={{ fontSize: 11, color: "var(--syn-text-dim)", margin: 0, lineHeight: 1.5 }}>
            {t("settings.embeddings.envNote")}
          </p>
        </div>
      )}
    </div>
  );
}

function EmbedRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 11, color: "var(--syn-text-muted)" }}>{label}</span>
      <span
        style={{
          fontSize: 12,
          color: "var(--syn-text)",
          fontFamily: mono ? "monospace" : undefined,
          padding: "5px 8px",
          background: "var(--syn-surface-sunken)",
          borderRadius: 4,
          border: "1px solid var(--syn-border)",
          wordBreak: "break-all",
        }}
      >
        {value}
      </span>
    </div>
  );
}

// ─── Section: Source Watch ────────────────────────────────────────────────────

function SectionSourceWatch() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader title={t("settings.nav.sourceWatch")} desc={t("settings.import.title")} />
      <ImportScheduleCard />
    </div>
  );
}

// ─── Section: Web Search ─────────────────────────────────────────────────────
// ADR-0041: SearXNG is the ONLY web-search backend (I9). No provider field.
// I3: single fetch on mount; PUT on each user action; local state only — no Zustand store.

function SectionWebSearch() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<WebSearchConfigResponse | null>(null);
  const [err, setErr] = useState(false);
  const [busy, setBusy] = useState(false);

  // Field local states — seeded from fetch on mount, then updated from PUT responses.
  const [urlInput, setUrlInput] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [categoriesInput, setCategoriesInput] = useState("");
  const [maxQueriesInput, setMaxQueriesInput] = useState<number>(3);

  useEffect(() => {
    const ac = new AbortController();
    fetchWebSearchConfig(ac.signal)
      .then((data) => {
        setCfg(data);
        setErr(false);
        setUrlInput(data.url ?? "");
        setCategoriesInput(data.categories.join(","));
        setMaxQueriesInput(data.max_queries);
      })
      .catch((e: unknown) => {
        if (!(e instanceof Error) || e.name !== "AbortError") setErr(true);
      });
    return () => { ac.abort(); };
  }, []);

  /** Apply response from any PUT /web-search/config to local state (I3). */
  const applyResponse = (resp: WebSearchConfigResponse) => {
    setCfg(resp);
    setUrlInput(resp.url ?? "");
    setCategoriesInput(resp.categories.join(","));
    setMaxQueriesInput(resp.max_queries);
    setUrlError(null);
  };

  /** Validate a URL string: must be http or https. */
  const validateUrl = (raw: string): boolean => {
    if (raw.trim() === "") return true; // empty = clear = valid
    try {
      const u = new URL(raw.trim());
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  };

  const handleSaveUrl = async () => {
    if (busy) return;
    const raw = urlInput.trim();
    if (raw !== "" && !validateUrl(raw)) {
      setUrlError(t("settings.webSearch.urlValidationError"));
      return;
    }
    setUrlError(null);
    setBusy(true);
    try {
      const resp = await setWebSearchConfig({ set_url: raw === "" ? null : raw });
      applyResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setBusy(false);
    }
  };

  const handleSaveCategories = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const resp = await setWebSearchConfig({ set_categories: categoriesInput });
      applyResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setBusy(false);
    }
  };

  const handleSaveMaxQueries = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const resp = await setWebSearchConfig({ set_max_queries: maxQueriesInput });
      applyResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setBusy(false);
    }
  };

  const handleClear = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const resp = await setWebSearchConfig({ clear: true });
      applyResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <SectionHeader title={t("settings.nav.webSearch")} desc={t("settings.webSearch.desc")} />

      {/* SearXNG-only notice (I9) */}
      <div style={{
        marginBottom: 20,
        padding: "8px 12px",
        background: "var(--syn-bg-soft)",
        border: "1px solid var(--syn-border)",
        borderRadius: 6,
        fontSize: 11,
        color: "var(--syn-text-muted)",
        lineHeight: 1.5,
      }}>
        {t("settings.webSearch.searxngOnly")}
      </div>

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{t("settings.webSearch.error")}</p>
      ) : cfg === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("settings.webSearch.loading")}</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

          {/* Status / source badge row */}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              data-testid="web-search-configured-badge"
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                background: cfg.configured ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)",
                border: `1px solid ${cfg.configured ? "color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)" : "color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)"}`,
                color: cfg.configured ? "var(--syn-green)" : "var(--syn-red)",
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              {cfg.configured ? t("settings.webSearch.configuredBadge") : t("settings.webSearch.notConfiguredBadge")}
            </span>
            <span
              data-testid="web-search-source-badge"
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                background: "var(--syn-surface-hover)",
                color: "var(--syn-text-muted)",
                fontSize: 11,
              }}
            >
              {t("settings.webSearch.sourceBadge", { source: cfg.source })}
            </span>
          </div>

          {/* URL field */}
          <div>
            <Field label={t("settings.webSearch.urlLabel")}>
              <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
                {t("settings.webSearch.urlHelp")}
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="text"
                  data-testid="web-search-url-input"
                  value={urlInput}
                  onChange={(e) => { setUrlInput(e.target.value); setUrlError(null); }}
                  placeholder={t("settings.webSearch.urlPlaceholder")}
                  style={{ ...INPUT_STYLE, flex: 1 }}
                />
                <button
                  data-testid="web-search-url-save"
                  onClick={() => { void handleSaveUrl(); }}
                  disabled={busy}
                  style={{
                    ...BTN_PRIMARY,
                    opacity: busy ? 0.4 : 1,
                    cursor: busy ? "not-allowed" : "pointer",
                    flexShrink: 0,
                  }}
                >
                  {busy ? "…" : t("settings.webSearch.urlSave")}
                </button>
              </div>
              {urlError && (
                <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-red)" }}>{urlError}</p>
              )}
            </Field>
          </div>

          {/* Categories field */}
          <div>
            <Field label={t("settings.webSearch.categoriesLabel")}>
              <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
                {t("settings.webSearch.categoriesHelp")}
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="text"
                  data-testid="web-search-categories-input"
                  value={categoriesInput}
                  onChange={(e) => setCategoriesInput(e.target.value)}
                  placeholder={t("settings.webSearch.categoriesPlaceholder")}
                  style={{ ...INPUT_STYLE, flex: 1 }}
                />
                <button
                  data-testid="web-search-categories-save"
                  onClick={() => { void handleSaveCategories(); }}
                  disabled={busy}
                  style={{
                    ...BTN_PRIMARY,
                    opacity: busy ? 0.4 : 1,
                    cursor: busy ? "not-allowed" : "pointer",
                    flexShrink: 0,
                  }}
                >
                  {busy ? "…" : t("settings.webSearch.categoriesSave")}
                </button>
              </div>
            </Field>
          </div>

          {/* Max queries field */}
          <div>
            <Field label={t("settings.webSearch.maxQueriesLabel")}>
              <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
                {t("settings.webSearch.maxQueriesHelp")}
              </p>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  type="number"
                  data-testid="web-search-max-queries-input"
                  value={maxQueriesInput}
                  min={1}
                  max={50}
                  onChange={(e) => setMaxQueriesInput(Number(e.target.value))}
                  style={{ ...INPUT_STYLE, width: 80 }}
                />
                <button
                  data-testid="web-search-max-queries-save"
                  onClick={() => { void handleSaveMaxQueries(); }}
                  disabled={busy}
                  style={{
                    ...BTN_PRIMARY,
                    opacity: busy ? 0.4 : 1,
                    cursor: busy ? "not-allowed" : "pointer",
                  }}
                >
                  {busy ? "…" : t("settings.webSearch.maxQueriesSave")}
                </button>
              </div>
            </Field>
          </div>

          {/* Clear all DB overrides */}
          <div style={{ paddingTop: 8, borderTop: "1px solid var(--syn-border)" }}>
            <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
              {t("settings.webSearch.clearHelp")}
            </p>
            <button
              data-testid="web-search-clear-btn"
              onClick={() => { void handleClear(); }}
              disabled={busy}
              style={{
                padding: "6px 14px",
                border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                borderRadius: 6,
                background: "transparent",
                color: "var(--syn-red)",
                fontSize: 12,
                cursor: busy ? "not-allowed" : "pointer",
                fontWeight: 500,
                opacity: busy ? 0.4 : 1,
              }}
            >
              {t("settings.webSearch.clearButton")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Section: API + MCP ───────────────────────────────────────────────────────
// ADR-0027 §2.4 (read-only panel) + ADR-0032 (scoped exception: remote toggle).
// I3: single fetch on mount; no Zustand store; toggle = one fetch/PUT, local state only.
// I9: display only except for the one config-write control (PUT /mcp/remote — ADR-0032 §2.6).

/**
 * Build a Claude Desktop MCP JSON snippet from the live API payload (ADR-0027 §2.4).
 * entry_point_command is tokenised: argv[0] = command, rest = args.
 * Server is keyed by server_name — nothing is hardcoded (I6).
 */
function buildClaudeDesktopSnippet(mcpInfo: McpInfoResponse): string {
  const tokens = mcpInfo.entry_point_command.trim().split(/\s+/);
  const command = tokens[0] ?? "";
  const args = tokens.slice(1);
  const payload = {
    mcpServers: {
      [mcpInfo.server_name]: {
        command,
        args,
      },
    },
  };
  return JSON.stringify(payload, null, 2);
}

/**
 * Build the claude.ai remote-MCP connection snippet (ADR-0032 §2.7).
 * Shows the URL and instructions — mirrors the Desktop snippet style.
 * The token is NEVER included (ADR-0032 §2.5).
 * url = window.location.origin + info.mount_path (I6 — no host hardcoded).
 */
function buildRemoteMcpSnippet(remoteUrl: string): string {
  const payload = {
    mcpServers: {
      synapse_remote: {
        type: "http",
        url: remoteUrl,
      },
    },
  };
  return JSON.stringify(payload, null, 2);
}

function SectionApiMcp() {
  const { t } = useTranslation();
  const [info, setInfo] = useState<McpInfoResponse | null>(null);
  const [err, setErr] = useState(false);
  const [copied, setCopied] = useState(false);

  // Remote toggle local state — derives from info on fetch; updated from PUT response (I3).
  // Separate from `info` so we can update the toggle state without re-fetching all fields.
  const [remoteEnabled, setRemoteEnabled] = useState(false);
  const [tokenConfigured, setTokenConfigured] = useState(false);
  const [tokenSource, setTokenSource] = useState<"db" | "env" | "none">("none");
  const [allowWithoutToken, setAllowWithoutToken] = useState(false);
  const [mountPath, setMountPath] = useState("/mcp/server");
  const [remoteWrite, setRemoteWrite] = useState(false);
  const [toggleBusy, setToggleBusy] = useState(false);
  const [copiedRemote, setCopiedRemote] = useState(false);
  const [copiedRemoteSnippet, setCopiedRemoteSnippet] = useState(false);

  // ADR-0033: one-time generated token reveal (local state only — never persisted, I3).
  const [generatedToken, setGeneratedToken] = useState<string | null>(null);
  const [copiedGenToken, setCopiedGenToken] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    fetchMcpInfo(ac.signal)
      .then((data) => {
        setInfo(data);
        setErr(false);
        // Seed local state from fetched posture (ADR-0032 §2.7, ADR-0033 §2.5).
        setRemoteEnabled(data.remote_enabled);
        setTokenConfigured(data.token_configured);
        setTokenSource(data.token_source);
        setAllowWithoutToken(data.allow_without_token);
        setMountPath(data.mount_path);
        setRemoteWrite(data.remote_write_enabled);
      })
      .catch((e: unknown) => {
        if (!(e instanceof Error) || e.name !== "AbortError") setErr(true);
      });
    return () => { ac.abort(); };
  }, []);

  /** Sync all local posture state from a PUT /mcp/auth response (ADR-0033 §2.5). */
  const applyAuthResponse = (resp: McpAuthResponse) => {
    setTokenConfigured(resp.token_configured);
    setTokenSource(resp.token_source);
    setAllowWithoutToken(resp.allow_without_token);
    setRemoteEnabled(resp.remote_enabled);
    setMountPath(resp.mount_path);
  };

  const handleCopy = () => {
    if (!info) return;
    const snippet = buildClaudeDesktopSnippet(info);
    navigator.clipboard.writeText(snippet).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }).catch(() => { /* clipboard unavailable */ });
  };

  /**
   * Handle the remote toggle flip (ADR-0032 §2.4 / §2.7).
   * Calls PUT /mcp/remote, then applies the server response to local state.
   * If clamped=true, the server refused to enable — keep off and the UI shows the no-token note.
   * Guard: remote can be enabled when token_configured OR allow_without_token (ADR-0033 §2.4).
   * I3: a single fetch/PUT on interaction; no Zustand store.
   */
  const handleRemoteToggle = async () => {
    const canEnable = tokenConfigured || allowWithoutToken;
    if (toggleBusy || !canEnable) return;
    const next = !remoteEnabled;
    setToggleBusy(true);
    try {
      const resp: McpRemoteStateResponse = await setRemoteMcpEnabled(next);
      setRemoteEnabled(resp.remote_enabled);
      setTokenConfigured(resp.token_configured);
      setMountPath(resp.mount_path);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      // On network error, do not flip the toggle — keep existing posture.
    } finally {
      setToggleBusy(false);
    }
  };

  /**
   * Generate a new token (ADR-0033 §2.5 rotate_token).
   * The server returns generated_token ONCE — we hold it in local state for the reveal box.
   * It is never written to any store or localStorage (I3).
   */
  const handleGenerateToken = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    setGeneratedToken(null); // clear any prior reveal before the call
    try {
      const resp = await setMcpAuth({ rotate_token: true });
      applyAuthResponse(resp);
      if (resp.generated_token) {
        setGeneratedToken(resp.generated_token);
      }
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
    }
  };

  /**
   * Clear the stored token (ADR-0033 §2.5 clear_token).
   * After this, the surface falls back to env bootstrap or "no token" posture.
   */
  const handleClearToken = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    setGeneratedToken(null);
    try {
      const resp = await setMcpAuth({ clear_token: true });
      applyAuthResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
    }
  };

  /**
   * Toggle the "allow without token" flag (ADR-0033 §2.3).
   * The server applies the allow-aware clamp on remote_enabled; we reflect the result.
   */
  const handleAllowWithoutTokenToggle = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    try {
      const resp = await setMcpAuth({ allow_without_token: !allowWithoutToken });
      applyAuthResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
    }
  };

  const handleDismissGeneratedToken = () => {
    setGeneratedToken(null);
  };

  const handleCopyGeneratedToken = () => {
    if (!generatedToken) return;
    navigator.clipboard.writeText(generatedToken).then(() => {
      setCopiedGenToken(true);
      setTimeout(() => setCopiedGenToken(false), 2000);
    }).catch(() => { /* clipboard unavailable */ });
  };

  const remoteUrl = `${window.location.origin}${mountPath}`;

  const handleCopyRemoteUrl = () => {
    navigator.clipboard.writeText(remoteUrl).then(() => {
      setCopiedRemote(true);
      setTimeout(() => setCopiedRemote(false), 2000);
    }).catch(() => { /* clipboard unavailable */ });
  };

  const handleCopyRemoteSnippet = () => {
    const snippet = buildRemoteMcpSnippet(remoteUrl);
    navigator.clipboard.writeText(snippet).then(() => {
      setCopiedRemoteSnippet(true);
      setTimeout(() => setCopiedRemoteSnippet(false), 2000);
    }).catch(() => { /* clipboard unavailable */ });
  };

  // Derived: whether the remote toggle can be enabled.
  // ADR-0033 §2.4: remote can be ON when token_configured OR allow_without_token.
  const canEnableRemote = tokenConfigured || allowWithoutToken;

  // Derived: human-readable token posture label (ADR-0033 §2.5, i18n).
  const tokenPostureKey = !tokenConfigured
    ? "settings.apiMcp.access.postureNone"
    : tokenSource === "db"
    ? "settings.apiMcp.access.postureDb"
    : "settings.apiMcp.access.postureEnv";

  return (
    <div>
      <SectionHeader title={t("settings.nav.apiMcp")} desc={t("settings.apiMcp.desc")} />

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>
          {t("settings.apiMcp.error")}
        </p>
      ) : info === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>
          {t("settings.apiMcp.loading")}
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>

          {/* ── Access sub-block — ADR-0033 §2.6 ── */}
          <div>
            <p style={{ margin: "0 0 12px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {t("settings.apiMcp.access.title")}
            </p>

            {/* Token posture row */}
            <div
              style={{
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: "1px solid var(--syn-border)",
                borderRadius: 8,
                marginBottom: 10,
              }}
            >
              {/* Posture label */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: tokenConfigured ? "var(--syn-green)" : "var(--syn-text-dim)",
                    flexShrink: 0,
                    display: "inline-block",
                  }}
                />
                <span
                  data-testid="mcp-token-posture"
                  style={{ fontSize: 12, fontWeight: 600, color: tokenConfigured ? "var(--syn-green)" : "var(--syn-text-dim)" }}
                >
                  {t(tokenPostureKey)}
                </span>
              </div>

              {/* Action buttons */}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  data-testid="mcp-generate-token-btn"
                  onClick={() => { void handleGenerateToken(); }}
                  disabled={authBusy}
                  style={{
                    ...BTN_PRIMARY,
                    opacity: authBusy ? 0.4 : 1,
                    cursor: authBusy ? "not-allowed" : "pointer",
                  }}
                >
                  {authBusy
                    ? "…"
                    : tokenConfigured
                    ? t("settings.apiMcp.access.rotateToken")
                    : t("settings.apiMcp.access.generateToken")}
                </button>
                {tokenConfigured && (
                  <button
                    data-testid="mcp-clear-token-btn"
                    onClick={() => { void handleClearToken(); }}
                    disabled={authBusy}
                    style={{
                      padding: "6px 14px",
                      border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                      borderRadius: 6,
                      background: "transparent",
                      color: "var(--syn-red)",
                      fontSize: 12,
                      cursor: authBusy ? "not-allowed" : "pointer",
                      fontWeight: 500,
                      opacity: authBusy ? 0.4 : 1,
                    }}
                  >
                    {t("settings.apiMcp.access.clearToken")}
                  </button>
                )}
              </div>
            </div>

            {/* One-time token reveal — shown ONLY immediately after rotate_token (ADR-0033 §2.1) */}
            {generatedToken !== null && (
              <div
                style={{
                  padding: "12px 14px",
                  background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)",
                  border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                  borderRadius: 8,
                  marginBottom: 10,
                }}
              >
                <p style={{ margin: "0 0 6px", fontSize: 12, fontWeight: 700, color: "var(--syn-green)" }}>
                  {t("settings.apiMcp.access.revealWarning")}
                </p>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span
                    data-testid="mcp-generated-token"
                    style={{
                      flex: 1,
                      fontFamily: "monospace",
                      fontSize: 12,
                      color: "var(--syn-text)",
                      padding: "6px 10px",
                      background: "var(--syn-bg)",
                      border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                      borderRadius: 4,
                      wordBreak: "break-all",
                      userSelect: "all",
                    }}
                  >
                    {generatedToken}
                  </span>
                  <button
                    data-testid="mcp-copy-generated-token-btn"
                    onClick={handleCopyGeneratedToken}
                    style={{
                      padding: "6px 12px",
                      border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                      borderRadius: 4,
                      background: copiedGenToken ? "var(--syn-green)" : "transparent",
                      color: copiedGenToken ? "#fff" : "var(--syn-green)",
                      fontSize: 11,
                      cursor: "pointer",
                      flexShrink: 0,
                      transition: "background 0.15s, color 0.15s",
                    }}
                  >
                    {copiedGenToken ? t("settings.apiMcp.copied") : t("common.copy")}
                  </button>
                </div>
                <button
                  data-testid="mcp-dismiss-generated-token-btn"
                  onClick={handleDismissGeneratedToken}
                  style={{
                    marginTop: 8,
                    padding: "4px 10px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 4,
                    background: "transparent",
                    color: "var(--syn-text-muted)",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  {t("settings.apiMcp.access.dismissToken")}
                </button>
              </div>
            )}

            {/* "Allow without token" switch — ADR-0033 §2.3 */}
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: `1px solid ${allowWithoutToken ? "color-mix(in srgb, var(--syn-amber) 30%, var(--syn-mix-base) 70%)" : "var(--syn-border)"}`,
                borderRadius: 8,
                opacity: authBusy ? 0.6 : 1,
                transition: "border-color 0.15s, opacity 0.15s",
              }}
            >
              <label
                style={{ display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer", userSelect: "none", flex: 1 }}
              >
                <span style={{ position: "relative", display: "inline-block", width: 36, height: 20, flexShrink: 0, marginTop: 1 }}>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label={t("settings.apiMcp.access.allowWithoutTokenLabel")}
                    data-testid="mcp-allow-without-token"
                    checked={allowWithoutToken}
                    disabled={authBusy}
                    onChange={() => { void handleAllowWithoutTokenToggle(); }}
                    style={{ position: "absolute", opacity: 0, width: 0, height: 0 }}
                  />
                  {/* Track */}
                  <span
                    aria-hidden="true"
                    style={{
                      display: "block",
                      width: 36,
                      height: 20,
                      borderRadius: 10,
                      background: allowWithoutToken ? "var(--syn-amber)" : "var(--syn-border)",
                      border: `1px solid ${allowWithoutToken ? "var(--syn-amber)" : "var(--syn-border-subtle)"}`,
                      transition: "background 0.15s, border-color 0.15s",
                    }}
                  />
                  {/* Thumb */}
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      top: 3,
                      left: allowWithoutToken ? 19 : 3,
                      width: 14,
                      height: 14,
                      borderRadius: "50%",
                      background: allowWithoutToken ? "#fff" : "var(--syn-text-dim)",
                      transition: "left 0.15s, background 0.15s",
                    }}
                  />
                </span>
                <div>
                  <span style={{ fontSize: 13, fontWeight: 600, color: allowWithoutToken ? "var(--syn-amber)" : "var(--syn-text-muted)" }}>
                    {t("settings.apiMcp.access.allowWithoutTokenLabel")}
                  </span>
                  {/* Security caveat — always visible for this switch (ADR-0033 §2.3) */}
                  <p
                    data-testid="mcp-allow-without-token-caveat"
                    style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-amber)", lineHeight: 1.5 }}
                  >
                    {t("settings.apiMcp.access.allowWithoutTokenCaveat")}
                  </p>
                </div>
              </label>
            </div>
          </div>

          {/* ── Remote (claude.ai) sub-section — ADR-0032 §2.7 ── */}
          <div>
            <p style={{ margin: "0 0 12px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {t("settings.apiMcp.remote.title")}
            </p>

            {/* Toggle row */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: `1px solid ${remoteEnabled ? "var(--syn-accent)" : "var(--syn-border)"}`,
                borderRadius: 8,
                marginBottom: 10,
                opacity: toggleBusy ? 0.6 : 1,
                transition: "border-color 0.15s, opacity 0.15s",
              }}
            >
              {/* Checkbox-based toggle switch — accessible (ADR-0032 §2.7) */}
              <label
                style={{ display: "flex", alignItems: "center", gap: 10, cursor: canEnableRemote ? "pointer" : "not-allowed", userSelect: "none", flex: 1 }}
                title={canEnableRemote ? undefined : t("settings.apiMcp.remote.noTokenNote")}
              >
                <span style={{ position: "relative", display: "inline-block", width: 36, height: 20, flexShrink: 0 }}>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label={t("settings.apiMcp.remote.enabledLabel")}
                    data-testid="mcp-remote-toggle"
                    checked={remoteEnabled}
                    disabled={!canEnableRemote || toggleBusy}
                    onChange={() => { void handleRemoteToggle(); }}
                    style={{ position: "absolute", opacity: 0, width: 0, height: 0 }}
                  />
                  {/* Track */}
                  <span
                    aria-hidden="true"
                    style={{
                      display: "block",
                      width: 36,
                      height: 20,
                      borderRadius: 10,
                      background: remoteEnabled ? "var(--syn-accent)" : "var(--syn-border)",
                      border: `1px solid ${remoteEnabled ? "var(--syn-accent)" : "var(--syn-border-subtle)"}`,
                      transition: "background 0.15s, border-color 0.15s",
                    }}
                  />
                  {/* Thumb */}
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      top: 3,
                      left: remoteEnabled ? 19 : 3,
                      width: 14,
                      height: 14,
                      borderRadius: "50%",
                      background: remoteEnabled ? "#fff" : "var(--syn-text-dim)",
                      transition: "left 0.15s, background 0.15s",
                    }}
                  />
                </span>
                <div>
                  <span style={{ fontSize: 13, fontWeight: 600, color: remoteEnabled ? "var(--syn-text)" : "var(--syn-text-muted)" }}>
                    {t("settings.apiMcp.remote.enabledLabel")}
                  </span>
                  {/* No-token note rendered inline when neither token nor allow is configured */}
                  {!canEnableRemote && (
                    <p style={{ margin: "3px 0 0", fontSize: 11, color: "var(--syn-amber)", lineHeight: 1.5 }}>
                      {t("settings.apiMcp.remote.noTokenNote")}
                    </p>
                  )}
                </div>
              </label>

              {/* read-only / read-write badge — visible only when enabled */}
              {remoteEnabled && (
                <span
                  style={{
                    padding: "2px 8px",
                    borderRadius: 4,
                    background: remoteWrite ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "var(--syn-surface-hover)",
                    color: remoteWrite ? "var(--syn-green)" : "var(--syn-text-muted)",
                    fontSize: 10,
                    fontWeight: 600,
                    letterSpacing: "0.04em",
                    flexShrink: 0,
                  }}
                >
                  {remoteWrite ? t("settings.apiMcp.remote.readWriteBadge") : t("settings.apiMcp.remote.readOnlyBadge")}
                </span>
              )}
            </div>

            {/* URL row — shown only when enabled (ADR-0032 §2.7 state 3) */}
            {remoteEnabled && (
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
                <div>
                  <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)" }}>
                    {t("settings.apiMcp.remote.urlLabel")}
                  </p>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span
                      data-testid="mcp-remote-url"
                      style={{
                        flex: 1,
                        fontFamily: "monospace",
                        fontSize: 12,
                        color: "var(--syn-accent)",
                        padding: "5px 8px",
                        background: "var(--syn-accent-soft)",
                        border: "1px solid color-mix(in srgb, var(--syn-accent) 30%, var(--syn-mix-base) 70%)",
                        borderRadius: 4,
                        wordBreak: "break-all",
                      }}
                    >
                      {remoteUrl}
                    </span>
                    <button
                      data-testid="mcp-remote-url-copy"
                      onClick={handleCopyRemoteUrl}
                      style={{
                        padding: "5px 10px",
                        border: "1px solid var(--syn-border)",
                        borderRadius: 4,
                        background: copiedRemote ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "transparent",
                        color: copiedRemote ? "var(--syn-green)" : "var(--syn-text-muted)",
                        fontSize: 11,
                        cursor: "pointer",
                        flexShrink: 0,
                        transition: "background 0.15s, color 0.15s",
                      }}
                    >
                      {copiedRemote ? t("settings.apiMcp.copied") : t("common.copy")}
                    </button>
                  </div>
                </div>

                {/* claude.ai remote-MCP connection snippet — mirrors Desktop snippet style (ADR-0032 §2.7) */}
                <div>
                  <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)" }}>
                    {t("settings.apiMcp.remote.snippetLabel")}
                  </p>
                  <div
                    data-testid="mcp-remote-snippet"
                    style={{
                      fontFamily: "monospace",
                      fontSize: 11,
                      background: "var(--syn-surface-sunken)",
                      border: "1px solid var(--syn-border)",
                      borderRadius: 6,
                      padding: "10px 12px",
                      color: "var(--syn-text-muted)",
                      whiteSpace: "pre",
                      overflowX: "auto",
                      marginBottom: 6,
                    }}
                  >
                    {buildRemoteMcpSnippet(remoteUrl)}
                  </div>
                  <button
                    data-testid="mcp-remote-snippet-copy"
                    onClick={handleCopyRemoteSnippet}
                    style={{
                      padding: "5px 12px",
                      border: "1px solid var(--syn-border)",
                      borderRadius: 6,
                      background: copiedRemoteSnippet ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "transparent",
                      color: copiedRemoteSnippet ? "var(--syn-green)" : "var(--syn-text-muted)",
                      fontSize: 11,
                      cursor: "pointer",
                      transition: "background 0.15s, color 0.15s",
                    }}
                  >
                    {copiedRemoteSnippet ? t("settings.apiMcp.copied") : t("settings.apiMcp.remote.copySnippet")}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* ── Connection sub-section (stdio — read-only, ADR-0027) ── */}
          <div>
            <p style={{ margin: "0 0 10px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {t("settings.apiMcp.connectionTitle")}
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <EmbedRow label={t("settings.apiMcp.transportLabel")} value={info.transport} mono />
              <EmbedRow label={t("settings.apiMcp.entryPointLabel")} value={info.entry_point_command} mono />
            </div>

            {/* Claude Desktop copy snippet — generated from payload (I6) */}
            <div style={{ marginTop: 14 }}>
              <div
                style={{
                  fontFamily: "monospace",
                  fontSize: 11,
                  background: "var(--syn-surface-sunken)",
                  border: "1px solid var(--syn-border)",
                  borderRadius: 6,
                  padding: "10px 12px",
                  color: "var(--syn-text-muted)",
                  whiteSpace: "pre",
                  overflowX: "auto",
                  marginBottom: 8,
                }}
                data-testid="mcp-snippet"
              >
                {buildClaudeDesktopSnippet(info)}
              </div>
              <button
                onClick={handleCopy}
                data-testid="mcp-copy-btn"
                style={{
                  padding: "5px 12px",
                  border: "1px solid var(--syn-border)",
                  borderRadius: 6,
                  background: copied ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "transparent",
                  color: copied ? "var(--syn-green)" : "var(--syn-text-muted)",
                  fontSize: 11,
                  cursor: "pointer",
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                {copied ? t("settings.apiMcp.copied") : t("settings.apiMcp.copySnippet")}
              </button>
            </div>
          </div>

          {/* ── Tools sub-section — rendered from info.tools, nothing hardcoded (I6/I9) ── */}
          <div>
            <p style={{ margin: "0 0 10px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {t("settings.apiMcp.toolsTitle")}
              <span style={{ marginLeft: 8, fontWeight: 400, textTransform: "none", color: "var(--syn-text-dim)" }}>
                ({info.tool_count})
              </span>
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {info.tools.map((tool) => {
                const paramCount = Object.keys(tool.input_schema.properties ?? {}).length;
                const firstSentence = (tool.description ?? "").split(/[.!?]/)[0] ?? "";
                const truncated = firstSentence.length > 80
                  ? firstSentence.slice(0, 79) + "…"
                  : firstSentence;
                return (
                  <div
                    key={tool.name}
                    data-testid={`mcp-tool-row-${tool.name}`}
                    className="settings-mcp-tool-row"
                    style={{
                      display: "grid",
                      gridTemplateColumns: "140px 1fr auto",
                      gap: 10,
                      alignItems: "center",
                      padding: "8px 12px",
                      background: "var(--syn-bg-soft)",
                      border: "1px solid var(--syn-border)",
                      borderRadius: 6,
                    }}
                  >
                    <span
                      data-testid={`mcp-tool-name-${tool.name}`}
                      style={{ fontFamily: "monospace", fontSize: 12, color: "var(--syn-text)", fontWeight: 600 }}
                    >
                      {tool.name}
                    </span>
                    <span style={{ fontSize: 12, color: "var(--syn-text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {truncated}
                    </span>
                    <span
                      data-testid={`mcp-tool-params-${tool.name}`}
                      data-param-count={paramCount}
                      style={{ fontSize: 11, color: "var(--syn-text-dim)", whiteSpace: "nowrap" }}
                    >
                      {t("settings.apiMcp.paramCount", { count: paramCount })}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* ── CLI Subscription Auth sub-block — ADR-0043 §2.6 ── */}
          <SectionCliAuth />

        </div>
      )}
    </div>
  );
}

// ─── Section: CLI Subscription Auth ─────────────────────────────────────────
// ADR-0043: user pastes their own token from `claude setup-token` (no server generation).
// password field + Save (PUT {token}) + Clear (PUT {clear:true}).
// Token value NEVER shown (no reveal — GET never returns it; ADR-0043 Do-NOT #2).
// I3: local state only; no Zustand store; plain <input type="password">.

function SectionCliAuth() {
  const { t } = useTranslation();
  const [posture, setPosture] = useState<CliAuthConfig | null>(null);
  const [err, setErr] = useState(false);
  // password field — local state only; discarded on save/clear (ADR-0043 Do-NOT for no persistence).
  const [tokenInput, setTokenInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    getCliAuthConfig(ac.signal)
      .then((data) => { setPosture(data); setErr(false); })
      .catch((e: unknown) => { if (!(e instanceof Error) || e.name !== "AbortError") setErr(true); });
    return () => { ac.abort(); };
  }, []);

  /** Apply the post-write posture from any PUT response. */
  const applyPosture = (resp: CliAuthConfig) => {
    setPosture(resp);
    // Discard the typed token from the field — never persisted (ADR-0043 §2.6).
    setTokenInput("");
    setSaveErr(null);
  };

  const handleSave = async () => {
    if (busy) return;
    const trimmed = tokenInput.trim();
    if (trimmed === "") {
      setSaveErr(t("settings.cliAuth.emptyTokenError"));
      return;
    }
    setBusy(true);
    setSaveErr(null);
    try {
      const resp = await setCliAuthConfig({ token: trimmed });
      applyPosture(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setSaveErr(t("settings.cliAuth.saveError"));
    } finally {
      setBusy(false);
    }
  };

  const handleClear = async () => {
    if (busy) return;
    setBusy(true);
    setSaveErr(null);
    try {
      const resp = await setCliAuthConfig({ clear: true });
      applyPosture(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setSaveErr(t("settings.cliAuth.saveError"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="cli-auth-section">
      <p style={{ margin: "0 0 12px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {t("settings.cliAuth.title")}
      </p>

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{t("settings.cliAuth.error")}</p>
      ) : posture === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("settings.cliAuth.loading")}</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

          {/* Posture badges */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span
              data-testid="cli-auth-configured-badge"
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                background: posture.token_configured ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)",
                border: `1px solid ${posture.token_configured ? "color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)" : "color-mix(in srgb, var(--syn-red) 30%, var(--syn-mix-base) 70%)"}`,
                color: posture.token_configured ? "var(--syn-green)" : "var(--syn-red)",
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              {posture.token_configured
                ? t("settings.cliAuth.configuredBadge")
                : t("settings.cliAuth.notConfiguredBadge")}
            </span>
            <span
              data-testid="cli-auth-source-badge"
              style={{ padding: "2px 8px", borderRadius: 4, background: "var(--syn-surface-hover)", color: "var(--syn-text-muted)", fontSize: 11 }}
            >
              {t("settings.cliAuth.sourceBadge", { source: posture.token_source })}
            </span>
            <span
              data-testid="cli-auth-mode-badge"
              style={{
                padding: "2px 8px",
                borderRadius: 4,
                background: posture.auth_mode === "subscription" ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "var(--syn-surface-hover)",
                color: posture.auth_mode === "subscription" ? "var(--syn-green)" : "var(--syn-text-muted)",
                fontSize: 11,
              }}
            >
              {t(`settings.cliAuth.authMode.${posture.auth_mode}`)}
            </span>
          </div>

          {/* Password input + Save + Clear */}
          <div>
            <label style={{ display: "block", marginBottom: 6, fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)" }}>
              {t("settings.cliAuth.tokenLabel")}
            </label>
            <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
              {t("settings.cliAuth.tokenHelp")}
            </p>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <input
                type="password"
                data-testid="cli-auth-token-input"
                value={tokenInput}
                onChange={(e) => { setTokenInput(e.target.value); setSaveErr(null); }}
                placeholder={t("settings.cliAuth.tokenPlaceholder")}
                autoComplete="off"
                style={{ ...INPUT_STYLE, flex: 1, minWidth: 200 }}
              />
              <button
                data-testid="cli-auth-save-btn"
                onClick={() => { void handleSave(); }}
                disabled={busy}
                style={{
                  ...BTN_PRIMARY,
                  opacity: busy ? 0.4 : 1,
                  cursor: busy ? "not-allowed" : "pointer",
                  flexShrink: 0,
                }}
              >
                {busy ? "…" : t("settings.cliAuth.saveButton")}
              </button>
              {posture.token_configured && (
                <button
                  data-testid="cli-auth-clear-btn"
                  onClick={() => { void handleClear(); }}
                  disabled={busy}
                  style={{
                    padding: "6px 14px",
                    border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                    borderRadius: 6,
                    background: "transparent",
                    color: "var(--syn-red)",
                    fontSize: 12,
                    cursor: busy ? "not-allowed" : "pointer",
                    fontWeight: 500,
                    opacity: busy ? 0.4 : 1,
                    flexShrink: 0,
                  }}
                >
                  {t("settings.cliAuth.clearButton")}
                </button>
              )}
            </div>
            {saveErr && (
              <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--syn-red)" }}>{saveErr}</p>
            )}
          </div>

          {/* Mini-guide (ADR-0043 §2.6) */}
          <div
            data-testid="cli-auth-guide"
            style={{
              padding: "10px 14px",
              background: "var(--syn-bg-soft)",
              border: "1px solid var(--syn-border)",
              borderRadius: 8,
              fontSize: 11,
              color: "var(--syn-text-muted)",
              lineHeight: 1.7,
            }}
          >
            <p style={{ margin: "0 0 6px", fontWeight: 600, color: "var(--syn-text-muted)" }}>
              {t("settings.cliAuth.guideTitle")}
            </p>
            <p style={{ margin: 0, whiteSpace: "pre-line" }}>
              {t("settings.cliAuth.guideSteps")}
            </p>
          </div>

          {/* Security caveat (ADR-0043 §2.1 / §2.6) */}
          <div
            data-testid="cli-auth-caveat"
            style={{
              padding: "8px 12px",
              background: "color-mix(in srgb, var(--syn-amber) 8%, var(--syn-mix-base) 92%)",
              border: "1px solid color-mix(in srgb, var(--syn-amber) 30%, var(--syn-mix-base) 70%)",
              borderRadius: 6,
              fontSize: 11,
              color: "var(--syn-amber)",
              lineHeight: 1.5,
            }}
          >
            {t("settings.cliAuth.caveat")}
          </div>

        </div>
      )}
    </div>
  );
}

// ─── Section: Web Clipper ────────────────────────────────────────────────────
// ADR-0040: mirrors SectionApiMcp token UX exactly — generate/rotate/clear with
// one-time reveal box, enable toggle, allowed-origins input.
// I3: single fetch on mount; PUT on each user action; local state only — no Zustand store.
// Token value NEVER shown except the one-time generated_token on rotate.

function SectionWebClipper() {
  const { t } = useTranslation();
  const [cfg, setCfg] = useState<ClipConfigResponse | null>(null);
  const [err, setErr] = useState(false);

  // Local state seeded from fetch; updated from every PUT response (I3).
  const [enabled, setEnabled] = useState(false);
  const [tokenConfigured, setTokenConfigured] = useState(false);
  const [tokenSource, setTokenSource] = useState<"db" | "env" | "none">("none");
  const [allowedOrigins, setAllowedOrigins] = useState<string[]>([]);
  const [originsInput, setOriginsInput] = useState("");
  const [maxBodyBytes, setMaxBodyBytes] = useState(0);

  // One-time generated token reveal (I3 — never persisted, never in store).
  const [generatedToken, setGeneratedToken] = useState<string | null>(null);
  const [copiedGenToken, setCopiedGenToken] = useState(false);

  const [authBusy, setAuthBusy] = useState(false);
  const [enableBusy, setEnableBusy] = useState(false);
  const [originsBusy, setOriginsBusy] = useState(false);
  const [copiedUrl, setCopiedUrl] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    fetchClipConfig(ac.signal)
      .then((data) => {
        setCfg(data);
        setErr(false);
        setEnabled(data.enabled);
        setTokenConfigured(data.token_configured);
        setTokenSource(data.token_source);
        setAllowedOrigins(data.allowed_origins);
        setOriginsInput(data.allowed_origins.join(", "));
        setMaxBodyBytes(data.max_body_bytes);
      })
      .catch((e: unknown) => {
        if (!(e instanceof Error) || e.name !== "AbortError") setErr(true);
      });
    return () => { ac.abort(); };
  }, []);

  /** Apply posture from any PUT /clip/config response to local state (I3). */
  const applyClipResponse = (resp: ClipConfigStateResponse) => {
    setEnabled(resp.enabled);
    setTokenConfigured(resp.token_configured);
    setTokenSource(resp.token_source);
    setAllowedOrigins(resp.allowed_origins);
    setOriginsInput(resp.allowed_origins.join(", "));
    setMaxBodyBytes(resp.max_body_bytes);
  };

  /** Toggle the clip ingress enabled flag (PUT set_enabled). */
  const handleEnableToggle = async () => {
    if (enableBusy) return;
    setEnableBusy(true);
    try {
      const resp = await setClipConfig({ set_enabled: !enabled });
      applyClipResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setEnableBusy(false);
    }
  };

  /**
   * Generate / rotate the clip token (ADR-0040 rotate_token).
   * generated_token returned ONCE — held in local state for reveal box.
   * Never written to any store or localStorage (I3).
   */
  const handleGenerateToken = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    setGeneratedToken(null); // clear any prior reveal before the call
    try {
      const resp = await setClipConfig({ rotate_token: true });
      applyClipResponse(resp);
      if (resp.generated_token) {
        setGeneratedToken(resp.generated_token);
      }
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
    }
  };

  /**
   * Clear the stored clip token (ADR-0040 clear_token).
   * Falls back to env bootstrap or "no token" posture.
   */
  const handleClearToken = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    setGeneratedToken(null);
    try {
      const resp = await setClipConfig({ clear_token: true });
      applyClipResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setAuthBusy(false);
    }
  };

  /** Save the allowed-origins field (PUT set_allowed_origins). */
  const handleSaveOrigins = async () => {
    if (originsBusy) return;
    setOriginsBusy(true);
    try {
      const resp = await setClipConfig({ set_allowed_origins: originsInput });
      applyClipResponse(resp);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
    } finally {
      setOriginsBusy(false);
    }
  };

  const handleDismissGeneratedToken = () => {
    setGeneratedToken(null);
  };

  const handleCopyGeneratedToken = () => {
    if (!generatedToken) return;
    navigator.clipboard.writeText(generatedToken).then(() => {
      setCopiedGenToken(true);
      setTimeout(() => setCopiedGenToken(false), 2000);
    }).catch(() => { /* clipboard unavailable */ });
  };

  const clipUrl = `${window.location.origin}/clip`;

  const handleCopyClipUrl = () => {
    navigator.clipboard.writeText(clipUrl).then(() => {
      setCopiedUrl(true);
      setTimeout(() => setCopiedUrl(false), 2000);
    }).catch(() => { /* clipboard unavailable */ });
  };

  // Derived: human-readable token posture label (mirrors SectionApiMcp pattern).
  const tokenPostureKey = !tokenConfigured
    ? "settings.webClipper.postureNone"
    : tokenSource === "db"
    ? "settings.webClipper.postureDb"
    : "settings.webClipper.postureEnv";

  return (
    <div>
      <SectionHeader title={t("settings.nav.webClipper")} desc={t("settings.webClipper.desc")} />

      {err ? (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{t("settings.webClipper.error")}</p>
      ) : cfg === null ? (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("settings.webClipper.loading")}</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>

          {/* ── Enable toggle ── */}
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: `1px solid ${enabled ? "var(--syn-accent)" : "var(--syn-border)"}`,
                borderRadius: 8,
                opacity: enableBusy ? 0.6 : 1,
                transition: "border-color 0.15s, opacity 0.15s",
              }}
            >
              <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", userSelect: "none", flex: 1 }}>
                <span style={{ position: "relative", display: "inline-block", width: 36, height: 20, flexShrink: 0 }}>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-label={t("settings.webClipper.enabledLabel")}
                    data-testid="clip-enabled-toggle"
                    checked={enabled}
                    disabled={enableBusy}
                    onChange={() => { void handleEnableToggle(); }}
                    style={{ position: "absolute", opacity: 0, width: 0, height: 0 }}
                  />
                  {/* Track */}
                  <span
                    aria-hidden="true"
                    style={{
                      display: "block",
                      width: 36,
                      height: 20,
                      borderRadius: 10,
                      background: enabled ? "var(--syn-accent)" : "var(--syn-border)",
                      border: `1px solid ${enabled ? "var(--syn-accent)" : "var(--syn-border-subtle)"}`,
                      transition: "background 0.15s, border-color 0.15s",
                    }}
                  />
                  {/* Thumb */}
                  <span
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      top: 3,
                      left: enabled ? 19 : 3,
                      width: 14,
                      height: 14,
                      borderRadius: "50%",
                      background: enabled ? "#fff" : "var(--syn-text-dim)",
                      transition: "left 0.15s, background 0.15s",
                    }}
                  />
                </span>
                <div>
                  <span style={{ fontSize: 13, fontWeight: 600, color: enabled ? "var(--syn-text)" : "var(--syn-text-muted)" }}>
                    {t("settings.webClipper.enabledLabel")}
                  </span>
                  <p style={{ margin: "3px 0 0", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
                    {t("settings.webClipper.enabledHelp")}
                  </p>
                </div>
              </label>
            </div>
          </div>

          {/* ── Token sub-block (mirrors SectionApiMcp Access sub-block) ── */}
          <div>
            <p style={{ margin: "0 0 12px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {t("settings.webClipper.tokenTitle")}
            </p>

            {/* Token posture row */}
            <div
              style={{
                padding: "10px 14px",
                background: "var(--syn-bg-soft)",
                border: "1px solid var(--syn-border)",
                borderRadius: 8,
                marginBottom: 10,
              }}
            >
              {/* Posture label */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: tokenConfigured ? "var(--syn-green)" : "var(--syn-text-dim)",
                    flexShrink: 0,
                    display: "inline-block",
                  }}
                />
                <span
                  data-testid="clip-token-posture"
                  style={{ fontSize: 12, fontWeight: 600, color: tokenConfigured ? "var(--syn-green)" : "var(--syn-text-dim)" }}
                >
                  {t(tokenPostureKey)}
                </span>
              </div>

              {/* Action buttons */}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  data-testid="clip-generate-token-btn"
                  onClick={() => { void handleGenerateToken(); }}
                  disabled={authBusy}
                  style={{
                    ...BTN_PRIMARY,
                    opacity: authBusy ? 0.4 : 1,
                    cursor: authBusy ? "not-allowed" : "pointer",
                  }}
                >
                  {authBusy
                    ? "…"
                    : tokenConfigured
                    ? t("settings.webClipper.rotateToken")
                    : t("settings.webClipper.generateToken")}
                </button>
                {tokenConfigured && (
                  <button
                    data-testid="clip-clear-token-btn"
                    onClick={() => { void handleClearToken(); }}
                    disabled={authBusy}
                    style={{
                      padding: "6px 14px",
                      border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                      borderRadius: 6,
                      background: "transparent",
                      color: "var(--syn-red)",
                      fontSize: 12,
                      cursor: authBusy ? "not-allowed" : "pointer",
                      fontWeight: 500,
                      opacity: authBusy ? 0.4 : 1,
                    }}
                  >
                    {t("settings.webClipper.clearToken")}
                  </button>
                )}
              </div>
            </div>

            {/* One-time token reveal — shown ONLY immediately after rotate_token */}
            {generatedToken !== null && (
              <div
                style={{
                  padding: "12px 14px",
                  background: "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)",
                  border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                  borderRadius: 8,
                  marginBottom: 10,
                }}
              >
                <p style={{ margin: "0 0 6px", fontSize: 12, fontWeight: 700, color: "var(--syn-green)" }}>
                  {t("settings.webClipper.revealWarning")}
                </p>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span
                    data-testid="clip-generated-token"
                    style={{
                      flex: 1,
                      fontFamily: "monospace",
                      fontSize: 12,
                      color: "var(--syn-text)",
                      padding: "6px 10px",
                      background: "var(--syn-bg)",
                      border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                      borderRadius: 4,
                      wordBreak: "break-all",
                      userSelect: "all",
                    }}
                  >
                    {generatedToken}
                  </span>
                  <button
                    data-testid="clip-copy-generated-token-btn"
                    onClick={handleCopyGeneratedToken}
                    style={{
                      padding: "6px 12px",
                      border: "1px solid color-mix(in srgb, var(--syn-green) 30%, var(--syn-mix-base) 70%)",
                      borderRadius: 4,
                      background: copiedGenToken ? "var(--syn-green)" : "transparent",
                      color: copiedGenToken ? "#fff" : "var(--syn-green)",
                      fontSize: 11,
                      cursor: "pointer",
                      flexShrink: 0,
                      transition: "background 0.15s, color 0.15s",
                    }}
                  >
                    {copiedGenToken ? t("settings.webClipper.copied") : t("common.copy")}
                  </button>
                </div>
                <button
                  data-testid="clip-dismiss-generated-token-btn"
                  onClick={handleDismissGeneratedToken}
                  style={{
                    marginTop: 8,
                    padding: "4px 10px",
                    border: "1px solid var(--syn-border)",
                    borderRadius: 4,
                    background: "transparent",
                    color: "var(--syn-text-muted)",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  {t("settings.webClipper.dismissToken")}
                </button>
              </div>
            )}
          </div>

          {/* ── Allowed origins field ── */}
          <div>
            <Field label={t("settings.webClipper.originsLabel")}>
              <p style={{ margin: "0 0 6px", fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
                {t("settings.webClipper.originsHelp")}
              </p>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="text"
                  data-testid="clip-origins-input"
                  value={originsInput}
                  onChange={(e) => setOriginsInput(e.target.value)}
                  placeholder={t("settings.webClipper.originsPlaceholder")}
                  style={{ ...INPUT_STYLE, flex: 1 }}
                />
                <button
                  data-testid="clip-origins-save"
                  onClick={() => { void handleSaveOrigins(); }}
                  disabled={originsBusy}
                  style={{
                    ...BTN_PRIMARY,
                    opacity: originsBusy ? 0.4 : 1,
                    cursor: originsBusy ? "not-allowed" : "pointer",
                    flexShrink: 0,
                  }}
                >
                  {originsBusy ? "…" : t("settings.webClipper.originsSave")}
                </button>
              </div>
              {/* Show the current allowed origins list read-only when non-empty */}
              {allowedOrigins.length > 0 && (
                <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {allowedOrigins.map((o) => (
                    <span
                      key={o}
                      data-testid={`clip-origin-tag-${o}`}
                      style={{
                        padding: "2px 8px",
                        borderRadius: 4,
                        background: "var(--syn-surface-hover)",
                        color: "var(--syn-text-muted)",
                        fontSize: 11,
                        fontFamily: "monospace",
                      }}
                    >
                      {o}
                    </span>
                  ))}
                </div>
              )}
            </Field>
          </div>

          {/* ── Clip endpoint URL (read-only — paste into extension Options) ── */}
          <div>
            <p style={{ margin: "0 0 6px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)" }}>
              {t("settings.webClipper.extensionUrlLabel")}
            </p>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span
                data-testid="clip-endpoint-url"
                style={{
                  flex: 1,
                  fontFamily: "monospace",
                  fontSize: 12,
                  color: "var(--syn-accent)",
                  padding: "5px 8px",
                  background: "var(--syn-accent-soft)",
                  border: "1px solid color-mix(in srgb, var(--syn-accent) 30%, var(--syn-mix-base) 70%)",
                  borderRadius: 4,
                  wordBreak: "break-all",
                }}
              >
                {clipUrl}
              </span>
              <button
                data-testid="clip-endpoint-url-copy"
                onClick={handleCopyClipUrl}
                style={{
                  padding: "5px 10px",
                  border: "1px solid var(--syn-border)",
                  borderRadius: 4,
                  background: copiedUrl ? "color-mix(in srgb, var(--syn-green) 8%, var(--syn-mix-base) 92%)" : "transparent",
                  color: copiedUrl ? "var(--syn-green)" : "var(--syn-text-muted)",
                  fontSize: 11,
                  cursor: "pointer",
                  flexShrink: 0,
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                {copiedUrl ? t("settings.webClipper.copied") : t("common.copy")}
              </button>
            </div>
            <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--syn-text-dim)", lineHeight: 1.5 }}>
              {t("settings.webClipper.extensionHint")}
            </p>
          </div>

          {/* Max body bytes (read-only — env var, not runtime-settable) */}
          {maxBodyBytes > 0 && (
            <div>
              <EmbedRow
                label={t("settings.webClipper.maxBodyLabel")}
                value={t("settings.webClipper.maxBodyBytes", { bytes: maxBodyBytes })}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Section: Output ─────────────────────────────────────────────────────────

function SectionOutput() {
  const { t, i18n } = useTranslation();
  const language = useSettingsStore(selectLanguage);
  const setLanguage = useSettingsStore(selectSetLanguage);
  const convHistory = useSettingsStore(selectConversationHistoryLength);
  const setConvHistory = useSettingsStore(selectSetConversationHistoryLength);

  const handleLanguageChange = (lang: string) => {
    setLanguage(lang);
    void i18n.changeLanguage(lang);
  };

  const turns = Math.round(convHistory / 2);

  return (
    <div>
      <SectionHeader title={t("settings.output.title")} desc={t("settings.output.desc")} />

      <Field label={t("settings.language")}>
        <div style={{ display: "flex", gap: 8 }}>
          {(["en", "it"] as const).map((lang) => (
            <button
              key={lang}
              onClick={() => handleLanguageChange(lang)}
              aria-pressed={language === lang}
              style={{
                padding: "6px 16px",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                background: language === lang ? "var(--syn-accent-soft)" : "transparent",
                color: language === lang ? "var(--syn-accent)" : "var(--syn-text-muted)",
                fontSize: 12,
                cursor: "pointer",
                fontWeight: language === lang ? 600 : 400,
              }}
            >
              {lang === "en" ? t("settings.languageEn") : t("settings.languageIt")}
            </button>
          ))}
        </div>
      </Field>

      <Field label={t("settings.output.convHistoryTitle")}>
        <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.output.convHistoryDesc")}
        </p>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {CONV_HISTORY_OPTIONS.map((n) => (
            <button
              key={n}
              onClick={() => setConvHistory(n as ConvHistoryLength)}
              aria-pressed={convHistory === n}
              style={{
                width: 40,
                height: 40,
                border: "1px solid var(--syn-border)",
                borderRadius: 8,
                background: convHistory === n ? "var(--syn-accent-soft)" : "transparent",
                color: convHistory === n ? "var(--syn-accent)" : "var(--syn-text-muted)",
                fontSize: 13,
                fontWeight: convHistory === n ? 600 : 400,
                cursor: "pointer",
                outline: convHistory === n ? `1px solid var(--syn-accent)` : "none",
              }}
            >
              {n}
            </button>
          ))}
        </div>
        <p style={{ margin: "8px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}>
          {t("settings.output.convHistoryLabel", { count: convHistory, turns })}
        </p>
      </Field>
    </div>
  );
}

// ─── Section: Interface ───────────────────────────────────────────────────────

function SectionInterface() {
  const { t } = useTranslation();
  const theme = useSettingsStore(selectTheme);
  const setTheme = useSettingsStore(selectSetTheme);

  const THEME_OPTIONS: { value: Theme; labelKey: string }[] = [
    { value: "system", labelKey: "settings.theme.system" },
    { value: "light",  labelKey: "settings.theme.light" },
    { value: "dark",   labelKey: "settings.theme.dark" },
  ];

  return (
    <div>
      <SectionHeader title={t("settings.nav.interface")} desc={t("settings.interface.desc")} />

      <Field label={t("settings.theme.label")}>
        <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.theme.help")}
        </p>
        <div style={{ display: "flex", gap: 6 }}>
          {THEME_OPTIONS.map(({ value, labelKey }) => (
            <button
              key={value}
              data-testid={`theme-btn-${value}`}
              onClick={() => setTheme(value)}
              aria-pressed={theme === value}
              style={{
                padding: "6px 14px",
                border: "1px solid var(--syn-border)",
                borderRadius: 6,
                background: theme === value ? "var(--syn-accent-soft)" : "transparent",
                color: theme === value ? "var(--syn-accent)" : "var(--syn-text-muted)",
                fontSize: 12,
                cursor: "pointer",
                fontWeight: theme === value ? 600 : 400,
              }}
            >
              {t(labelKey)}
            </button>
          ))}
        </div>
      </Field>
    </div>
  );
}

// ─── Section: Maintenance ─────────────────────────────────────────────────────

function SectionMaintenance() {
  const { t, i18n } = useTranslation();
  const reset = useSettingsStore(selectResetSettings);

  const handleReset = () => {
    if (window.confirm(t("settings.maintenance.resetConfirm"))) {
      reset();
      void i18n.changeLanguage("en");
    }
  };

  return (
    <div>
      <SectionHeader title={t("settings.nav.maintenance")} desc={t("settings.maintenance.desc")} />

      {/* Detect duplicates */}
      <div style={{ padding: 16, border: "1px solid var(--syn-border)", borderRadius: 8, background: "var(--syn-bg-soft)", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <span style={{ marginTop: 1, opacity: 0.6 }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
            </svg>
          </span>
          <div style={{ flex: 1 }}>
            <p style={{ margin: "0 0 4px", fontSize: 13, fontWeight: 600, color: "var(--syn-text)" }}>
              {t("settings.maintenance.duplicates")}
            </p>
            <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
              {t("settings.maintenance.duplicatesDesc")}
            </p>
            <button disabled style={{ ...BTN_PRIMARY, opacity: 0.4, cursor: "not-allowed" }}>
              {t("settings.maintenance.duplicatesScan")}
            </button>
            <span style={{ marginLeft: 8, fontSize: 11, color: "var(--syn-text-dim)" }}>
              {t("settings.maintenance.duplicatesComingSoon")}
            </span>
          </div>
        </div>
      </div>

      {/* Danger zone */}
      <div style={{ padding: 16, border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)", borderRadius: 8, marginBottom: 16 }}>
        <p style={{ margin: "0 0 4px", fontSize: 12, fontWeight: 600, color: "var(--syn-red)" }}>
          {t("settings.maintenance.dangerZone")}
        </p>
        <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.maintenance.resetDesc")}
        </p>
        <button
          onClick={handleReset}
          data-testid="settings-reset-btn"
          style={{
            padding: "6px 16px",
            border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
            borderRadius: 6,
            background: "transparent",
            color: "var(--syn-red)",
            fontSize: 12,
            cursor: "pointer",
          }}
        >
          {t("settings.maintenance.reset")}
        </button>
      </div>
    </div>
  );
}

// ─── Section: About ───────────────────────────────────────────────────────────

function SectionAbout() {
  const { t } = useTranslation();
  return (
    <div>
      <SectionHeader title={t("settings.nav.about")} desc="Synapse — Self-hosted LLM Wiki" />

      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 16px", fontSize: 12, marginBottom: 24 }}>
        <span style={{ color: "var(--syn-text-dim)" }}>{t("settings.about.version")}</span>
        <span style={{ color: "var(--syn-text)", fontFamily: "monospace" }}>v{__APP_VERSION__}</span>
      </div>

      <p style={{ margin: "0 0 8px", fontSize: 11, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--syn-text-dim)" }}>
        {t("settings.about.links")}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <a
          href="https://github.com/nashsu/llm_wiki"
          target="_blank"
          rel="noopener noreferrer"
          style={{ fontSize: 12, color: "var(--syn-accent)", textDecoration: "none" }}
        >
          {t("settings.about.github")} ↗
        </a>
      </div>
    </div>
  );
}

// ─── Section: Scenarios ───────────────────────────────────────────────────────
// R7-1 (FE): fetches GET /scenarios, renders up to 5 cards, Apply → ConfirmDialog → POST apply.

function SectionScenarios() {
  const { t } = useTranslation();
  const [scenarios, setScenarios] = useState<ScenarioItem[]>([]);
  const [loadErr, setLoadErr] = useState(false);
  const [loading, setLoading] = useState(true);
  const [pendingScenario, setPendingScenario] = useState<ScenarioItem | null>(null);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    fetchScenarios(ac.signal)
      .then((items) => { setScenarios(items); setLoadErr(false); setLoading(false); })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "AbortError") return;
        setLoadErr(true);
        setLoading(false);
      });
    return () => { ac.abort(); };
  }, []);

  const handleApplyConfirm = async () => {
    if (!pendingScenario) return;
    const scenario = pendingScenario;
    setPendingScenario(null);
    setApplying(true);
    try {
      await applyScenario(scenario.id);
      showToast(t("settings.scenarios.applied"), "success");
    } catch (err: unknown) {
      showToast(err instanceof Error ? err.message : t("settings.scenarios.loadError"), "error");
    } finally {
      setApplying(false);
    }
  };

  return (
    <div>
      <SectionHeader title={t("settings.scenarios.title")} desc={t("settings.scenarios.desc")} />

      {loading && (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>{t("common.loading")}</p>
      )}
      {loadErr && (
        <p style={{ fontSize: 12, color: "var(--syn-red)" }}>{t("settings.scenarios.loadError")}</p>
      )}
      {!loading && !loadErr && scenarios.length === 0 && (
        <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("settings.scenarios.loadError")}</p>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {scenarios.slice(0, 5).map((sc) => (
          <div
            key={sc.id}
            data-testid="scenario-card"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
              padding: "12px 14px",
              border: "1px solid var(--syn-border)",
              borderRadius: 8,
              background: "var(--syn-surface)",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <p style={{ margin: "0 0 4px", fontSize: 13, fontWeight: 600, color: "var(--syn-text)" }}>
                {sc.name}
              </p>
              <p style={{ margin: 0, fontSize: 11, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>
                {sc.description}
              </p>
            </div>
            <button
              data-testid="scenario-apply-btn"
              style={{
                ...BTN_PRIMARY,
                flexShrink: 0,
                opacity: applying ? 0.5 : 1,
                cursor: applying ? "not-allowed" : "pointer",
              }}
              disabled={applying}
              onClick={() => setPendingScenario(sc)}
            >
              {applying ? t("settings.scenarios.applying") : t("settings.scenarios.apply")}
            </button>
          </div>
        ))}
      </div>

      {pendingScenario && (
        <ConfirmDialog
          title={t("settings.scenarios.applyConfirmTitle")}
          body={t("settings.scenarios.applyConfirmBody", { name: pendingScenario.name })}
          confirmLabel={t("settings.scenarios.applyConfirm")}
          cancelLabel={t("settings.scenarios.applyCancel")}
          onConfirm={() => { void handleApplyConfirm(); }}
          onCancel={() => setPendingScenario(null)}
        />
      )}
    </div>
  );
}

// ─── Section: Costs (R9-1) ────────────────────────────────────────────────────
// I3: single fetch on mount + manual Refresh button; no background polling.
// No Zustand store — local state only (I3).

function SectionCosts() {
  const { t } = useTranslation();
  const [data, setData] = useState<CostsSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(false);

  // Month selector — "YYYY-MM" string; null = current month
  const [month, setMonth] = useState<string>(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  });

  const load = useCallback(async (selectedMonth: string) => {
    setLoading(true);
    setErr(false);
    try {
      const result = await fetchCostsSummary(selectedMonth);
      setData(result);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === "AbortError") return;
      setErr(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(month);
  }, [load, month]);

  const handleMonthChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setMonth(e.target.value);
  };

  // SVG bar chart for by_day (pure CSS/SVG, no chart lib — I9)
  const renderDayBars = (days: CostsSummary["by_day"]) => {
    if (days.length === 0) return null;
    const max = Math.max(...days.map((d) => d.total_usd), 0.0001);
    const BAR_W = 6;
    const GAP = 2;
    const H = 36;
    const totalW = days.length * (BAR_W + GAP);

    return (
      <svg
        width={totalW}
        height={H + 16}
        data-testid="costs-day-chart"
        aria-label={t("settings.costs.byDay")}
        role="img"
        style={{ display: "block", overflow: "visible" }}
      >
        {days.map((d, i) => {
          const barH = Math.max(2, Math.round((d.total_usd / max) * H));
          const x = i * (BAR_W + GAP);
          const y = H - barH;
          return (
            <g key={d.date}>
              <title>{`${d.date}: $${d.total_usd.toFixed(4)}`}</title>
              <rect
                x={x}
                y={y}
                width={BAR_W}
                height={barH}
                fill="var(--syn-accent)"
                opacity={0.8}
                rx={1}
              />
            </g>
          );
        })}
      </svg>
    );
  };

  return (
    <div>
      <SectionHeader title={t("settings.costs.title")} desc={t("settings.costs.desc")} />

      {/* Month selector + Refresh */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20 }}>
        <label style={{ fontSize: 12, color: "var(--syn-text-muted)", flexShrink: 0 }}>
          {t("settings.costs.period")}
        </label>
        <input
          type="month"
          data-testid="costs-month-selector"
          value={month}
          onChange={handleMonthChange}
          style={{ ...INPUT_STYLE, width: 160 }}
        />
        <button
          data-testid="costs-refresh-btn"
          onClick={() => { void load(month); }}
          disabled={loading}
          style={{ ...BTN_PRIMARY, opacity: loading ? 0.4 : 1, cursor: loading ? "not-allowed" : "pointer" }}
        >
          {loading ? "…" : t("settings.costs.refresh")}
        </button>
      </div>

      {err && (
        <p style={{ fontSize: 12, color: "var(--syn-red)", margin: "8px 0" }}>{t("settings.costs.error")}</p>
      )}
      {loading && !data && (
        <p style={{ fontSize: 12, color: "var(--syn-text-muted)", margin: "8px 0" }}>{t("settings.costs.loading")}</p>
      )}

      {data !== null && (
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>

          {/* Monthly total + threshold alert */}
          <div>
            {data.threshold_alert && (
              <div
                data-testid="costs-threshold-alert"
                style={{
                  marginBottom: 12,
                  padding: "8px 12px",
                  background: "color-mix(in srgb, var(--syn-red) 8%, var(--syn-mix-base) 92%)",
                  border: "1px solid color-mix(in srgb, var(--syn-red) 30%, transparent 70%)",
                  borderRadius: 6,
                  fontSize: 12,
                  color: "var(--syn-red)",
                  fontWeight: 600,
                }}
                role="alert"
              >
                {t("settings.costs.thresholdAlert", { threshold: data.threshold_usd.toFixed(2) })}
              </div>
            )}
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span style={{ fontSize: 28, fontWeight: 700, color: "var(--syn-text)", fontFamily: "monospace" }} data-testid="costs-monthly-total">
                ${data.monthly_total_usd.toFixed(4)}
              </span>
              <span style={{ fontSize: 12, color: "var(--syn-text-muted)" }}>
                {t("settings.costs.monthlyTotal")}
              </span>
            </div>
          </div>

          {/* Daily bar chart */}
          {data.by_day.length > 0 && (
            <div>
              <p style={{ margin: "0 0 8px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {t("settings.costs.byDay")}
              </p>
              <div style={{ overflowX: "auto" }}>
                {renderDayBars(data.by_day)}
              </div>
            </div>
          )}

          {/* By provider */}
          {data.by_provider.length > 0 && (
            <div>
              <p style={{ margin: "0 0 8px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {t("settings.costs.byProvider")}
              </p>
              {data.by_provider_note && (
                <p style={{ fontSize: 11, color: "var(--syn-text-dim)", margin: "0 0 8px", lineHeight: 1.5 }}>
                  {data.by_provider_note}
                </p>
              )}
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }} data-testid="costs-by-provider">
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--syn-border)" }}>
                    <th style={{ padding: "4px 0", textAlign: "left", color: "var(--syn-text-muted)", fontWeight: 600 }}>{t("settings.costs.providerCol")}</th>
                    <th style={{ padding: "4px 0", textAlign: "right", color: "var(--syn-text-muted)", fontWeight: 600 }}>{t("settings.costs.totalUsd")}</th>
                    <th style={{ padding: "4px 0", textAlign: "right", color: "var(--syn-text-muted)", fontWeight: 600 }}>{t("settings.costs.callCount")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_provider.map((row) => (
                    <tr key={row.provider} style={{ borderBottom: "1px solid var(--syn-border)" }}>
                      <td style={{ padding: "6px 0", color: "var(--syn-text)", fontFamily: "monospace" }}>{row.provider}</td>
                      <td style={{ padding: "6px 0", textAlign: "right", color: "var(--syn-text)", fontFamily: "monospace" }}>${row.total_usd.toFixed(4)}</td>
                      <td style={{ padding: "6px 0", textAlign: "right", color: "var(--syn-text-muted)" }}>{row.call_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* By operation */}
          {data.by_operation.length > 0 && (
            <div>
              <p style={{ margin: "0 0 8px", fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {t("settings.costs.byOperation")}
              </p>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }} data-testid="costs-by-operation">
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--syn-border)" }}>
                    <th style={{ padding: "4px 0", textAlign: "left", color: "var(--syn-text-muted)", fontWeight: 600 }}>{t("settings.costs.operationCol")}</th>
                    <th style={{ padding: "4px 0", textAlign: "right", color: "var(--syn-text-muted)", fontWeight: 600 }}>{t("settings.costs.totalUsd")}</th>
                    <th style={{ padding: "4px 0", textAlign: "right", color: "var(--syn-text-muted)", fontWeight: 600 }}>{t("settings.costs.callCount")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_operation.map((row) => (
                    <tr key={row.operation} style={{ borderBottom: "1px solid var(--syn-border)" }}>
                      <td style={{ padding: "6px 0", color: "var(--syn-text)", fontFamily: "monospace" }}>{row.operation}</td>
                      <td style={{ padding: "6px 0", textAlign: "right", color: "var(--syn-text)", fontFamily: "monospace" }}>${row.total_usd.toFixed(4)}</td>
                      <td style={{ padding: "6px 0", textAlign: "right", color: "var(--syn-text-muted)" }}>{row.call_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* No data */}
          {data.by_provider.length === 0 && data.by_operation.length === 0 && (
            <p style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>{t("settings.costs.noData")}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function SectionHeader({ title, desc }: { title: string; desc: string }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <h2 style={{ margin: "0 0 6px", fontSize: 16, fontWeight: 700, color: "var(--syn-text)" }}>{title}</h2>
      <p style={{ margin: 0, fontSize: 12, color: "var(--syn-text-muted)", lineHeight: 1.5 }}>{desc}</p>
    </div>
  );
}

function Field({ label, children, compact }: { label: string; children: ReactNode; compact?: boolean }) {
  return (
    <div style={{ marginBottom: compact ? 10 : 20 }}>
      <label style={{ display: "block", marginBottom: 6, fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)" }}>
        {label}
      </label>
      {children}
    </div>
  );
}

function BudgetRow({ label, pct, tokens }: { label: string; pct: number; tokens: number }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "120px 32px 1fr 60px",
        gap: 8,
        alignItems: "center",
        marginBottom: 4,
      }}
    >
      <span style={{ fontSize: 11, color: "var(--syn-text-muted)" }}>{label}</span>
      <span style={{ fontSize: 11, color: "var(--syn-text-muted)", fontFamily: "monospace" }}>{pct}%</span>
      <div style={{ height: 4, background: "var(--syn-border)", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: "var(--syn-accent)", borderRadius: 2 }} />
      </div>
      <span style={{ fontSize: 11, color: "var(--syn-text-muted)", fontFamily: "monospace", textAlign: "right" }}>
        {formatTokenCount(tokens)}
      </span>
    </div>
  );
}

// ─── Style constants ──────────────────────────────────────────────────────────

const INPUT_STYLE: React.CSSProperties = {
  width: "100%",
  padding: "6px 10px",
  background: "var(--syn-bg)",
  border: "1px solid var(--syn-border)",
  borderRadius: 6,
  color: "var(--syn-text)",
  fontSize: 12,
  cursor: "pointer",
  boxSizing: "border-box",
};

const BTN_PRIMARY: React.CSSProperties = {
  padding: "6px 14px",
  border: "1px solid var(--syn-accent)",
  borderRadius: 6,
  background: "var(--syn-accent-soft)",
  color: "var(--syn-accent)",
  fontSize: 12,
  cursor: "pointer",
  fontWeight: 500,
};

const BTN_SECONDARY: React.CSSProperties = {
  padding: "6px 14px",
  border: "1px solid var(--syn-border)",
  borderRadius: 6,
  background: "transparent",
  color: "var(--syn-text-muted)",
  fontSize: 12,
  cursor: "pointer",
};

// ─── Section: Security (ADR-0052 §4.6) ───────────────────────────────────────

/**
 * SectionSecurity — client-side token management (ADR-0052 §4.6).
 *
 * Shows:
 *  (a) Current server URL (read-only, for context).
 *  (b) "Rotate token" field — paste a new token, click Update. No server call.
 *  (c) Clear button — removes token from localStorage.
 *  (d) Asymmetry banner: server-side rotation = env change + restart; this UI only
 *      updates the client copy (ADR-0052 §2.6). Must be explicit per AC-R10-2-5.
 *
 * The Authorization header is NEVER constructed here — only setAuthToken / clearAuthToken
 * are called (base.ts keeps the single injection point, ADR-0052 Do-NOT §10).
 */
function SectionSecurity() {
  const { t } = useTranslation();
  const [newToken, setNewToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [saved, setSaved] = useState(false);
  const [cleared, setCleared] = useState(false);

  const currentServerUrl = apiBase() || window.location.origin;
  const hasToken = getAuthToken() !== null;

  const handleUpdate = useCallback(() => {
    const trimmed = newToken.trim();
    if (!trimmed) return;
    setAuthToken(trimmed);
    setNewToken("");
    setSaved(true);
    setCleared(false);
    setTimeout(() => setSaved(false), 2500);
  }, [newToken]);

  const handleClear = useCallback(() => {
    clearAuthToken();
    setCleared(true);
    setSaved(false);
    setTimeout(() => setCleared(false), 2500);
  }, []);

  return (
    <div>
      <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4, color: "var(--syn-text)" }}>
        {t("settings.security.title")}
      </h2>
      <p style={{ fontSize: 13, color: "var(--syn-text-dim)", marginBottom: 24, lineHeight: 1.6 }}>
        {t("settings.security.desc")}
      </p>

      {/* Server URL (read-only) */}
      <div style={{ marginBottom: 20 }}>
        <label style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", display: "block", marginBottom: 4 }}>
          {t("settings.security.serverUrl")}
        </label>
        <div
          style={{
            fontSize: 12,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            color: "var(--syn-text-dim)",
            background: "var(--syn-bg-soft)",
            border: "1px solid var(--syn-border)",
            borderRadius: 6,
            padding: "8px 12px",
          }}
        >
          {currentServerUrl}
        </div>
        <p style={{ fontSize: 11, color: "var(--syn-text-dim)", marginTop: 4 }}>
          {hasToken ? t("settings.security.tokenPresent") : t("settings.security.tokenAbsent")}
        </p>
      </div>

      {/* Rotate token field */}
      <div style={{ marginBottom: 12 }}>
        <label
          htmlFor="settings-security-token"
          style={{ fontSize: 12, fontWeight: 600, color: "var(--syn-text-muted)", display: "block", marginBottom: 4 }}
        >
          {t("settings.security.rotateLabel")}
        </label>
        <div style={{ position: "relative", marginBottom: 8 }}>
          <input
            id="settings-security-token"
            type={showToken ? "text" : "password"}
            value={newToken}
            onChange={(e) => setNewToken(e.target.value)}
            placeholder={t("settings.security.rotatePlaceholder")}
            autoComplete="new-password"
            style={{
              ...INPUT_STYLE,
              paddingRight: 40,
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              fontSize: 12,
            }}
          />
          <button
            type="button"
            onClick={() => setShowToken((v) => !v)}
            aria-label={showToken ? t("connect.hideToken") : t("connect.showToken")}
            style={{
              position: "absolute",
              right: 10,
              top: "50%",
              transform: "translateY(-50%)",
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: 2,
              color: "var(--syn-text-dim)",
              display: "flex",
              alignItems: "center",
            }}
          >
            {showToken ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                <line x1="1" y1="1" x2="23" y2="23"/>
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
            )}
          </button>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            type="button"
            onClick={handleUpdate}
            disabled={newToken.trim().length === 0}
            style={{ ...BTN_PRIMARY, opacity: newToken.trim().length === 0 ? 0.5 : 1 }}
            data-testid="security-update-btn"
          >
            {t("settings.security.update")}
          </button>
          <button
            type="button"
            onClick={handleClear}
            style={BTN_SECONDARY}
            data-testid="security-clear-btn"
          >
            {t("settings.security.clear")}
          </button>
          {saved && (
            <span style={{ fontSize: 12, color: "var(--syn-green)" }}>
              {t("settings.security.saved")}
            </span>
          )}
          {cleared && (
            <span style={{ fontSize: 12, color: "var(--syn-text-dim)" }}>
              {t("settings.security.cleared")}
            </span>
          )}
        </div>
      </div>

      {/* Asymmetry banner (ADR-0052 §2.6 / AC-R10-2-5) */}
      <div
        style={{
          marginTop: 24,
          padding: "12px 14px",
          background: "var(--syn-notice-info-bg, #eff6ff)",
          border: "1px solid var(--syn-notice-info-border, #bfdbfe)",
          borderRadius: 8,
          fontSize: 12,
          color: "var(--syn-text)",
          lineHeight: 1.6,
        }}
      >
        <p style={{ fontWeight: 600, marginBottom: 4 }}>{t("settings.security.asymmetryTitle")}</p>
        <p style={{ margin: 0, color: "var(--syn-text-dim)" }}>{t("settings.security.asymmetryNote")}</p>
      </div>
    </div>
  );
}
