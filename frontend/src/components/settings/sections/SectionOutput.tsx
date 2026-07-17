/**
 * SectionOutput.tsx — language + conversation history (F16).
 * Extracted from SettingsPanel monolith (ADR-0055).
 *
 * Changes write to the DRAFT layer; the SettingsSaveFooter commits on Save (F16).
 * i18n.changeLanguage is called by SettingsSaveFooter at commit time, not here.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SectionHeader, Field } from "../ui";
import {
  useSettingsStore,
  selectDraftLanguage,
  selectDraftConversationHistoryLength,
  selectSetDraftLanguage,
  selectSetDraftConversationHistoryLength,
  CONV_HISTORY_OPTIONS,
  type ConvHistoryLength,
} from "../../../store/settingsStore";
import { selectVaultId, useAppStore } from "../../../store/appStore";
import { fetchVaultOutputLanguage, setVaultOutputLanguage } from "../../../api/vaultMetaClient";

// The vault's AI OUTPUT language (F3/ADR-0081) — the language the model writes pages, queries and
// the overview in. Distinct from the interface language below. "" = auto (detect per source).
const AI_LANGUAGE_OPTIONS: { value: string; label: string }[] = [
  { value: "en", label: "English" },
  { value: "it", label: "Italiano" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "pt", label: "Português" },
  { value: "zh", label: "中文" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "ru", label: "Русский" },
];

export function SectionOutput() {
  const { t } = useTranslation();
  const draftLanguage = useSettingsStore(selectDraftLanguage);
  const setDraftLanguage = useSettingsStore(selectSetDraftLanguage);
  const draftConvHistory = useSettingsStore(selectDraftConversationHistoryLength);
  const setDraftConvHistory = useSettingsStore(selectSetDraftConversationHistoryLength);
  const vaultId = useAppStore(selectVaultId);

  // Per-vault AI output language — saved immediately (not via the draft/Save footer) since it is a
  // vault-scoped setting, not an app preference.
  const [aiLang, setAiLang] = useState<string>("");
  const [aiState, setAiState] = useState<"idle" | "saving" | "saved" | "error">("idle");

  useEffect(() => {
    let cancelled = false;
    void fetchVaultOutputLanguage()
      .then((lang) => {
        if (!cancelled) setAiLang(lang ?? "");
      })
      .catch(() => {
        /* leave as auto */
      });
    return () => {
      cancelled = true;
    };
  }, [vaultId]);

  const onAiLangChange = (next: string) => {
    const prev = aiLang;
    setAiLang(next);
    setAiState("saving");
    void setVaultOutputLanguage(next)
      .then(() => {
        setAiState("saved");
        window.setTimeout(() => setAiState("idle"), 2000);
      })
      .catch(() => {
        setAiLang(prev); // revert on failure
        setAiState("error");
      });
  };

  const turns = Math.round(draftConvHistory / 2);

  return (
    <div>
      <SectionHeader title={t("settings.output.title")} desc={t("settings.output.desc")} />

      <Field label={t("settings.output.aiLanguageTitle")}>
        <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.output.aiLanguageDesc")}
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <select
            data-testid="settings-ai-output-language"
            value={aiLang}
            onChange={(e) => onAiLangChange(e.target.value)}
            style={{
              padding: "7px 10px",
              border: "1px solid var(--syn-border)",
              borderRadius: 8,
              background: "var(--syn-surface)",
              color: "var(--syn-text)",
              fontSize: 12.5,
              cursor: "pointer",
              minWidth: 160,
            }}
          >
            <option value="">{t("settings.output.aiLanguageAuto")}</option>
            {AI_LANGUAGE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {aiState === "saving" && (
            <span style={{ fontSize: 11, color: "var(--syn-text-dim)" }}>…</span>
          )}
          {aiState === "saved" && (
            <span style={{ fontSize: 11, color: "var(--syn-success)" }}>
              {t("settings.output.aiLanguageSaved")}
            </span>
          )}
          {aiState === "error" && (
            <span style={{ fontSize: 11, color: "var(--syn-danger)" }}>{t("common.error")}</span>
          )}
        </div>
      </Field>

      <Field label={t("settings.language")}>
        <div style={{ display: "flex", gap: 6 }}>
          {(["en", "it"] as const).map((lang) => {
            const on = draftLanguage === lang;
            return (
              <button
                key={lang}
                onClick={() => setDraftLanguage(lang)}
                aria-pressed={on}
                style={{
                  padding: "7px 16px",
                  border: `1px solid ${on ? "var(--syn-accent)" : "var(--syn-border)"}`,
                  borderRadius: 8,
                  background: on ? "var(--syn-accent)" : "var(--syn-surface)",
                  color: on ? "#fff" : "var(--syn-text-muted)",
                  fontSize: 12.5,
                  cursor: "pointer",
                  fontWeight: on ? 600 : 500,
                  transition: "background 0.12s, color 0.12s",
                }}
              >
                {lang === "en" ? t("settings.languageEn") : t("settings.languageIt")}
              </button>
            );
          })}
        </div>
      </Field>

      <Field label={t("settings.output.convHistoryTitle")}>
        <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--syn-text-muted)" }}>
          {t("settings.output.convHistoryDesc")}
        </p>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {CONV_HISTORY_OPTIONS.map((n) => {
            const on = draftConvHistory === n;
            return (
              <button
                key={n}
                onClick={() => setDraftConvHistory(n as ConvHistoryLength)}
                aria-pressed={on}
                style={{
                  width: 40,
                  height: 40,
                  border: `1px solid ${on ? "var(--syn-accent)" : "var(--syn-border)"}`,
                  borderRadius: 8,
                  background: on ? "var(--syn-accent)" : "var(--syn-surface)",
                  color: on ? "#fff" : "var(--syn-text-muted)",
                  fontSize: 13,
                  fontWeight: on ? 600 : 500,
                  cursor: "pointer",
                  transition: "background 0.12s, color 0.12s",
                }}
              >
                {n}
              </button>
            );
          })}
        </div>
        <p style={{ margin: "8px 0 0", fontSize: 11, color: "var(--syn-text-dim)" }}>
          {t("settings.output.convHistoryLabel", { count: draftConvHistory, turns })}
        </p>
      </Field>
    </div>
  );
}
