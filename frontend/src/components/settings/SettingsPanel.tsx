/**
 * SettingsPanel.tsx — F14 + F16 settings surface (ADR-0018 §5).
 *
 * Sections:
 *   1. Context window (4K–1M select) + 60/20/5/15 budget split display (F14)
 *   2. Language toggle (EN / IT) (F16)
 *   3. Provider list (read-only — editing done via ProviderSelector in the Header)
 *   4. Reset button — clears localStorage for all three stores
 *
 * INVARIANT I3: subscribes to settingsStore + providerStore only via typed selectors.
 * INVARIANT I6: no hardcoded model/provider IDs in rendered values.
 */

import { useEffect, type ReactNode } from "react";
import { ImportScheduleCard } from "./ImportScheduleCard";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useSettingsStore,
  selectContextWindow,
  selectLanguage,
  selectSetContextWindow,
  selectSetLanguage,
  selectResetSettings,
  CONTEXT_WINDOW_OPTIONS,
  computeBudgetSplit,
  formatTokenCount,
} from "../../store/settingsStore";
import {
  useProviderStore,
  selectProviderList,
  selectProviderLoading,
  selectFetchProviderList,
} from "../../store/providerStore";
import { useGraphStore, selectVaultId } from "../../store/graphStore";

// ─── Component ───────────────────────────────────────────────────────────────

