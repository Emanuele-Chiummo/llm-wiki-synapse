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
import { useShallow } from "zustand/react/shallow";
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

type SettingsSection =
  | "general"
  | "llmModels"
  | "embeddings"
  | "sourceWatch"
  | "webSearch"
  | "apiMcp"
  | "webClipper"
  | "output"
  | "interface"
  | "maintenance"
  | "about"
  | "scenarios"
  | "costs";

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

function IconDatabase() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <ellipse cx="12" cy="5" rx="9" ry="3"/>
      <path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/>
      <path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/>
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

function IconServer() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect width="20" height="8" x="2" y="2" rx="2" ry="2"/>
      <rect width="20" height="8" x="2" y="14" rx="2" ry="2"/>
      <line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/>
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

function IconMonitor() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect width="20" height="14" x="2" y="3" rx="2"/>
      <line x1="8" x2="16" y1="21" y2="21"/>
      <line x1="12" x2="12" y1="17" y2="21"/>
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

function IconInfo() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10"/>
      <path d="M12 16v-4"/><path d="M12 8h.01"/>
    </svg>
  );
}

function IconClip() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
    </svg>
  );
}

function IconSearch() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8"/>
      <path d="m21 21-4.35-4.35"/>
    </svg>
  );
}

function IconScenarios() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
    </svg>
  );
}

function IconWallet() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 12V22H4V12"/>
      <path d="M22 7H2v5h20V7z"/>
      <path d="M12 22V7"/>
      <path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/>
      <path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/>
    </svg>
  );
}

const NAV_ITEMS: NavItem[] = [
  { id: "general",     labelKey: "settings.nav.general",     icon: <IconSliders /> },
  { id: "llmModels",   labelKey: "settings.nav.llmModels",   icon: <IconCpu /> },
  { id: "embeddings",  labelKey: "settings.nav.embeddings",  icon: <IconDatabase /> },
  { id: "sourceWatch", labelKey: "settings.nav.sourceWatch", icon: <IconFolder /> },
  { id: "webSearch",   labelKey: "settings.nav.webSearch",   icon: <IconSearch /> },
  { id: "apiMcp",      labelKey: "settings.nav.apiMcp",      icon: <IconServer /> },
  { id: "webClipper",  labelKey: "settings.nav.webClipper",  icon: <IconClip /> },
  { id: "output",      labelKey: "settings.nav.output",      icon: <IconType /> },
  { id: "interface",   labelKey: "settings.nav.interface",   icon: <IconMonitor /> },
  { id: "costs",       labelKey: "settings.nav.costs",       icon: <IconWallet /> },
  { id: "scenarios",   labelKey: "settings.nav.scenarios",   icon: <IconScenarios /> },
  { id: "maintenance", labelKey: "settings.nav.maintenance", icon: <IconWrench /> },
  { id: "about",       labelKey: "settings.nav.about",       icon: <IconInfo /> },
];

// ─── Component ────────────────────────────────────────────────────────────────

export function SettingsPanel() {
  const [activeSection, setActiveSection] = useState<SettingsSection>("general");
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

      {/* Content area */}
      <div style={{ flex: 1, overflowY: "auto", padding: "32px 40px", maxWidth: 680 }}>
        {activeSection === "general" && <SectionGeneral />}
        {activeSection === "llmModels" && <SectionLlmModels />}
        {activeSection === "embeddings" && <SectionEmbeddings />}
        {activeSection === "sourceWatch" && <SectionSourceWatch />}
        {activeSection === "webSearch" && <SectionWebSearch />}
        {activeSection === "apiMcp" && <SectionApiMcp />}
        {activeSection === "webClipper" && <SectionWebClipper />}
        {activeSection === "output" && <SectionOutput />}
        {activeSection === "interface" && <SectionInterface />}
        {activeSection === "costs" && <SectionCosts />}
        {activeSection === "maintenance" && <SectionMaintenance />}
        {activeSection === "about" && <SectionAbout />}
        {activeSection === "scenarios" && <SectionScenarios />}
      </div>
    </div>
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