export function SettingsPanel() {
  const { t, i18n } = useTranslation();
  const contextWindow = useSettingsStore(selectContextWindow);
  const language = useSettingsStore(selectLanguage);
  const setContextWindow = useSettingsStore(selectSetContextWindow);
  const setLanguage = useSettingsStore(selectSetLanguage);
  const reset = useSettingsStore(selectResetSettings);

  const providerList = useProviderStore(useShallow(selectProviderList));
  const providerLoading = useProviderStore(selectProviderLoading);
  const fetchProviders = useProviderStore(selectFetchProviderList);
  const vaultId = useGraphStore(selectVaultId);

  const budget = computeBudgetSplit(contextWindow);

  // Fetch providers if empty
  useEffect(() => {
    if (providerList.length === 0 && !providerLoading) {
      void fetchProviders();
    }
  }, [providerList.length, providerLoading, fetchProviders]);

  // Sync i18next language with settingsStore (I3: one-way, settings is source of truth)
  useEffect(() => {
    if (i18n.language !== language) {
      void i18n.changeLanguage(language);
    }
  }, [language, i18n]);

  const handleLanguageChange = (lang: string) => {
    setLanguage(lang);
    void i18n.changeLanguage(lang);
  };

  const handleReset = () => {
    if (window.confirm(t("settings.resetConfirm"))) {
      reset();
      void i18n.changeLanguage("en");
    }
  };

  return (
    <div
      data-testid="settings-panel"
      style={{
        maxWidth: 600,
        margin: "0 auto",
        padding: "32px 24px",
        color: "#e6edf3",
        fontSize: 13,
      }}
    >
      <h2 style={{ margin: "0 0 24px", fontSize: 16, fontWeight: 700, color: "#e6edf3" }}>
        {t("settings.title")}
      </h2>

      {/* ── Section 1: Context window ─────────────────────────────────────── */}
      <Section title={t("settings.contextWindow")}>
        <p style={{ margin: "0 0 12px", fontSize: 12, color: "#6e7681" }}>
          {t("settings.contextWindowHelp")}
        </p>

        <label htmlFor="ctx-select" style={{ display: "block", marginBottom: 6, fontSize: 12, color: "#8b949e" }}>
          {t("settings.contextWindow")}
        </label>
        <select
          id="ctx-select"
          value={contextWindow}
          onChange={(e) => setContextWindow(Number(e.target.value) as typeof contextWindow)}
          style={{
            padding: "6px 10px",
            background: "#161b22",
            border: "1px solid #21262d",
            borderRadius: 6,
            color: "#e6edf3",
            fontSize: 12,
            cursor: "pointer",
          }}
        >
          {CONTEXT_WINDOW_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {formatTokenCount(opt)}
            </option>
          ))}
        </select>

        {/* Budget split display */}
        <div style={{ marginTop: 16 }}>
          <p style={{ margin: "0 0 8px", fontSize: 12, fontWeight: 600, color: "#8b949e" }}>
            {t("settings.budgetSplit")}
          </p>
          <BudgetRow label={t("settings.budgetHistory")} pct={60} tokens={budget.history} />
          <BudgetRow label={t("settings.budgetRetrieved")} pct={20} tokens={budget.retrieved} />
          <BudgetRow label={t("settings.budgetSystem")} pct={5} tokens={budget.system} />
          <BudgetRow label={t("settings.budgetGeneration")} pct={15} tokens={budget.generation} />
        </div>
      </Section>

      {/* ── Section 2: Language ───────────────────────────────────────────── */}
      <Section title={t("settings.language")}>
        <div style={{ display: "flex", gap: 8 }}>
          {(["en", "it"] as const).map((lang) => (
            <button
              key={lang}
              onClick={() => handleLanguageChange(lang)}
              aria-pressed={language === lang}
              style={{
                padding: "6px 16px",
                border: "1px solid #21262d",
                borderRadius: 6,
                background: language === lang ? "#1f6feb22" : "transparent",
                color: language === lang ? "#58a6ff" : "#6e7681",
                fontSize: 12,
                cursor: "pointer",
                fontWeight: language === lang ? 600 : 400,
              }}
            >
              {lang === "en" ? t("settings.languageEn") : t("settings.languageIt")}
            </button>
          ))}
        </div>
      </Section>

      {/* ── Section 3: Provider list (read-only) ─────────────────────────── */}
      <Section title={t("settings.providerSection")}>
        {providerLoading && (
          <p style={{ margin: 0, fontSize: 12, color: "#484f58" }}>{t("common.loading")}</p>
        )}
        {!providerLoading && providerList.length === 0 && (
          <p style={{ margin: 0, fontSize: 12, color: "#484f58" }}>{t("provider.noProviders")}</p>
        )}
        {providerList.map((item) => (
          <div
            key={item.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 0",
              borderBottom: "1px solid #21262d",
              fontSize: 12,
            }}
          >
            <span style={{ flex: 1, color: "#e6edf3" }}>
              {t(`provider.type.${item.provider_type}` as string) || item.provider_type}
              {item.model_id && (
                <span style={{ marginLeft: 6, color: "#6e7681", fontFamily: "monospace", fontSize: 11 }}>
                  {item.model_id}
                </span>
              )}
            </span>
            <span
              style={{
                padding: "1px 6px",
                borderRadius: 4,
                background: "#21262d",
                color: "#8b949e",
                fontSize: 10,
              }}
            >
              {t(`provider.scope.${item.scope}`)}
            </span>
            {item.vault_id && item.scope === "vault" && (
              <span
                style={{ fontSize: 10, color: "#484f58", fontFamily: "monospace" }}
                title={`vault: ${item.vault_id}`}
              >
                {item.vault_id === vaultId ? t("common.vault") : item.vault_id.slice(0, 8)}
              </span>
            )}
          </div>
        ))}
      </Section>

      {/* ── Section 4: Scheduled folder import (ADR-0020 Feature S) ──────── */}
      <Section title={t("settings.import.title")}>
        <ImportScheduleCard />
      </Section>

      {/* ── Section 5: Reset ──────────────────────────────────────────────── */}
      <div style={{ marginTop: 32 }}>
        <button
          onClick={handleReset}
          data-testid="settings-reset-btn"
          style={{
            padding: "6px 16px",
            border: "1px solid #f8514933",
            borderRadius: 6,
            background: "transparent",
            color: "#f85149",
            fontSize: 12,
            cursor: "pointer",
          }}
        >
          {t("settings.reset")}
        </button>
      </div>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ marginBottom: 28 }}>
      <h3
        style={{
          margin: "0 0 12px",
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "#484f58",
          borderBottom: "1px solid #21262d",
          paddingBottom: 6,
        }}
      >
        {title}
      </h3>
      {children}
    </section>
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
      <span style={{ fontSize: 11, color: "#8b949e" }}>{label}</span>
      <span style={{ fontSize: 11, color: "#6e7681", fontFamily: "monospace" }}>{pct}%</span>
      <div style={{ height: 4, background: "#21262d", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: "#1f6feb", borderRadius: 2 }} />
      </div>
      <span style={{ fontSize: 11, color: "#6e7681", fontFamily: "monospace", textAlign: "right" }}>
        {formatTokenCount(tokens)}
      </span>
    </div>
  );
}
